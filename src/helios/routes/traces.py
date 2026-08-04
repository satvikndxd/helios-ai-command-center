from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.models import ApiKey, DecisionTrace
from helios.schemas import TraceOut
from helios.security import get_api_key


router = APIRouter(tags=["traces"])


@router.get("/v1/traces", response_model=list[TraceOut])
def list_traces(
    limit: int = Query(default=20, ge=1, le=100),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    List recent traces for the authenticated tenant.
    """

    traces = (
        db.query(DecisionTrace)
        .filter(DecisionTrace.tenant_id == api_key.tenant_id)
        .order_by(DecisionTrace.created_at.desc())
        .limit(limit)
        .all()
    )

    return traces


@router.get("/v1/traces/{trace_id}", response_model=TraceOut)
def get_trace(
    trace_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Fetch one trace by ID.

    Tenant isolation is enforced explicitly.
    """

    trace = (
        db.query(DecisionTrace)
        .filter(
            DecisionTrace.id == trace_id,
            DecisionTrace.tenant_id == api_key.tenant_id,
        )
        .first()
    )

    if not trace:
        raise HTTPException(
            status_code=404,
            detail="Trace not found",
        )

    return trace
