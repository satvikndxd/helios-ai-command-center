"""
POLICY PLANE API — the Model Registry.

    POST /v1/models                      register a model
    GET  /v1/models                      list models
    GET  /v1/models/{id}                 inspect
    PATCH /v1/models/{id}                update clearances
    POST /v1/models/{id}/status          approve | revoke
    POST /v1/models/evaluate             gate a model+data+env combination
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.governance.model_registry import (
    ModelGovernanceError,
    evaluate_model_use,
    model_to_dict,
    register_model,
    set_model_status,
    update_model,
)
from helios.models import AIModel, ApiKey
from helios.security import get_api_key

router = APIRouter(prefix="/v1/models", tags=["models"])


class ModelIn(BaseModel):
    provider: str
    model_id: str
    version: str = "1"
    capabilities: list[str] | None = None
    region: str | None = None
    pricing: dict | None = None
    risk_class: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    status: str = Field(default="proposed", pattern="^(proposed|approved|revoked)$")
    allowed_data_classes: list[str] | None = None
    allowed_environments: list[str] | None = None
    notes: str | None = None


class ModelPatch(BaseModel):
    version: str | None = None
    risk_class: str | None = Field(default=None, pattern="^(low|medium|high|critical)$")
    region: str | None = None
    notes: str | None = None
    allowed_data_classes: list[str] | None = None
    allowed_environments: list[str] | None = None
    capabilities: list[str] | None = None
    pricing: dict | None = None


class StatusIn(BaseModel):
    status: str = Field(pattern="^(proposed|approved|revoked)$")


class EvaluateIn(BaseModel):
    provider: str
    model_id: str
    data_classes: list[str] = Field(default_factory=list)
    environment: str = Field(default="dev", pattern="^(dev|staging|production)$")


def _author(api_key: ApiKey) -> str:
    return f"apikey:{api_key.id[:8]}"


@router.post("", status_code=201)
def register(payload: ModelIn, api_key: ApiKey = Depends(get_api_key),
             db: Session = Depends(get_db)):
    try:
        model = register_model(db, api_key.tenant_id, payload.model_dump(), _author(api_key))
    except ModelGovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return model_to_dict(model)


@router.get("")
def list_models(status: str | None = None, api_key: ApiKey = Depends(get_api_key),
                db: Session = Depends(get_db)):
    query = db.query(AIModel).filter(AIModel.tenant_id == api_key.tenant_id)
    if status:
        query = query.filter(AIModel.status == status)
    models = query.order_by(AIModel.created_at.desc()).limit(200).all()
    return {"models": [model_to_dict(m) for m in models]}


def _model_or_404(db: Session, api_key: ApiKey, model_pk: str) -> AIModel:
    model = (
        db.query(AIModel)
        .filter(AIModel.id == model_pk, AIModel.tenant_id == api_key.tenant_id)
        .first()
    )
    if model is None:
        raise HTTPException(status_code=404, detail="model not found")
    return model


@router.post("/evaluate")
def evaluate(payload: EvaluateIn, api_key: ApiKey = Depends(get_api_key),
             db: Session = Depends(get_db)):
    decision = evaluate_model_use(
        db, api_key.tenant_id, payload.provider, payload.model_id,
        data_classes=payload.data_classes, environment=payload.environment,
    )
    return decision.to_dict()


@router.get("/{model_pk}")
def inspect(model_pk: str, api_key: ApiKey = Depends(get_api_key),
            db: Session = Depends(get_db)):
    return model_to_dict(_model_or_404(db, api_key, model_pk))


@router.patch("/{model_pk}")
def patch(model_pk: str, payload: ModelPatch, api_key: ApiKey = Depends(get_api_key),
          db: Session = Depends(get_db)):
    model = _model_or_404(db, api_key, model_pk)
    model = update_model(db, model, payload.model_dump(exclude_none=True), _author(api_key))
    return model_to_dict(model)


@router.post("/{model_pk}/status")
def status(model_pk: str, payload: StatusIn, api_key: ApiKey = Depends(get_api_key),
           db: Session = Depends(get_db)):
    model = _model_or_404(db, api_key, model_pk)
    try:
        model = set_model_status(db, model, payload.status, _author(api_key))
    except ModelGovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return model_to_dict(model)
