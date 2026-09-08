"""
AI Change Management API (ASSURANCE plane).

    POST /v1/changes                       create a change record (candidate)
    GET  /v1/changes                       list (system/state/type filters)
    GET  /v1/changes/{id}                  full record (snapshots + evidence)
    POST /v1/changes/{id}/replay           replay the target system against the
                                           candidate; attach report; -> replay
    POST /v1/changes/{id}/evaluate         attach governance evaluation; -> evaluation
    POST /v1/changes/{id}/risk-comparison  derive the risk comparison; -> risk_comparison
    POST /v1/changes/{id}/submit-review    create the bound approval; -> human_review
    POST /v1/changes/{id}/approve          validate bound approval; -> approved
    POST /v1/changes/{id}/reject           -> rejected (reason required)
    POST /v1/changes/{id}/deploy           apply candidate; -> deployed
    POST /v1/changes/{id}/rollback         re-apply previous; -> rolled_back

The lifecycle is enforced server-side: you cannot skip replay/evaluation/
review, cannot deploy without an approval bound to the exact change content,
and cannot deploy or roll back invisibly — every transition is recorded in
the change history and as a governance event.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.governance import changes as change_service
from helios.governance.evaluators import evaluate_system
from helios.governance.replay import replay_system
from helios.models import ApiKey, ChangeRecord
from helios.security import get_api_key

router = APIRouter(prefix="/v1/changes", tags=["changes"])


# --- schemas ---------------------------------------------------------------


class ChangeIn(BaseModel):
    change_type: str = Field(pattern="^(model|model_version|prompt|policy|tool|"
                                     "dataset|system_instruction|workflow|agent_config)$")
    title: str = Field(default="", max_length=255)
    description: str | None = None
    author: str = Field(min_length=1, max_length=255)
    target: dict
    candidate: dict


class ByIn(BaseModel):
    by: str = Field(min_length=1, max_length=255)
    note: str | None = None


class ApproveIn(BaseModel):
    by: str = Field(min_length=1, max_length=255)
    approval_id: str


class RejectIn(BaseModel):
    by: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1)


class ReplayChangeIn(BaseModel):
    by: str = Field(min_length=1, max_length=255)
    run_limit: int = Field(default=25, ge=1, le=200)


# --- helpers ---------------------------------------------------------------


def _change_or_404(db: Session, api_key: ApiKey, change_id: str) -> ChangeRecord:
    change = (
        db.query(ChangeRecord)
        .filter(ChangeRecord.id == change_id,
                ChangeRecord.tenant_id == api_key.tenant_id)
        .first()
    )
    if change is None:
        raise HTTPException(status_code=404, detail="change record not found")
    return change


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except change_service.ChangeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


def _candidates_for(change: ChangeRecord) -> dict:
    """Map a change record's candidate onto replay inputs by family."""
    family = (change.target or {}).get("family")
    candidate = dict(change.candidate or {})
    if family == "policy_set":
        governance = {
            "name": change.target.get("name") or candidate.get("name"),
            "version": candidate.get("version"),
            "rules": candidate.get("rules") or [],
            "description": candidate.get("description", ""),
            "default_effect": candidate.get("default_effect", "allow"),
            "scope": candidate.get("scope") or {},
        }
        return {
            "candidate_governance_set": governance,
            "candidate_tool_policy": candidate.get("tool_policy"),
        }
    if family == "system":
        return {"candidate_system": {
            k: candidate[k] for k in ("autonomy_level", "environment")
            if k in candidate
        } or None}
    if family == "model_asset":
        return {"candidate_model": {
            k: candidate[k] for k in ("approval_status", "allowed_data_classes",
                                      "allowed_environments")
            if k in candidate
        } or None}
    return {}


# --- routes ----------------------------------------------------------------


