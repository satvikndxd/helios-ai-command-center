"""
AI Decision Record API (EVIDENCE plane).

    GET  /v1/decisions                    query the normalized record store
    GET  /v1/decisions/{id}               one record (full evidence refs)
    GET  /v1/decisions/{id}/explanation   the WHY — assembled from evidence
    POST /v1/decisions/rebuild            (re)project records for one run
                                          (idempotent; backfills pre-Phase-6 runs)

Records are projections of immutable TraceEvents; the rebuild endpoint makes
the projection convergent — as approvals are decided and runs resume, records
refresh from the newest evidence without ever duplicating.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.governance.decisions import build_for_run, explain, record_to_dict
from helios.models import AgentRun, ApiKey, DecisionRecord
from helios.security import get_api_key

router = APIRouter(prefix="/v1/decisions", tags=["decisions"])


class RebuildIn(BaseModel):
    run_id: str = Field(min_length=1)


def _record_or_404(db: Session, api_key: ApiKey, record_id: str) -> DecisionRecord:
    record = (
        db.query(DecisionRecord)
        .filter(DecisionRecord.id == record_id,
                DecisionRecord.tenant_id == api_key.tenant_id)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="decision record not found")
    return record


@router.get("")
def list_decisions(
    system_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    kind: str | None = Query(default=None,
                             pattern="^(tool_action|model_preflight|run_outcome)$"),
    decision: str | None = Query(default=None),
    risk: str | None = Query(default=None, pattern="^(low|medium|high|critical)$"),
    oversight_required: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    q = db.query(DecisionRecord).filter(DecisionRecord.tenant_id == api_key.tenant_id)
    if system_id:
        q = q.filter(DecisionRecord.system_id == system_id)
    if run_id:
        q = q.filter(DecisionRecord.run_id == run_id)
    if kind:
        q = q.filter(DecisionRecord.kind == kind)
    if decision:
        q = q.filter(DecisionRecord.decision == decision.upper())
    if risk:
        q = q.filter(DecisionRecord.risk == risk)
    rows = q.order_by(DecisionRecord.created_at.desc()).limit(limit).all()
    if oversight_required is not None:
        rows = [r for r in rows
                if bool((r.oversight or {}).get("required")) == oversight_required]
    return {"count": len(rows), "decisions": [record_to_dict(r) for r in rows]}


@router.get("/{record_id}")
def get_decision(
    record_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    return record_to_dict(_record_or_404(db, api_key, record_id))


@router.get("/{record_id}/explanation")
def explain_decision(
    record_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    WHY was this allowed / blocked / gated? Assembled from the record's
    stored policy, permission, classification, risk, and oversight evidence —
    every line cites recorded state, nothing is canned.
    """
    return explain(_record_or_404(db, api_key, record_id))


@router.post("/rebuild")
def rebuild_decisions(
    payload: RebuildIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    run = (
        db.query(AgentRun)
        .filter(AgentRun.id == payload.run_id,
                AgentRun.tenant_id == api_key.tenant_id)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    result = build_for_run(db, api_key.tenant_id, run.id)
    return {"run_id": run.id, **result}
