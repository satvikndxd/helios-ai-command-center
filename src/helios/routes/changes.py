"""
ASSURANCE PLANE API — AI Change Management.

    POST /v1/changes                       open a change record
    GET  /v1/changes                       list changes
    GET  /v1/changes/{id}                  inspect
    POST /v1/changes/{id}/replay           attach policy-replay evidence
    POST /v1/changes/{id}/transition       move through the lifecycle
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.agent.replay import replay_run
from helios.db import get_db
from helios.governance.changes import (
    ChangeError,
    attach_evidence,
    change_to_dict,
    create_change,
    transition,
)
from helios.models import AgentRun, AgentSession, ApiKey, ChangeRecord
from helios.security import get_api_key

router = APIRouter(prefix="/v1/changes", tags=["changes"])


class ChangeIn(BaseModel):
    kind: str = Field(pattern="^(policy|model|prompt|tool|config)$")
    title: str
    system_id: str | None = None
    current: dict | None = None
    candidate: dict | None = None


class ReplayEvidenceIn(BaseModel):
    run_id: str
    # for a policy change, the candidate policy document to replay against
    policy: dict | None = None
    policy_version: str | None = None


class TransitionIn(BaseModel):
    status: str = Field(pattern="^(replayed|in_review|approved|rejected|deployed|rolled_back)$")


def _author(api_key: ApiKey) -> str:
    return f"apikey:{api_key.id[:8]}"


def _change_or_404(db: Session, api_key: ApiKey, change_id: str) -> ChangeRecord:
    change = (
        db.query(ChangeRecord)
        .filter(ChangeRecord.id == change_id,
                ChangeRecord.tenant_id == api_key.tenant_id)
        .first()
    )
    if change is None:
        raise HTTPException(status_code=404, detail="change not found")
    return change


@router.post("", status_code=201)
def open_change(payload: ChangeIn, api_key: ApiKey = Depends(get_api_key),
                db: Session = Depends(get_db)):
    try:
        change = create_change(db, api_key.tenant_id, payload.model_dump(), _author(api_key))
    except ChangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return change_to_dict(change)


@router.get("")
def list_changes(system_id: str | None = None, status: str | None = None,
                 api_key: ApiKey = Depends(get_api_key), db: Session = Depends(get_db)):
    query = db.query(ChangeRecord).filter(ChangeRecord.tenant_id == api_key.tenant_id)
    if system_id:
        query = query.filter(ChangeRecord.system_id == system_id)
    if status:
        query = query.filter(ChangeRecord.status == status)
    rows = query.order_by(ChangeRecord.created_at.desc()).limit(100).all()
    return {"changes": [change_to_dict(c) for c in rows]}


@router.get("/{change_id}")
def inspect(change_id: str, api_key: ApiKey = Depends(get_api_key),
            db: Session = Depends(get_db)):
    return change_to_dict(_change_or_404(db, api_key, change_id))


@router.post("/{change_id}/replay")
def replay_change(change_id: str, payload: ReplayEvidenceIn,
                  api_key: ApiKey = Depends(get_api_key), db: Session = Depends(get_db)):
    """Replay a historical run under the candidate policy; attach the diff."""
    change = _change_or_404(db, api_key, change_id)
    run = (db.query(AgentRun)
           .filter(AgentRun.id == payload.run_id,
                   AgentRun.tenant_id == api_key.tenant_id)
           .first())
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    session = db.get(AgentSession, run.session_id)
    policy_doc = payload.policy or change.candidate.get("policy")
    try:
        comparison = replay_run(db, run, session,
                                policy_version=payload.policy_version,
                                policy_doc=policy_doc)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    change = attach_evidence(db, change, "replay", comparison)
    return change_to_dict(change)


@router.post("/{change_id}/transition")
def transition_change(change_id: str, payload: TransitionIn,
                      api_key: ApiKey = Depends(get_api_key), db: Session = Depends(get_db)):
    change = _change_or_404(db, api_key, change_id)
    try:
        change = transition(db, change, payload.status, _author(api_key))
    except ChangeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return change_to_dict(change)
