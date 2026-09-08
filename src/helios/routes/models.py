"""
Model Registry API (IDENTITY + POLICY planes).

    POST   /v1/models                 register a model asset
    GET    /v1/models                 list (filter: provider, status, environment)
    GET    /v1/models/lookup          exact lookup by provider/model_id/version
    POST   /v1/models/check           governance compatibility check (the WHY)
    PATCH  /v1/models/{id}            update governed metadata
    POST   /v1/models/{id}/approve    approve (accountable identity required)
    POST   /v1/models/{id}/deny       refuse
    POST   /v1/models/{id}/deprecate  retire

Approval changes are recorded as governance events (TraceEvents), so the
audit trail answers "when did this model become approved, by whom?".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.governance import models_registry as registry
from helios.models import ApiKey, ModelAsset
from helios.security import get_api_key

router = APIRouter(prefix="/v1/models", tags=["models"])


# --- schemas ---------------------------------------------------------------


class ModelIn(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    model_id: str = Field(min_length=1, max_length=150)
    version: str = Field(default="latest", max_length=50)
    name: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    region: str | None = None
    pricing: dict = Field(default_factory=dict)
    risk_class: str = Field(default="MEDIUM", pattern="^(?i:LOW|MEDIUM|HIGH|CRITICAL)$")
    approval_status: str = Field(default="pending",
                                 pattern="^(pending|approved|not_approved|deprecated)$")
    allowed_data_classes: list[str] = Field(default_factory=list)
    allowed_environments: list[str] = Field(default_factory=list)
    restrictions: dict = Field(default_factory=dict)
    approved_by: str | None = None
    author: str | None = None


class ModelPatch(BaseModel):
    name: str | None = None
    capabilities: list[str] | None = None
    region: str | None = None
    pricing: dict | None = None
    risk_class: str | None = Field(default=None, pattern="^(?i:LOW|MEDIUM|HIGH|CRITICAL)$")
    allowed_data_classes: list[str] | None = None
    allowed_environments: list[str] | None = None
    restrictions: dict | None = None
    evaluation_summary: dict | None = None


class ApprovalIn(BaseModel):
    by: str = Field(min_length=1, max_length=255)
    reason: str | None = None


class CheckIn(BaseModel):
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    version: str | None = None
    data_classes: list[str] = Field(default_factory=list)
    environment: str | None = Field(default=None, pattern="^(dev|staging|production)$")
    governed: bool = True


# --- helpers ---------------------------------------------------------------


def _asset_or_404(db: Session, api_key: ApiKey, asset_id: str) -> ModelAsset:
    asset = (
        db.query(ModelAsset)
        .filter(ModelAsset.id == asset_id, ModelAsset.tenant_id == api_key.tenant_id)
        .first()
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="model asset not found")
    return asset


# --- routes ----------------------------------------------------------------


@router.post("", status_code=201)
def register_model(
    payload: ModelIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    try:
        asset = registry.register_model(
            db, api_key.tenant_id, payload.model_dump(exclude_none=True),
            author=payload.author,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return registry.model_to_dict(asset)


@router.get("")
def list_models(
    provider: str | None = Query(default=None),
    approval_status: str | None = Query(default=None),
    environment: str | None = Query(default=None, pattern="^(dev|staging|production)$"),
    limit: int = Query(default=100, ge=1, le=500),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    q = db.query(ModelAsset).filter(ModelAsset.tenant_id == api_key.tenant_id)
    if provider:
        q = q.filter(ModelAsset.provider == provider)
    if approval_status:
        q = q.filter(ModelAsset.approval_status == approval_status)
    rows = q.order_by(ModelAsset.created_at.desc()).limit(limit).all()
    if environment:
        rows = [
            a for a in rows
            if not a.allowed_environments or environment in a.allowed_environments
        ]
    return {"count": len(rows), "models": [registry.model_to_dict(a) for a in rows]}


@router.get("/lookup")
def lookup_model(
    provider: str = Query(min_length=1),
    model_id: str = Query(min_length=1),
    version: str | None = Query(default=None),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    asset = registry.find_model(db, api_key.tenant_id, provider, model_id,
                                version=version)
    if asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"model '{provider}/{model_id}' is not registered "
                   "(unknown models are NOT APPROVED by default)",
        )
    return registry.model_to_dict(asset)


@router.post("/check")
def check_model(
    payload: CheckIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    The model-governance WHY endpoint: is this model allowed to receive these
    data classes in this environment? Returns the decision with every check.
    """
    try:
        return registry.check_model_usage(
            db, api_key.tenant_id, payload.provider, payload.model_id,
            version=payload.version, data_classes=payload.data_classes,
            environment=payload.environment, governed=payload.governed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/{asset_id}")
def update_model(
    asset_id: str,
    payload: ModelPatch,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(db, api_key, asset_id)
    try:
        asset = registry.update_model(db, asset, payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return registry.model_to_dict(asset)


@router.post("/{asset_id}/approve")
def approve_model(
    asset_id: str,
    payload: ApprovalIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(db, api_key, asset_id)
    try:
        asset = registry.set_approval(db, asset, "approved", by=payload.by,
                                      reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return registry.model_to_dict(asset)


@router.post("/{asset_id}/deny")
def deny_model(
    asset_id: str,
    payload: ApprovalIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(db, api_key, asset_id)
    try:
        asset = registry.set_approval(db, asset, "not_approved", by=payload.by,
                                      reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return registry.model_to_dict(asset)


@router.post("/{asset_id}/deprecate")
def deprecate_model(
    asset_id: str,
    payload: ApprovalIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(db, api_key, asset_id)
    try:
        asset = registry.set_approval(db, asset, "deprecated", by=payload.by,
                                      reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return registry.model_to_dict(asset)
