"""
Governance policy API (POLICY plane).

    POST   /v1/policies                          create a policy set (candidate by default)
    GET    /v1/policies/governance               list sets (filter: status, name)
    GET    /v1/policies/governance/{id}          inspect one set
    POST   /v1/policies/governance/{id}/activate enforce (archives previous same-name active)
    POST   /v1/policies/governance/{id}/archive  retire
    POST   /v1/policies/dry-run                  evaluate a subject against active sets,
                                                 optionally including a candidate set —
                                                 policy changes are testable BEFORE deployment

The V1 tool-policy listing stays on GET /v1/policies (routes/agents.py) and is
extended there with a `governance_policies` section — the response keeps its
`policies` key intact for existing consumers.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.governance import engine as governance_engine
from helios.governance.policy import GovernancePolicySet, GovernanceSubject
from helios.models import ApiKey, GovernancePolicySet as PolicySetRow
from helios.security import get_api_key

router = APIRouter(prefix="/v1/policies", tags=["policies"])


# --- schemas ---------------------------------------------------------------


class PolicySetIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    version: str = Field(min_length=1, max_length=50)
    description: str = ""
    rules: list[dict] = Field(default_factory=list)
    default_effect: str = Field(
        default="allow",
        pattern="^(allow|deny|require_approval|require_human_review|block_deployment)$",
    )
    scope: dict = Field(default_factory=dict)
    status: str = Field(default="candidate", pattern="^(candidate|active)$")
    author: str | None = None


class DryRunIn(BaseModel):
    subject: dict = Field(default_factory=dict)
    # Evaluate this stored candidate set IN ADDITION to the active ones.
    candidate_policy_set_id: str | None = None
    # Or an inline policy document (replay-style, no persistence needed).
    candidate_policy: dict | None = None


# --- helpers ---------------------------------------------------------------


def _row_or_404(db: Session, api_key: ApiKey, set_id: str) -> PolicySetRow:
    row = (
        db.query(PolicySetRow)
        .filter(PolicySetRow.id == set_id, PolicySetRow.tenant_id == api_key.tenant_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="policy set not found")
    return row


# --- routes ----------------------------------------------------------------


@router.post("", status_code=201)
def create_policy_set(
    payload: PolicySetIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    try:
        row = governance_engine.create_policy_set(
            db, api_key.tenant_id, payload.model_dump(exclude_none=True),
            status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return governance_engine.policy_set_to_dict(row)


@router.get("/governance")
def list_policy_sets(
    status: str | None = Query(default=None, pattern="^(candidate|active|archived)$"),
    name: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    q = db.query(PolicySetRow).filter(PolicySetRow.tenant_id == api_key.tenant_id)
    if status:
        q = q.filter(PolicySetRow.status == status)
    if name:
        q = q.filter(PolicySetRow.name == name)
    rows = q.order_by(PolicySetRow.name, PolicySetRow.version).limit(limit).all()
    return {
        "count": len(rows),
        "builtin_defaults": [DEFAULT_DICT],
        "policy_sets": [governance_engine.policy_set_to_dict(r) for r in rows],
    }


DEFAULT_DICT = governance_engine.DEFAULT_GOVERNANCE_POLICY.to_dict()


@router.get("/governance/{set_id}")
def get_policy_set(
    set_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    return governance_engine.policy_set_to_dict(_row_or_404(db, api_key, set_id))


@router.post("/governance/{set_id}/activate")
def activate_policy_set(
    set_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    row = _row_or_404(db, api_key, set_id)
    row = governance_engine.activate_policy_set(db, row)
    return governance_engine.policy_set_to_dict(row)


@router.post("/governance/{set_id}/archive")
def archive_policy_set(
    set_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    row = _row_or_404(db, api_key, set_id)
    row = governance_engine.archive_policy_set(db, row)
    return governance_engine.policy_set_to_dict(row)


@router.post("/dry-run")
def dry_run(
    payload: DryRunIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Test a governance subject — and optionally a CANDIDATE policy — without
    enforcing anything. This is the pre-deployment policy testbed: same
    evaluation code path as production, zero side effects.
    """
    subject_data = dict(payload.subject or {})
    kind = subject_data.get("kind")
    if kind not in ("deployment", "model_call", "tool_action", "system"):
        raise HTTPException(
            status_code=400,
            detail="subject.kind must be one of deployment|model_call|tool_action|system",
        )
    subject = GovernanceSubject.from_dict(subject_data)

    extra: list[GovernancePolicySet] = []
    if payload.candidate_policy_set_id:
        row = _row_or_404(db, api_key, payload.candidate_policy_set_id)
        extra.append(GovernancePolicySet.from_dict({
            "name": row.name, "version": row.version, "rules": row.rules,
            "description": row.description, "default_effect": row.default_effect,
            "scope": row.scope or {},
        }))
    if payload.candidate_policy:
        try:
            extra.append(GovernancePolicySet.from_dict(payload.candidate_policy))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400,
                                detail=f"invalid candidate policy: {exc}")

    decision = governance_engine.evaluate_subject(
        db, api_key.tenant_id, subject, extra_sets=extra)
    return {
        "subject": subject.to_dict(),
        "decision": decision.to_dict(),
        "candidate_included": bool(extra),
    }
