"""
POLICY PLANE — the Model Registry + model-use governance.

A model is governable independently from any agent. The core question:

    model + data classes + environment -> ALLOWED | DENIED | NOT_APPROVED

Unknown model            -> NOT_APPROVED (deny by default)
Approved model + PUBLIC  -> ALLOWED
Approved model + data class it is not cleared for -> DENIED

Every status change is a governance event.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from helios.models import AIModel, TraceEvent
from helios.broker.trace import TraceRecorder


MODEL_STATUS = ("proposed", "approved", "revoked")


class ModelGovernanceError(ValueError):
    pass


@dataclass
class ModelUseDecision:
    """Result of gating one model call against the registry."""

    allowed: bool
    status: str  # allowed | denied | not_approved
    provider: str
    model_id: str
    reasons: list[str] = field(default_factory=list)
    model_version: str | None = None
    risk_class: str | None = None

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "provider": self.provider,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "risk_class": self.risk_class,
            "reasons": list(self.reasons),
        }


def _model_event(db: Session, tenant_id: str, model_key: str, name: str, payload: dict):
    run_id = f"gov:model:{model_key}"
    last = (
        db.query(TraceEvent.seq)
        .filter(TraceEvent.run_id == run_id, TraceEvent.tenant_id == tenant_id)
        .order_by(TraceEvent.seq.desc())
        .first()
    )
    recorder = TraceRecorder(
        db, tenant_id=tenant_id, run_id=run_id, start_seq=last[0] if last else 0
    )
    recorder.record("governance_event", name, payload)


def get_model(db: Session, tenant_id: str, provider: str, model_id: str) -> AIModel | None:
    return (
        db.query(AIModel)
        .filter(
            AIModel.tenant_id == tenant_id,
            AIModel.provider == provider,
            AIModel.model_id == model_id,
        )
        .order_by(AIModel.created_at.desc())
        .first()
    )


def register_model(db: Session, tenant_id: str, data: dict, author: str) -> AIModel:
    provider = str(data.get("provider", "")).strip()
    model_id = str(data.get("model_id", "")).strip()
    if not provider or not model_id:
        raise ModelGovernanceError("provider and model_id are required")

    existing = get_model(db, tenant_id, provider, model_id)
    if existing is not None:
        raise ModelGovernanceError(f"model '{provider}/{model_id}' already registered")

    status = data.get("status", "proposed")
    if status not in MODEL_STATUS:
        raise ModelGovernanceError(f"status must be one of {MODEL_STATUS}")

    model = AIModel(
        tenant_id=tenant_id,
        provider=provider,
        model_id=model_id,
        version=str(data.get("version", "1")),
        capabilities=list(data.get("capabilities") or []),
        region=data.get("region"),
        pricing=dict(data.get("pricing") or {}),
        risk_class=data.get("risk_class") or "medium",
        status=status,
        allowed_data_classes=list(data.get("allowed_data_classes") or ["public"]),
        allowed_environments=list(data.get("allowed_environments") or ["dev"]),
        notes=data.get("notes") or "",
        decided_by=author if status != "proposed" else None,
    )
    db.add(model)
    db.commit()
    _model_event(db, tenant_id, f"{provider}/{model_id}", "model_registered", {
        "provider": provider, "model_id": model_id, "status": status,
        "allowed_data_classes": model.allowed_data_classes,
        "allowed_environments": model.allowed_environments, "author": author,
    })
    return model


def set_model_status(db: Session, model: AIModel, status: str, author: str) -> AIModel:
    if status not in MODEL_STATUS:
        raise ModelGovernanceError(f"status must be one of {MODEL_STATUS}")
    previous = model.status
    model.status = status
    model.decided_by = author
    db.commit()
    _model_event(db, model.tenant_id, f"{model.provider}/{model.model_id}",
                 "model_status_changed",
                 {"from": previous, "to": status, "author": author})
    return model


def update_model(db: Session, model: AIModel, updates: dict, author: str) -> AIModel:
    changed = {}
    for field_name in ("version", "risk_class", "region", "notes",
                       "allowed_data_classes", "allowed_environments",
                       "capabilities", "pricing"):
        if field_name in updates and updates[field_name] is not None:
            previous = getattr(model, field_name)
            if previous != updates[field_name]:
                changed[field_name] = {"from": previous, "to": updates[field_name]}
                setattr(model, field_name, updates[field_name])
    if changed:
        db.commit()
        _model_event(db, model.tenant_id, f"{model.provider}/{model.model_id}",
                     "model_updated", {"changed": changed, "author": author})
    return model


def evaluate_model_use(
    db: Session,
    tenant_id: str,
    provider: str,
    model_id: str,
    *,
    data_classes: list[str] | None = None,
    environment: str = "dev",
) -> ModelUseDecision:
    """
    Governance gate for a model call. Deterministic, explainable, deny by
    default. Called by the runtime before every model_call when the session
    is bound to a registered AI system.
    """
    data_classes = [d.lower() for d in (data_classes or [])]
    model = get_model(db, tenant_id, provider, model_id)

    if model is None:
        return ModelUseDecision(
            allowed=False, status="not_approved", provider=provider, model_id=model_id,
            reasons=[f"model '{provider}/{model_id}' is not in the registry "
                     "(unknown models are not approved)"],
        )

    reasons: list[str] = []
    if model.status != "approved":
        return ModelUseDecision(
            allowed=False, status="not_approved", provider=provider, model_id=model_id,
            model_version=model.version, risk_class=model.risk_class,
            reasons=[f"model status is '{model.status}', not 'approved'"],
        )
    reasons.append("model approved")

    allowed_env = model.allowed_environments or []
    if allowed_env and environment not in allowed_env:
        return ModelUseDecision(
            allowed=False, status="denied", provider=provider, model_id=model_id,
            model_version=model.version, risk_class=model.risk_class,
            reasons=[f"environment '{environment}' not in model's allowed "
                     f"environments {allowed_env}"],
        )
    reasons.append(f"environment '{environment}' permitted")

    cleared = {d.lower() for d in (model.allowed_data_classes or [])}
    for data_class in data_classes:
        if data_class not in cleared:
            return ModelUseDecision(
                allowed=False, status="denied", provider=provider, model_id=model_id,
                model_version=model.version, risk_class=model.risk_class,
                reasons=[f"data class '{data_class}' not cleared for this model "
                         f"(cleared: {sorted(cleared)})"],
            )
    if data_classes:
        reasons.append(f"data classes {data_classes} cleared")
    else:
        reasons.append("no sensitive data classes declared")

    return ModelUseDecision(
        allowed=True, status="allowed", provider=provider, model_id=model_id,
        model_version=model.version, risk_class=model.risk_class, reasons=reasons,
    )


def model_to_dict(model: AIModel) -> dict:
    return {
        "id": model.id,
        "provider": model.provider,
        "model_id": model.model_id,
        "version": model.version,
        "capabilities": model.capabilities,
        "region": model.region,
        "pricing": model.pricing,
        "risk_class": model.risk_class,
        "status": model.status,
        "allowed_data_classes": model.allowed_data_classes,
        "allowed_environments": model.allowed_environments,
        "notes": model.notes,
        "decided_by": model.decided_by,
        "created_at": model.created_at.isoformat() if model.created_at else None,
        "updated_at": model.updated_at.isoformat() if model.updated_at else None,
    }
