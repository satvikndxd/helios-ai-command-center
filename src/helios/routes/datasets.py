from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

import json

from helios.db import get_db
from helios.models import ApiKey, Dataset, DatasetItem, DecisionTrace
from helios.schemas import DatasetBuildIn, DatasetOut
from helios.security import get_api_key


router = APIRouter(tags=["datasets"])


def _trace_failed(trace: DecisionTrace) -> bool:
    """A trace counts as a failure if it errored, was blocked, or failed evals."""
    if trace.status in {"error", "blocked"}:
        return True
    scores = trace.evaluation_scores or {}
    return any(not s.get("passed", True) for s in scores.values())


def _labels_for(trace: DecisionTrace) -> dict:
    scores = trace.evaluation_scores or {}
    return {
        "status": trace.status,
        "failed_evaluators": [k for k, s in scores.items() if not s.get("passed", True)],
        "hallucination_risk": scores.get("groundedness", {})
        .get("details", {})
        .get("hallucination_risk"),
        "feedback": (trace.feedback or {}).get("rating"),
        "risk_level": trace.risk_level,
        "task_type": trace.task_type,
    }


@router.post("/v1/datasets/build", response_model=DatasetOut, status_code=201)
def build_dataset(
    payload: DatasetBuildIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Helios Forge (FR-DS-001/005/006): mine production traces into a versioned
    evaluation dataset. Sources:

    - failures: errored/blocked traces or any failed evaluator
    - negative_feedback: traces with a thumbs-down
    - all: every trace (up to limit)
    """
    traces = (
        db.query(DecisionTrace)
        .filter(DecisionTrace.tenant_id == api_key.tenant_id)
        .order_by(DecisionTrace.created_at.desc())
        .limit(1000)
        .all()
    )

    if payload.source == "failures":
        selected = [t for t in traces if _trace_failed(t)]
    elif payload.source == "negative_feedback":
        selected = [t for t in traces if (t.feedback or {}).get("rating") == "down"]
    else:
        selected = traces
    selected = selected[: payload.limit]

    if not selected:
        raise HTTPException(
            status_code=422, detail=f"No traces match source '{payload.source}'"
        )

    # Versioning with lineage: same name -> auto-incremented version.
    max_version = (
        db.query(func.max(Dataset.version))
        .filter(Dataset.tenant_id == api_key.tenant_id, Dataset.name == payload.name)
        .scalar()
    )
    dataset = Dataset(
        tenant_id=api_key.tenant_id,
        name=payload.name,
        version=(max_version or 0) + 1,
        kind="evaluation",
        source=payload.source,
        item_count=len(selected),
    )
    db.add(dataset)
    db.flush()

    for trace in selected:
        db.add(
            DatasetItem(
                dataset_id=dataset.id,
                trace_id=trace.id,
                input_text=str((trace.input_payload or {}).get("input", "")),
                reference_output=trace.output_text,
                labels=_labels_for(trace),
            )
        )
    db.commit()
    return dataset


@router.get("/v1/datasets", response_model=list[DatasetOut])
def list_datasets(
    limit: int = Query(default=50, ge=1, le=200),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    return (
        db.query(Dataset)
        .filter(Dataset.tenant_id == api_key.tenant_id)
        .order_by(Dataset.created_at.desc(), Dataset.version.desc())
        .limit(limit)
        .all()
    )


@router.get("/v1/datasets/{dataset_id}/export", response_class=PlainTextResponse)
def export_dataset(
    dataset_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Export a dataset as JSONL (one evaluation case per line)."""
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.tenant_id == api_key.tenant_id)
        .first()
    )
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    items = (
        db.query(DatasetItem)
        .filter(DatasetItem.dataset_id == dataset.id)
        .order_by(DatasetItem.created_at.asc())
        .all()
    )
    lines = [
        json.dumps(
            {
                "input": item.input_text,
                "reference_output": item.reference_output,
                "labels": item.labels,
                "trace_id": item.trace_id,
                "dataset": f"{dataset.name}:v{dataset.version}",
            }
        )
        for item in items
    ]
    return "\n".join(lines)
