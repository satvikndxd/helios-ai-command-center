"""
Audit API (ASSURANCE plane).

    GET /v1/audit/{system_id}            full governance evidence report (JSON)
    GET /v1/systems/{system_id}/audit    alias on the system resource

The report is machine-readable by construction: the JSON response IS the
export. It is governance evidence — policy status, audit record, control
status — never a legal/regulatory compliance certification.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.governance.audit import build_audit_report
from helios.governance.systems import RegistryError
from helios.models import ApiKey
from helios.security import get_api_key

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("/{system_id}")
def audit_report(
    system_id: str,
    window: int = Query(default=50, ge=1, le=500),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    try:
        return build_audit_report(db, api_key.tenant_id, system_id, window=window)
    except RegistryError:
        raise HTTPException(status_code=404,
                            detail=f"ai system '{system_id}' not found")
