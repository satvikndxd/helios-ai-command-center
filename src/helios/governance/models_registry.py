"""
Model Registry + model governance (IDENTITY + POLICY planes).

A model must be governable independently from any agent:

    Claude-X + PUBLIC data                -> ALLOWED
    Claude-X + CONFIDENTIAL financial     -> DENIED (data ceiling)
    Claude-X in production, approved only
      for staging                         -> DENIED (environment)
    Unknown model on a governed system    -> NOT APPROVED -> DENY

Every decision from `check_model_usage` is a list of explicit checks with
pass/fail + reason — the same explainability DNA as the broker's policy
engine. Approval-status changes are recorded as governance events
(TraceEvents with no run) so "when did this model become approved, by whom?"
is answerable from evidence.

Development providers (`mock`, `scripted`) are exempt from registration ONLY
on legacy unbound sessions: they never call an external model, keeping local
development zero-config. A session bound to a registered AI system is fully
governed — its models must be registered and approved like any other. Every
exemption is recorded as a check, never silent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from helios.broker.trace import TraceRecorder
from helios.governance.data import SEVERITY, max_class, parse_classes
from helios.models import ModelAsset

APPROVAL_STATES = ("pending", "approved", "not_approved", "deprecated")
RISK_CLASSES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

# Providers that never reach an external model — exempt from registration so
# zero-config local dev and deterministic tests keep working.
EXEMPT_PROVIDERS = ("mock", "scripted")


class ModelRegistryError(ValueError):
    """Invalid registry operation."""


# --- registry CRUD ----------------------------------------------------------


def register_model(
    db: Session,
    tenant_id: str,
    data: dict,
    *,
    author: str | None = None,
) -> ModelAsset:
    provider = str(data.get("provider") or "").strip()
    model_id = str(data.get("model_id") or "").strip()
    if not provider or not model_id:
        raise ModelRegistryError("provider and model_id are required")
    version = str(data.get("version") or "latest").strip()

    risk_class = str(data.get("risk_class") or "MEDIUM").upper()
    if risk_class not in RISK_CLASSES:
        raise ModelRegistryError(f"risk_class must be one of {RISK_CLASSES}")
    approval_status = str(data.get("approval_status") or "pending")
    if approval_status not in APPROVAL_STATES:
        raise ModelRegistryError(f"approval_status must be one of {APPROVAL_STATES}")
    if approval_status == "approved" and not data.get("approved_by"):
        # Approving is a governance act; it requires an accountable identity.
        raise ModelRegistryError("approving a model at registration requires approved_by")

    envs = list(data.get("allowed_environments") or [])
    bad_envs = [e for e in envs if e not in ("dev", "staging", "production")]
    if bad_envs:
        raise ModelRegistryError(
            f"allowed_environments must be dev|staging|production, got {bad_envs}"
        )

    existing = find_model(db, tenant_id, provider, model_id, version=version)
    if existing is not None:
        raise ModelRegistryError(
            f"model '{provider}/{model_id}@{version}' already registered"
        )

    asset = ModelAsset(
        tenant_id=tenant_id,
        provider=provider,
        model_id=model_id,
        version=version,
        name=data.get("name"),
        capabilities=list(data.get("capabilities") or []),
        region=data.get("region"),
        pricing=dict(data.get("pricing") or {}),
        risk_class=risk_class,
        approval_status=approval_status,
        allowed_data_classes=parse_classes(data.get("allowed_data_classes") or []),
        allowed_environments=list(data.get("allowed_environments") or []),
        restrictions=dict(data.get("restrictions") or {}),
        evaluation_summary=dict(data.get("evaluation_summary") or {}),
        approved_by=data.get("approved_by") if approval_status == "approved" else None,
        approved_at=(datetime.now(timezone.utc) if approval_status == "approved" else None),
    )
    db.add(asset)
    db.flush()
    _record_event(db, asset, "model_registered",
                  {"by": author, "approval_status": asset.approval_status})
    db.commit()
    return asset


def find_model(
    db: Session,
    tenant_id: str,
    provider: str,
    model_id: str,
    *,
    version: str | None = None,
) -> ModelAsset | None:
    """Exact lookup; version=None matches the 'latest' alias row if present."""
    q = db.query(ModelAsset).filter(
        ModelAsset.tenant_id == tenant_id,
        ModelAsset.provider == provider,
        ModelAsset.model_id == model_id,
    )
    if version is not None:
        return q.filter(ModelAsset.version == version).first()
    exact = q.order_by(ModelAsset.created_at.desc()).all()
    for asset in exact:
        if asset.version == "latest":
            return asset
    return exact[0] if exact else None


def set_approval(
    db: Session,
    asset: ModelAsset,
    status: str,
    *,
    by: str,
    reason: str | None = None,
) -> ModelAsset:
    """Approve / refuse / deprecate a model. `by` is mandatory — accountability."""
    if status not in APPROVAL_STATES:
        raise ModelRegistryError(f"approval_status must be one of {APPROVAL_STATES}")
    if not by:
        raise ModelRegistryError("approval changes require an accountable identity (by)")
    previous = asset.approval_status
    asset.approval_status = status
    if status == "approved":
        asset.approved_by = by
        asset.approved_at = datetime.now(timezone.utc)
        asset.deprecation_reason = None
    if status == "deprecated":
        asset.deprecation_reason = (reason or "")[:500] or None
    db.flush()
    _record_event(db, asset, "model_approval_changed",
                  {"from": previous, "to": status, "by": by, "reason": reason})
    db.commit()
    return asset


def update_model(db: Session, asset: ModelAsset, changes: dict) -> ModelAsset:
    """
    Update governed metadata. Identity fields (provider/model_id/version) are
    NOT mutable — a different model is a different asset (and a governance
    change record in Phase 9).
    """
    allowed = {
        "name", "capabilities", "region", "pricing", "risk_class",
        "allowed_data_classes", "allowed_environments", "restrictions",
        "evaluation_summary",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ModelRegistryError(f"unknown or immutable fields: {sorted(unknown)}")
    if not changes:
        raise ModelRegistryError("no fields to update")
    if "risk_class" in changes:
        risk = str(changes["risk_class"]).upper()
        if risk not in RISK_CLASSES:
            raise ModelRegistryError(f"risk_class must be one of {RISK_CLASSES}")
        changes["risk_class"] = risk
    if "allowed_data_classes" in changes:
        changes["allowed_data_classes"] = parse_classes(changes["allowed_data_classes"])
    for field, value in changes.items():
        setattr(asset, field, value)
    db.flush()
    _record_event(db, asset, "model_updated", {"fields": sorted(changes)})
    db.commit()
    return asset


# --- governance decision ------------------------------------------------------


def check_model_usage(
    db: Session,
    tenant_id: str,
    provider: str,
    model_id: str,
    *,
    version: str | None = None,
    data_classes: list[str] | None = None,
    environment: str | None = None,
    governed: bool = True,
    asset_override=None,
) -> dict:
    """
    Deterministic model-governance decision.

        governed=True (the session is bound to a registered AI system):
        unknown model           -> DENY (deny-by-default; governed means
                                   governed — no development-provider exemption)
        not approved/deprecated -> DENY
        environment not allowed -> DENY
        data above ceiling      -> DENY
    governed=False (legacy unbound session):
        unknown model           -> ALLOW with a recorded warning check
                                   (V1 compatibility; flagged, never silent)
        registered model        -> full approval/environment/data checks
                                   (known models are always enforced)

    `asset_override` substitutes a (possibly transient, unsaved) ModelAsset —
    used by replay to simulate CANDIDATE registry state without mutating it.

    Returns {"decision", "reasons", "checks", "model"} — serializable straight
    onto a trace event.
    """
    checks: list[dict] = []
    effective_class = max_class(parse_classes(data_classes or []))

    def _check(name: str, passed: bool, reason: str) -> bool:
        checks.append({"check": name, "passed": passed, "reason": reason})
        return passed

    asset = (asset_override if asset_override is not None
             else find_model(db, tenant_id, provider, model_id, version=version))

    if asset is None:
        # Unregistered model. Governed systems are deny-by-default — no
        # exceptions for development providers (governed means governed).
        # Legacy unbound sessions keep working (V1 compatibility) but the gap
        # is recorded as a failed check, never silent.
        if governed:
            _check("registration", False,
                   f"model '{provider}/{model_id}' is not registered — "
                   "governed systems may only use registered models (deny by default)")
            return {
                "decision": "deny",
                "reasons": [f"unknown model '{provider}/{model_id}'"],
                "checks": checks,
                "model": {"provider": provider, "model_id": model_id,
                          "version": version, "approval_status": "not_approved"},
            }
        exempt = provider in EXEMPT_PROVIDERS
        _check("registration", False,
               f"model '{provider}/{model_id}' is not registered "
               "(legacy unbound session — allowed but flagged"
               + (", local development provider)" if exempt else ")"))
        return {
            "decision": "allow",
            "reasons": [f"unregistered model on legacy session: {provider}/{model_id}"],
            "checks": checks,
            "model": {"provider": provider, "model_id": model_id,
                      "version": version, "approval_status": "not_approved"},
        }

    status = asset.approval_status
    approved = _check(
        "approval", status == "approved",
        f"model approval status: {status}"
        + (f" (approved by {asset.approved_by})" if asset.approved_by else ""),
    )
    if status == "deprecated":
        checks[-1]["reason"] += f" — deprecated: {asset.deprecation_reason or 'no reason given'}"

    env_ok = True
    if asset is not None and asset.allowed_environments:
        if environment:
            env_ok = _check(
                "environment", environment in asset.allowed_environments,
                f"environment '{environment}' "
                + ("allowed" if environment in asset.allowed_environments
                   else f"not in allowed environments {asset.allowed_environments}"),
            )
        else:
            _check("environment", True,
                   f"no environment in request; model restricted to "
                   f"{asset.allowed_environments} (check skipped)")
    elif asset is not None:
        _check("environment", True, "model declares no environment restriction")

    data_ok = True
    if asset is not None and asset.allowed_data_classes:
        ceiling = max(SEVERITY[c] for c in asset.allowed_data_classes)
        data_ok = _check(
            "data_classification", SEVERITY[effective_class] <= ceiling,
            f"data class '{effective_class}' "
            + ("within" if SEVERITY[effective_class] <= ceiling else "ABOVE")
            + f" model ceiling {asset.allowed_data_classes}",
        )
    elif asset is not None:
        _check("data_classification", True,
               "model declares no data-class ceiling (governance-score gap)")

    denied = (asset is not None and not approved) or not env_ok or not data_ok
    decision = "deny" if denied else "allow"
    reasons = [c["reason"] for c in checks if not c["passed"]]
    return {
        "decision": decision,
        "reasons": reasons or [f"{provider}/{model_id} approved for this usage"],
        "checks": checks,
        "model": model_to_dict(asset) if asset else
                 {"provider": provider, "model_id": model_id, "version": version,
                  "approval_status": "not_approved"},
    }


# --- serialization + evidence ---------------------------------------------------


def model_to_dict(asset: ModelAsset) -> dict:
    return {
        "id": asset.id,
        "provider": asset.provider,
        "model_id": asset.model_id,
        "version": asset.version,
        "name": asset.name,
        "capabilities": list(asset.capabilities or []),
        "region": asset.region,
        "pricing": dict(asset.pricing or {}),
        "risk_class": asset.risk_class,
        "approval_status": asset.approval_status,
        "allowed_data_classes": list(asset.allowed_data_classes or []),
        "allowed_environments": list(asset.allowed_environments or []),
        "restrictions": dict(asset.restrictions or {}),
        "evaluation_summary": dict(asset.evaluation_summary or {}),
        "approved_by": asset.approved_by,
        "approved_at": asset.approved_at.isoformat() if asset.approved_at else None,
        "deprecation_reason": asset.deprecation_reason,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


def _record_event(db: Session, asset: ModelAsset, event_type: str, payload: dict) -> None:
    """Model governance events are evidence: stored, scrubbed, tenant-scoped."""
    recorder = TraceRecorder(db, tenant_id=asset.tenant_id)
    recorder.record(
        "model_governance",
        f"{asset.provider}/{asset.model_id}@{asset.version}",
        {"event": event_type, "model_asset_id": asset.id, **payload},
        status="ok",
    )
