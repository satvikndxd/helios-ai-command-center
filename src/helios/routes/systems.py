"""
IDENTITY PLANE API — the AI System Registry.

    POST /v1/systems                register an AI system
    GET  /v1/systems                list / search systems
    GET  /v1/systems/{id}           inspect (with revisions)
    PATCH /v1/systems/{id}          update (versioned)
    POST /v1/systems/{id}/lifecycle activate | archive
    GET  /v1/systems/{id}/events    governance event stream
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.broker.trace import event_to_dict
from helios.db import get_db
from helios.governance.systems import (
    GovernanceError,
    create_system,
    get_system,
    set_lifecycle,
    system_to_dict,
    update_system,
)
from helios.models import AISystem, ApiKey, TraceEvent
from helios.security import get_api_key

router = APIRouter(prefix="/v1/systems", tags=["systems"])


class SystemIn(BaseModel):
    system_id: str = Field(min_length=1, max_length=100)
    name: str | None = None
    owner: str | None = None
    organization: str | None = None
    purpose: str | None = None
    description: str | None = None
    environment: str = Field(default="dev", pattern="^(dev|staging|production)$")
    autonomy_level: int = Field(default=1, ge=0, le=5)
    risk_class: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    models: list[dict] | None = None
    tools: list[str] | None = None
    data_classes: list[str] | None = None
    policies: list[str] | None = None
    oversight: dict | None = None
    activate: bool = True


class SystemPatch(BaseModel):
    name: str | None = None
    owner: str | None = None
    organization: str | None = None
    purpose: str | None = None
    description: str | None = None
    environment: str | None = Field(default=None, pattern="^(dev|staging|production)$")
    autonomy_level: int | None = Field(default=None, ge=0, le=5)
    risk_class: str | None = Field(default=None, pattern="^(low|medium|high|critical)$")
    models: list[dict] | None = None
    tools: list[str] | None = None
    data_classes: list[str] | None = None
    policies: list[str] | None = None
    oversight: dict | None = None


class LifecycleIn(BaseModel):
    lifecycle: str = Field(pattern="^(draft|active|archived)$")


def _author(api_key: ApiKey) -> str:
    return f"apikey:{api_key.id[:8]}"


@router.post("", status_code=201)
def register(payload: SystemIn, api_key: ApiKey = Depends(get_api_key),
             db: Session = Depends(get_db)):
    try:
        system = create_system(db, api_key.tenant_id, payload.model_dump(),
                               _author(api_key))
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return system_to_dict(system, include_revisions=True)


@router.get("")
def list_systems(
    q: str | None = Query(default=None),
    lifecycle: str | None = None,
    environment: str | None = None,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    query = db.query(AISystem).filter(AISystem.tenant_id == api_key.tenant_id)
    if lifecycle:
        query = query.filter(AISystem.lifecycle == lifecycle)
    if environment:
        query = query.filter(AISystem.environment == environment)
    systems = query.order_by(AISystem.created_at.desc()).limit(200).all()
    if q:
        needle = q.lower()
        systems = [
            s for s in systems
            if needle in s.system_id.lower() or needle in s.name.lower()
            or needle in (s.owner or "").lower() or needle in (s.purpose or "").lower()
        ]
    return {"systems": [system_to_dict(s) for s in systems]}


def _system_or_404(db: Session, api_key: ApiKey, system_id: str) -> AISystem:
    system = get_system(db, api_key.tenant_id, system_id)
    if system is None:
        raise HTTPException(status_code=404, detail="system not found")
    return system


@router.get("/{system_id}")
def inspect(system_id: str, api_key: ApiKey = Depends(get_api_key),
            db: Session = Depends(get_db)):
    return system_to_dict(_system_or_404(db, api_key, system_id), include_revisions=True)


@router.patch("/{system_id}")
def patch(system_id: str, payload: SystemPatch, api_key: ApiKey = Depends(get_api_key),
          db: Session = Depends(get_db)):
    system = _system_or_404(db, api_key, system_id)
    try:
        system = update_system(db, system, payload.model_dump(exclude_none=True),
                               _author(api_key))
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return system_to_dict(system, include_revisions=True)


@router.post("/{system_id}/lifecycle")
def lifecycle(system_id: str, payload: LifecycleIn,
              api_key: ApiKey = Depends(get_api_key), db: Session = Depends(get_db)):
    system = _system_or_404(db, api_key, system_id)
    try:
        system = set_lifecycle(db, system, payload.lifecycle, _author(api_key))
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return system_to_dict(system)


@router.get("/{system_id}/lineage")
def lineage(system_id: str, api_key: ApiKey = Depends(get_api_key),
            db: Session = Depends(get_db)):
    from helios.governance.lineage import system_lineage

    _system_or_404(db, api_key, system_id)
    return system_lineage(db, api_key.tenant_id, system_id)


@router.get("/{system_id}/events")
def events(system_id: str, api_key: ApiKey = Depends(get_api_key),
           db: Session = Depends(get_db)):
    _system_or_404(db, api_key, system_id)
    rows = (
        db.query(TraceEvent)
        .filter(TraceEvent.tenant_id == api_key.tenant_id,
                TraceEvent.run_id == f"gov:{system_id}")
        .order_by(TraceEvent.seq)
        .limit(500)
        .all()
    )
    return {"system_id": system_id, "events": [event_to_dict(e) for e in rows]}
