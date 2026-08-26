"""
Action + approval + schedule routes (Phase W4).

propose -> human decision -> execute (idempotent) — every step audited.
Scheduled research runs through the same governed broker and never writes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.models import ApiKey, ApprovalRequest, ScheduledResearch
from helios.routes.web import get_broker
from helios.security import get_api_key
from helios.web.actions import (
    ACTION_REGISTRY,
    ActionDenied,
    execute_action,
    propose_action,
    run_scheduled_research,
)

router = APIRouter(tags=["actions"])


class ProposeIn(BaseModel):
    action: str
    args: dict = Field(default_factory=dict)


class DecideIn(BaseModel):
    decision: str  # "approved" | "denied"
    decided_by: str = "operator"


class ExecuteIn(BaseModel):
    action: str
    args: dict = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=100)


class ScheduleIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    sources: list[str] = Field(default_factory=list)
    interval_minutes: int = Field(default=60, ge=5, le=10_080)


@router.get("/v1/actions")
async def list_actions(api_key: ApiKey = Depends(get_api_key)):
    """The typed action registry — the only actions that can ever execute."""
    return {
        "actions": [
            {"action": name, "risk": meta["risk"], "description": meta["description"]}
            for name, meta in ACTION_REGISTRY.items()
        ]
    }


@router.post("/v1/actions/propose", status_code=201)
async def propose(
    payload: ProposeIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    try:
        approval = propose_action(db, api_key.tenant_id, payload.action, payload.args)
    except ActionDenied as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "approval_id": approval.id,
        "action": approval.action,
        "risk": approval.risk,
        "args_hash": approval.args_hash,
        "status": approval.status,
    }


@router.get("/v1/approvals")
async def list_approvals(
    status: str | None = None,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    query = db.query(ApprovalRequest).filter(
        ApprovalRequest.tenant_id == api_key.tenant_id
    )
    if status:
        query = query.filter(ApprovalRequest.status == status)
    return {
        "approvals": [
            {
                "id": a.id,
                "action": a.action,
                "risk": a.risk,
                "summary": a.summary,
                "status": a.status,
                "args_hash": a.args_hash,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in query.order_by(ApprovalRequest.created_at.desc()).all()
        ]
    }


@router.post("/v1/approvals/{approval_id}/decide")
async def decide(
    approval_id: str,
    payload: DecideIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    if payload.decision not in ("approved", "denied"):
        raise HTTPException(status_code=422, detail="decision must be approved|denied")
    approval = (
        db.query(ApprovalRequest)
        .filter(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.tenant_id == api_key.tenant_id,
        )
        .first()
    )
    if not approval:
        raise HTTPException(status_code=404, detail="approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail=f"already {approval.status}")

    approval.status = payload.decision
    approval.decided_by = payload.decided_by
    approval.decided_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": approval.id, "status": approval.status, "decided_by": approval.decided_by}


@router.post("/v1/actions/execute")
async def execute(
    payload: ExecuteIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    try:
        effect, replayed = execute_action(
            db, api_key.tenant_id, payload.action, payload.args, payload.idempotency_key
        )
    except ActionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "effect_id": effect.id,
        "action": effect.action,
        "status": effect.status,
        "replayed": replayed,  # True => idempotent replay, nothing re-executed
        "result": effect.result,
    }


# -- scheduled research ----------------------------------------------------


@router.post("/v1/schedules", status_code=201)
async def create_schedule(
    payload: ScheduleIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    schedule = ScheduledResearch(
        tenant_id=api_key.tenant_id,
        query=payload.query,
        sources=payload.sources,
        interval_minutes=payload.interval_minutes,
    )
    db.add(schedule)
    db.commit()
    return {"id": schedule.id, "query": schedule.query, "interval_minutes": schedule.interval_minutes}


@router.get("/v1/schedules")
async def list_schedules(
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    schedules = (
        db.query(ScheduledResearch)
        .filter(ScheduledResearch.tenant_id == api_key.tenant_id)
        .all()
    )
    return {
        "schedules": [
            {
                "id": s.id,
                "query": s.query,
                "interval_minutes": s.interval_minutes,
                "active": s.active,
                "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
                "last_report": s.last_report,
            }
            for s in schedules
        ]
    }


@router.post("/v1/schedules/{schedule_id}/run")
async def run_schedule(
    schedule_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
    broker=Depends(get_broker),
):
    """One watch cycle now (the worker runs due schedules automatically)."""
    schedule = (
        db.query(ScheduledResearch)
        .filter(
            ScheduledResearch.id == schedule_id,
            ScheduledResearch.tenant_id == api_key.tenant_id,
        )
        .first()
    )
    if not schedule:
        raise HTTPException(status_code=404, detail="schedule not found")
    report = run_scheduled_research(db, schedule, broker)
    return {"id": schedule.id, "report": report}
