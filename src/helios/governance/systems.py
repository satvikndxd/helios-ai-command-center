"""
AI System Registry (IDENTITY plane).

The registry answers, for the whole organization:

    "What AI systems do we have?"
    "What is this system allowed to do?"
    "Who owns it?"
    "What models and tools does it use?"
    "What data does it touch?"
    "What level of autonomy does it has?"

Design rules:
* Every mutation is validated strictly (autonomy 0..5, canonical risk classes,
  canonical data classes, known environments/lifecycle states) and snapshots
  the FULL previous state into `ai_system_versions` before applying — so any
  version can be diffed or rolled back to.
* `system_id` is the human-facing stable key, unique per tenant. The UUID `id`
  is internal.
* Lifecycle: draft -> active -> (suspended <-> active) -> archived;
  `blocked` is entered only with a reason (set by the policy plane's
  deployment gate in Phase 4, or manually by an operator).
* Nothing here executes anything; the registry is pure identity + composition.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.governance import autonomy as autonomy_vocab
from helios.governance.data import parse_classes
from helios.models import AiSystem, AiSystemVersion

ENVIRONMENTS = ("dev", "staging", "production")
RISK_CLASSES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
LIFECYCLE_STATES = ("draft", "active", "suspended", "archived", "blocked")

# Fields a PATCH may change. `system_id`, `tenant_id`, `id`, `version`,
# timestamps, and `blocked_reason` are not directly editable (blocked_reason is
# set through lifecycle transitions so it is always paired with a state).
MUTABLE_FIELDS = (
    "name", "owner", "organization", "purpose", "description",
    "environment", "autonomy_level", "risk_class",
    "models", "tools", "data_classes", "policies", "oversight", "deployment",
)


class RegistryError(ValueError):
    """Invalid registry operation (bad field, unknown system, duplicate id)."""


def _validate(data: dict, *, partial: bool = False) -> dict:
    """Strict field validation; returns normalized values."""
    out: dict = {}

    if "environment" in data:
        if data["environment"] not in ENVIRONMENTS:
            raise RegistryError(
                f"environment must be one of {ENVIRONMENTS}, got {data['environment']!r}"
            )
        out["environment"] = data["environment"]

    if "autonomy_level" in data:
        out["autonomy_level"] = autonomy_vocab.parse_level(data["autonomy_level"])

    if "risk_class" in data:
        risk = str(data["risk_class"]).strip().upper()
        if risk not in RISK_CLASSES:
            raise RegistryError(f"risk_class must be one of {RISK_CLASSES}, got {risk!r}")
        out["risk_class"] = risk

    if "data_classes" in data:
        out["data_classes"] = parse_classes(data["data_classes"])

    for list_field in ("models", "tools", "policies"):
        if list_field in data:
            value = data[list_field]
            if not isinstance(value, list):
                raise RegistryError(f"{list_field} must be a list")
            out[list_field] = list(value)

    if "oversight" in data:
        if not isinstance(data["oversight"], dict):
            raise RegistryError("oversight must be an object")
        out["oversight"] = dict(data["oversight"])

    if "deployment" in data:
        if not isinstance(data["deployment"], dict):
            raise RegistryError("deployment must be an object")
        out["deployment"] = dict(data["deployment"])

    for text_field in ("name", "owner", "organization", "purpose", "description"):
        if text_field in data:
            value = data[text_field]
            if value is not None and not isinstance(value, str):
                raise RegistryError(f"{text_field} must be a string")
            if text_field in ("name", "owner") and not (value or "").strip():
                raise RegistryError(f"{text_field} is required and must be non-empty")
            out[text_field] = value

    if not partial:
        sid = str(data.get("system_id") or "").strip()
        if not sid:
            raise RegistryError("system_id is required")
        if not all(ch.isalnum() or ch in "-_." for ch in sid):
            raise RegistryError(
                "system_id must contain only alphanumeric characters, '-', '_', '.' "
                f"(got {sid!r})"
            )
        out["system_id"] = sid
        for required in ("name", "owner"):
            if required not in out:
                raise RegistryError(f"{required} is required")

    unknown = set(data) - set(MUTABLE_FIELDS) - {"system_id", "lifecycle", "author",
                                                  "change_summary"}
    if unknown:
        raise RegistryError(f"unknown fields: {sorted(unknown)}")
    return out


def snapshot(system: AiSystem) -> dict:
    """Full serializable snapshot of a system's governed state."""
    return {
        "system_id": system.system_id,
        "name": system.name,
        "owner": system.owner,
        "organization": system.organization,
        "purpose": system.purpose,
        "description": system.description,
        "environment": system.environment,
        "lifecycle": system.lifecycle,
        "autonomy_level": system.autonomy_level,
        "risk_class": system.risk_class,
        "models": list(system.models or []),
        "tools": list(system.tools or []),
        "data_classes": list(system.data_classes or []),
        "policies": list(system.policies or []),
        "oversight": dict(system.oversight or {}),
        "deployment": dict(system.deployment or {}),
        "version": system.version,
    }


def _record_version(
    db: Session, system: AiSystem, *, author: str | None, change_summary: str | None
) -> AiSystemVersion:
    row = AiSystemVersion(
        tenant_id=system.tenant_id,
        system_pk=system.id,
        system_id=system.system_id,
        version=system.version,
        snapshot=snapshot(system),
        author=author,
        change_summary=(change_summary or "")[:500] or None,
    )
    db.add(row)
    return row


