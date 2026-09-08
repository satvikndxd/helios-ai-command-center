"""
Evaluation API (ASSURANCE plane).

    POST /v1/systems/{system_id}/evaluate   run + persist a governance evaluation
                                            (or a benchmark against a Dataset)
    GET  /v1/evaluations                    list evaluation runs (system filter)
    GET  /v1/evaluations/{id}               full per-metric results + evidence
    GET  /v1/systems/{system_id}/score      the transparent governance score

Evaluations are stored so regression suites have baselines to compare
against (Phase 9 change records reference them; Phase 10 drift compares
evaluation pass rates over time).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.governance.evaluators import evaluate_benchmark, evaluate_system
from helios.governance.score import compute_governance_score
from helios.governance.systems import get_system
from helios.models import ApiKey, EvaluationRun
from helios.security import get_api_key

router = APIRouter(prefix="/v1", tags=["evaluations"])


class EvaluateIn(BaseModel):
    kind: str = Field(default="governance", pattern="^(governance|benchmark)$")
    dataset_id: str | None = None


def _system_or_404(db: Session, api_key: ApiKey, system_id: str):
    system = get_system(db, api_key.tenant_id, system_id)
    if system is None:
        raise HTTPException(status_code=404,
                            detail=f"ai system '{system_id}' not found")
    return system


def _evaluation_to_dict(row: EvaluationRun) -> dict:
    return {
        "id": row.id,
        "system_id": row.system_id,
        "kind": row.kind,
        "status": row.status,
        "score": row.score,
        "passed": row.passed,
        "results": row.results,
        "evidence": row.evidence,
        "dataset_id": row.dataset_id,
        "baseline_evaluation_id": row.baseline_evaluation_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("/systems/{system_id}/evaluate", status_code=201)
def evaluate(
    system_id: str,
    payload: EvaluateIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    system = _system_or_404(db, api_key, system_id)

    if payload.kind == "benchmark":
        if not payload.dataset_id:
            raise HTTPException(status_code=400,
                                detail="benchmark evaluation requires dataset_id")
        try:
            result = evaluate_benchmark(db, api_key.tenant_id,
                                        system.system_id, payload.dataset_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        dataset_id = payload.dataset_id
    else:
        result = evaluate_system(db, api_key.tenant_id, system.system_id,
                                 system=system)
        dataset_id = None

    previous = (
        db.query(EvaluationRun)
        .filter(EvaluationRun.tenant_id == api_key.tenant_id,
                EvaluationRun.system_id == system.system_id,
                EvaluationRun.kind == payload.kind,
                EvaluationRun.status == "completed")
        .order_by(EvaluationRun.created_at.desc())
        .first()
    )
    row = EvaluationRun(
        tenant_id=api_key.tenant_id,
        system_id=system.system_id,
        kind=payload.kind,
        status=result["status"],
        score=result["score"],
        passed=result["passed"],
        results=result["results"],
        evidence=result["evidence"],
        dataset_id=dataset_id,
        baseline_evaluation_id=previous.id if previous else None,
    )
    db.add(row)
    db.commit()
    return _evaluation_to_dict(row)


@router.get("/evaluations")
def list_evaluations(
    system_id: str | None = Query(default=None),
    kind: str | None = Query(default=None, pattern="^(governance|benchmark|regression)$"),
    limit: int = Query(default=50, ge=1, le=500),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    q = db.query(EvaluationRun).filter(
        EvaluationRun.tenant_id == api_key.tenant_id)
    if system_id:
        q = q.filter(EvaluationRun.system_id == system_id)
    if kind:
        q = q.filter(EvaluationRun.kind == kind)
    rows = q.order_by(EvaluationRun.created_at.desc()).limit(limit).all()
    return {"count": len(rows), "evaluations": [_evaluation_to_dict(r) for r in rows]}


@router.get("/evaluations/{evaluation_id}")
def get_evaluation(
    evaluation_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    row = (
        db.query(EvaluationRun)
        .filter(EvaluationRun.id == evaluation_id,
                EvaluationRun.tenant_id == api_key.tenant_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="evaluation not found")
    return _evaluation_to_dict(row)


@router.get("/systems/{system_id}/score")
def governance_score(
    system_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    The transparent governance status: every component with its checks,
    weights, evidence refs, and the overall formula inputs.
    """
    system = _system_or_404(db, api_key, system_id)
    return compute_governance_score(db, system)
