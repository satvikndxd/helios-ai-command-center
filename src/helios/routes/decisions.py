"""
EVIDENCE PLANE API — normalized AI Decision Records + WHY explanations.

    GET  /v1/decisions                 query decision records
    GET  /v1/decisions/{id}            one record
    GET  /v1/decisions/{id}/why        governance explanation from real state
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.governance.decisions import record_to_dict
from helios.governance.explain import explain_decision
from helios.models import ApiKey, DecisionRecord
from helios.security import get_api_key

router = APIRouter(prefix="/v1/decisions", tags=["decisions"])


@router.get("")
def list_decisions(
    system_id: str | None = None,
    run_id: str | None = None,
    kind: str | None = None,
    decision: str | None = None,
    limit: int = 100,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    query = db.query(DecisionRecord).filter(DecisionRecord.tenant_id == api_key.tenant_id)
    if system_id:
        query = query.filter(DecisionRecord.system_id == system_id)
    if run_id:
        query = query.filter(DecisionRecord.run_id == run_id)
    if kind:
        query = query.filter(DecisionRecord.kind == kind)
    if decision:
        query = query.filter(DecisionRecord.decision == decision)
    rows = query.order_by(DecisionRecord.created_at.desc()).limit(min(limit, 500)).all()
    return {"decisions": [record_to_dict(r) for r in rows]}


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


@router.get("/{record_id}")
def get_decision(record_id: str, api_key: ApiKey = Depends(get_api_key),
                 db: Session = Depends(get_db)):
    return record_to_dict(_record_or_404(db, api_key, record_id))


@router.get("/{record_id}/why")
def why(record_id: str, api_key: ApiKey = Depends(get_api_key),
        db: Session = Depends(get_db)):
    record = _record_or_404(db, api_key, record_id)
    return explain_decision(db, record)
