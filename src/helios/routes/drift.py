"""
Drift API (ASSURANCE plane).

    GET  /v1/drift                          open drift signals (system filter)
    POST /v1/drift/{signal_id}/acknowledge  acknowledge an open signal

System-scoped baseline/check endpoints live on the system resource:
    POST /v1/systems/{id}/drift/baseline
    POST /v1/systems/{id}/drift/check
    GET  /v1/systems/{id}/drift
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.governance import drift as drift_service
from helios.models import ApiKey, DriftSignal
from helios.security import get_api_key

router = APIRouter(prefix="/v1/drift", tags=["drift"])


class AcknowledgeIn(BaseModel):
    by: str = Field(min_length=1, max_length=255)
    note: str | None = None


@router.get("")
def list_drift(
    system_id: str | None = Query(default=None),
    status: str | None = Query(default="open",
                               pattern="^(open|acknowledged|resolved)$"),
    limit: int = Query(default=100, ge=1, le=500),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    q = db.query(DriftSignal).filter(DriftSignal.tenant_id == api_key.tenant_id)
    if system_id:
        q = q.filter(DriftSignal.system_id == system_id)
    if status:
        q = q.filter(DriftSignal.status == status)
    rows = q.order_by(DriftSignal.last_seen.desc()).limit(limit).all()
    return {
        "count": len(rows),
        "signals": [drift_service._signal_to_dict(s) for s in rows],
    }


@router.post("/{signal_id}/acknowledge")
def acknowledge(
    signal_id: str,
    payload: AcknowledgeIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    signal = (
        db.query(DriftSignal)
        .filter(DriftSignal.id == signal_id,
                DriftSignal.tenant_id == api_key.tenant_id)
        .first()
    )
    if signal is None:
        raise HTTPException(status_code=404, detail="drift signal not found")
    if signal.status != "open":
        raise HTTPException(status_code=409, detail=f"already {signal.status}")
    signal = drift_service.acknowledge_signal(db, signal, payload.by, payload.note)
    return drift_service._signal_to_dict(signal)