def create_system(
    db: Session,
    tenant_id: str,
    data: dict,
    *,
    author: str | None = None,
    lifecycle: str = "draft",
) -> AiSystem:
    """Register a new AI system. Duplicate system_id within a tenant is an error."""
    validated = _validate(data)
    if lifecycle not in LIFECYCLE_STATES:
        raise RegistryError(f"lifecycle must be one of {LIFECYCLE_STATES}")

    existing = get_system(db, tenant_id, validated["system_id"])
    if existing is not None:
        raise RegistryError(f"ai system '{validated['system_id']}' already registered")

    system = AiSystem(
        tenant_id=tenant_id,
        system_id=validated["system_id"],
        name=validated["name"],
        owner=validated["owner"],
        organization=validated.get("organization"),
        purpose=validated.get("purpose") or "",
        description=validated.get("description"),
        environment=validated.get("environment", "dev"),
        lifecycle=lifecycle,
        autonomy_level=validated.get("autonomy_level", 1),
        risk_class=validated.get("risk_class", "MEDIUM"),
        models=validated.get("models", []),
        tools=validated.get("tools", []),
        data_classes=validated.get("data_classes", []),
        policies=validated.get("policies", []),
        oversight=validated.get("oversight", {}),
        deployment=validated.get("deployment", {}),
        version=1,
    )
    db.add(system)
    db.flush()  # assign id before the version snapshot FK
    _record_version(db, system, author=author or data.get("author"),
                    change_summary="registered")
    db.commit()
    return system


def get_system(db: Session, tenant_id: str, system_id: str) -> AiSystem | None:
    """Lookup by human-facing id, tenant-scoped."""
    return (
        db.query(AiSystem)
        .filter(AiSystem.tenant_id == tenant_id, AiSystem.system_id == system_id)
        .first()
    )


def get_system_or_raise(db: Session, tenant_id: str, system_id: str) -> AiSystem:
    system = get_system(db, tenant_id, system_id)
    if system is None:
        raise RegistryError(f"ai system '{system_id}' not found")
    return system


def update_system(
    db: Session,
    system: AiSystem,
    changes: dict,
    *,
    author: str | None = None,
    change_summary: str | None = None,
) -> AiSystem:
    """
    Apply a validated partial update. Bumps `version` and snapshots the NEW
    state (version N is always retrievable; diffs come from N-1 vs N).
    Archived systems are immutable except for lifecycle transitions.
    """
    if system.lifecycle == "archived":
        raise RegistryError(
            f"ai system '{system.system_id}' is archived; unarchive before editing"
        )
    validated = _validate(changes, partial=True)
    if not validated:
        raise RegistryError("no valid fields to update")

    for field_name, value in validated.items():
        setattr(system, field_name, value)
    system.version += 1
    db.flush()
    _record_version(db, system, author=author, change_summary=change_summary)
    db.commit()
    return system


def set_lifecycle(
    db: Session,
    system: AiSystem,
    state: str,
    *,
    reason: dict | None = None,
    author: str | None = None,
) -> AiSystem:
    """
    Lifecycle transition (activate / suspend / archive / block / unblock).

    `blocked` requires a reason {"policy": ..., "rule_id": ..., "detail": ...}
    so a blocked system always explains WHY. Transitions snapshot like updates.
    """
    if state not in LIFECYCLE_STATES:
        raise RegistryError(f"lifecycle must be one of {LIFECYCLE_STATES}")
    if state == "blocked" and not reason:
        raise RegistryError("blocking a system requires a reason (policy/rule/detail)")
    if system.lifecycle == state:
        return system

    previous = system.lifecycle
    system.lifecycle = state
    system.blocked_reason = reason if state == "blocked" else None
    system.version += 1
    db.flush()
    _record_version(
        db, system, author=author,
        change_summary=f"lifecycle {previous} -> {state}"
        + (f" ({reason.get('detail')})" if state == "blocked" and reason else ""),
    )
    db.commit()
    return system


def search_systems(
    db: Session,
    tenant_id: str,
    *,
    query: str | None = None,
    environment: str | None = None,
    lifecycle: str | None = None,
    risk_class: str | None = None,
    owner: str | None = None,
    limit: int = 100,
) -> list[AiSystem]:
    """Filter/search the registry. `query` matches system_id/name/purpose/owner."""
    q = db.query(AiSystem).filter(AiSystem.tenant_id == tenant_id)
    if environment:
        q = q.filter(AiSystem.environment == environment)
    if lifecycle:
        q = q.filter(AiSystem.lifecycle == lifecycle)
    if risk_class:
        q = q.filter(AiSystem.risk_class == risk_class.upper())
    if owner:
        q = q.filter(AiSystem.owner == owner)
    rows = q.order_by(AiSystem.updated_at.desc()).limit(min(limit, 500)).all()
    if query:
        needle = query.lower()
        rows = [
            s for s in rows
            if needle in (s.system_id or "").lower()
            or needle in (s.name or "").lower()
            or needle in (s.purpose or "").lower()
            or needle in (s.owner or "").lower()
        ]
    return rows


def list_versions(db: Session, system: AiSystem) -> list[AiSystemVersion]:
    return (
        db.query(AiSystemVersion)
        .filter(AiSystemVersion.system_pk == system.id)
        .order_by(AiSystemVersion.version.desc())
        .all()
    )


def get_version(db: Session, system: AiSystem, version: int) -> AiSystemVersion | None:
    return (
        db.query(AiSystemVersion)
        .filter(AiSystemVersion.system_pk == system.id, AiSystemVersion.version == version)
        .first()
    )


def system_to_dict(system: AiSystem, *, include_blocked: bool = True) -> dict:
    data = snapshot(system)
    data.update({
        "id": system.id,
        "tenant_id": system.tenant_id,
        "autonomy_label": autonomy_vocab.label(system.autonomy_level),
        "created_at": system.created_at.isoformat() if system.created_at else None,
        "updated_at": system.updated_at.isoformat() if system.updated_at else None,
    })
    if include_blocked and system.blocked_reason:
        data["blocked_reason"] = system.blocked_reason
    return data
