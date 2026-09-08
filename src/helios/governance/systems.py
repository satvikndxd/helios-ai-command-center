"""
IDENTITY PLANE — the AI System Registry.

An organization must be able to answer: what AI systems do we have, who owns
them, what are they for, what autonomy do they have, and what models/tools/
data are they allowed to touch?

Every mutation is versioned (revisions carry author + changed fields) and
emits a governance event into the evidence stream `gov:<system_id>`.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.broker.trace import TraceRecorder
from helios.models import AISystem, TraceEvent


AUTONOMY_LEVELS = {
    0: "informational only",
    1: "recommendations",
    2: "supervised actions",
    3: "conditional autonomy",
    4: "high autonomy",
    5: "unrestricted autonomy",
}

LIFECYCLES = ("draft", "active", "archived")
RISK_CLASSES = ("low", "medium", "high", "critical")

# Fields a caller may set/update directly.
MUTABLE_FIELDS = (
    "name", "owner", "organization", "purpose", "description", "environment",
    "autonomy_level", "risk_class", "models", "tools", "data_classes",
    "policies", "oversight",
)


class GovernanceError(ValueError):
    pass


def record_governance_event(
    db: Session, tenant_id: str, system_id: str, name: str, payload: dict
) -> TraceEvent:
    """Append to the system's governance evidence stream (run `gov:<id>`)."""
    run_id = f"gov:{system_id}"
    last = (
        db.query(TraceEvent.seq)
        .filter(TraceEvent.run_id == run_id, TraceEvent.tenant_id == tenant_id)
        .order_by(TraceEvent.seq.desc())
        .first()
    )
    recorder = TraceRecorder(
        db, tenant_id=tenant_id, run_id=run_id, start_seq=last[0] if last else 0
    )
    return recorder.record("governance_event", name, payload)


def validate_system_fields(data: dict) -> None:
    level = data.get("autonomy_level")
    if level is not None and level not in AUTONOMY_LEVELS:
        raise GovernanceError(f"autonomy_level must be 0-5, got {level!r}")
    risk = data.get("risk_class")
    if risk is not None and risk not in RISK_CLASSES:
        raise GovernanceError(f"risk_class must be one of {RISK_CLASSES}, got {risk!r}")
    environment = data.get("environment")
    if environment is not None and environment not in ("dev", "staging", "production"):
        raise GovernanceError(f"environment must be dev|staging|production, got {environment!r}")


def get_system(db: Session, tenant_id: str, system_id: str) -> AISystem | None:
    return (
        db.query(AISystem)
        .filter(AISystem.tenant_id == tenant_id, AISystem.system_id == system_id)
        .first()
    )


def create_system(db: Session, tenant_id: str, data: dict, author: str) -> AISystem:
    system_id = str(data.get("system_id", "")).strip()
    if not system_id:
        raise GovernanceError("system_id is required")
    if get_system(db, tenant_id, system_id) is not None:
        raise GovernanceError(f"system '{system_id}' already exists")
    validate_system_fields(data)

    system = AISystem(
        tenant_id=tenant_id,
        system_id=system_id,
        name=data.get("name") or system_id,
        owner=data.get("owner") or "unassigned",
        organization=data.get("organization"),
        purpose=data.get("purpose") or "",
        description=data.get("description") or "",
        environment=data.get("environment") or "dev",
        lifecycle="active" if data.get("activate", True) else "draft",
        autonomy_level=int(data.get("autonomy_level", 1)),
        risk_class=data.get("risk_class") or "medium",
        models=list(data.get("models") or []),
        tools=list(data.get("tools") or []),
        data_classes=list(data.get("data_classes") or []),
        policies=list(data.get("policies") or []),
        oversight=dict(data.get("oversight") or {}),
        revisions=[{"version": 1, "author": author, "change": "registered"}],
    )
    db.add(system)
    db.commit()
    record_governance_event(db, tenant_id, system_id, "system_registered", {
        "system_id": system_id,
        "owner": system.owner,
        "environment": system.environment,
        "autonomy_level": system.autonomy_level,
        "risk_class": system.risk_class,
        "author": author,
    })
    return system


def update_system(db: Session, system: AISystem, updates: dict, author: str) -> AISystem:
    if system.lifecycle == "archived":
        raise GovernanceError("archived systems cannot be updated — unarchive first")
    validate_system_fields(updates)

    changed: dict = {}
    for field in MUTABLE_FIELDS:
        if field in updates and updates[field] is not None:
            previous = getattr(system, field)
            new = updates[field]
            if previous != new:
                changed[field] = {"from": previous, "to": new}
                setattr(system, field, new)
    if not changed:
        return system

    system.version += 1
    system.revisions = list(system.revisions or []) + [{
        "version": system.version, "author": author, "changed": changed,
    }]
    db.commit()
    record_governance_event(db, system.tenant_id, system.system_id, "system_updated", {
        "version": system.version, "changed": changed, "author": author,
    })
    return system


def set_lifecycle(db: Session, system: AISystem, lifecycle: str, author: str) -> AISystem:
    if lifecycle not in LIFECYCLES:
        raise GovernanceError(f"lifecycle must be one of {LIFECYCLES}")
    previous = system.lifecycle
    if previous == lifecycle:
        return system
    system.lifecycle = lifecycle
    system.version += 1
    system.revisions = list(system.revisions or []) + [{
        "version": system.version, "author": author,
        "changed": {"lifecycle": {"from": previous, "to": lifecycle}},
    }]
    db.commit()
    record_governance_event(db, system.tenant_id, system.system_id, "lifecycle_changed", {
        "from": previous, "to": lifecycle, "author": author,
    })
    return system


def system_to_dict(system: AISystem, include_revisions: bool = False) -> dict:
    data = {
        "system_id": system.system_id,
        "name": system.name,
        "owner": system.owner,
        "organization": system.organization,
        "purpose": system.purpose,
        "description": system.description,
        "environment": system.environment,
        "lifecycle": system.lifecycle,
        "autonomy_level": system.autonomy_level,
        "autonomy_label": AUTONOMY_LEVELS.get(system.autonomy_level, "?"),
        "risk_class": system.risk_class,
        "models": system.models,
        "tools": system.tools,
        "data_classes": system.data_classes,
        "policies": system.policies,
        "oversight": system.oversight,
        "version": system.version,
        "created_at": system.created_at.isoformat() if system.created_at else None,
        "updated_at": system.updated_at.isoformat() if system.updated_at else None,
    }
    if include_revisions:
        data["revisions"] = system.revisions
    return data