@router.post("", status_code=201)
def create_change(
    payload: ChangeIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    try:
        change = change_service.create_change(
            db, api_key.tenant_id, payload.model_dump())
    except change_service.ChangeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return change_service.change_to_dict(change)


@router.get("")
def list_changes(
    system_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
    change_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    q = db.query(ChangeRecord).filter(ChangeRecord.tenant_id == api_key.tenant_id)
    if system_id:
        q = q.filter(ChangeRecord.system_id == system_id)
    if state:
        q = q.filter(ChangeRecord.state == state)
    if change_type:
        q = q.filter(ChangeRecord.change_type == change_type)
    rows = q.order_by(ChangeRecord.created_at.desc()).limit(limit).all()
    return {"count": len(rows),
            "changes": [change_service.change_to_dict(r) for r in rows]}


@router.get("/{change_id}")
def get_change(
    change_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    return change_service.change_to_dict(_change_or_404(db, api_key, change_id))


@router.post("/{change_id}/replay")
def replay_change(
    change_id: str,
    payload: ReplayChangeIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    change = _change_or_404(db, api_key, change_id)
    system_id = change.system_id or (change.target or {}).get("system_id")
    if not system_id:
        # policy_set changes may be tenant-wide: replay every system in scope
        from helios.models import AiSystem

        rows = (db.query(AiSystem)
                .filter(AiSystem.tenant_id == api_key.tenant_id)
                .order_by(AiSystem.updated_at.desc()).limit(5).all())
        reports = [
            replay_system(db, api_key.tenant_id, s.system_id,
                          run_limit=payload.run_limit,
                          **_candidates_for(change))
            for s in rows
        ]
        report = {"multi_system": True, "reports": reports,
                  "summary": reports[0]["summary"] if reports else {}}
    else:
        report = replay_system(db, api_key.tenant_id, system_id,
                               run_limit=payload.run_limit,
                               **_candidates_for(change))
    change = _guard(change_service.attach_replay, db, change, report, payload.by)
    return change_service.change_to_dict(change)


@router.post("/{change_id}/evaluate")
def evaluate_change(
    change_id: str,
    payload: ByIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    change = _change_or_404(db, api_key, change_id)
    system_id = change.system_id or (change.target or {}).get("system_id")
    if system_id:
        evaluation = evaluate_system(db, api_key.tenant_id, system_id)
    else:
        evaluation = {"status": "not_applicable", "score": None, "passed": None,
                      "results": {}, "evidence": {
                          "reason": "tenant-wide change; no single system to "
                                    "evaluate — replay evidence covers impact"}}
    change = _guard(change_service.attach_evaluation, db, change, evaluation,
                    payload.by)
    return change_service.change_to_dict(change)


@router.post("/{change_id}/risk-comparison")
def risk_comparison(
    change_id: str,
    payload: ByIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    change = _change_or_404(db, api_key, change_id)
    change = _guard(change_service.build_risk_comparison, db, change, payload.by)
    return change_service.change_to_dict(change)


@router.post("/{change_id}/submit-review")
def submit_review(
    change_id: str,
    payload: ByIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    change = _change_or_404(db, api_key, change_id)
    change = _guard(change_service.submit_for_review, db, change, payload.by)
    return change_service.change_to_dict(change)


@router.post("/{change_id}/approve")
def approve_change(
    change_id: str,
    payload: ApproveIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    change = _change_or_404(db, api_key, change_id)
    change = _guard(change_service.approve_change, db, change,
                    payload.approval_id, payload.by)
    return change_service.change_to_dict(change)


@router.post("/{change_id}/reject")
def reject_change(
    change_id: str,
    payload: RejectIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    change = _change_or_404(db, api_key, change_id)
    change = _guard(change_service.reject_change, db, change, payload.by,
                    payload.reason)
    return change_service.change_to_dict(change)


@router.post("/{change_id}/deploy")
def deploy_change(
    change_id: str,
    payload: ByIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    change = _change_or_404(db, api_key, change_id)
    change = _guard(change_service.deploy_change, db, change, payload.by)
    return change_service.change_to_dict(change)


@router.post("/{change_id}/rollback")
def rollback_change(
    change_id: str,
    payload: ByIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    change = _change_or_404(db, api_key, change_id)
    change = _guard(change_service.rollback_change, db, change, payload.by,
                    payload.note)
    data = change_service.change_to_dict(change)
    rollback_record = (
        db.query(ChangeRecord)
        .filter(ChangeRecord.tenant_id == api_key.tenant_id,
                ChangeRecord.change_type == "rollback")
        .order_by(ChangeRecord.created_at.desc())
        .first()
    )
    data["rollback_record"] = (change_service.change_to_dict(rollback_record)
                               if rollback_record else None)
    return data
