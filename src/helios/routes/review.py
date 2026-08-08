from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.models import ApiKey, DecisionTrace, ReviewItem
from helios.schemas import FeedbackIn, ReviewItemOut, ReviewResolveIn
from helios.security import get_api_key


router = APIRouter(tags=["review"])


@router.get("/v1/review/queue", response_model=list[ReviewItemOut])
def review_queue(
    status: str = Query(default="open"),
    limit: int = Query(default=50, ge=1, le=200),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Human review queue: decisions flagged by evaluation or feedback."""
    return (
        db.query(ReviewItem)
        .filter(ReviewItem.tenant_id == api_key.tenant_id, ReviewItem.status == status)
        .order_by(ReviewItem.created_at.desc())
        .limit(limit)
        .all()
    )


@router.post("/v1/review/{item_id}/resolve", response_model=ReviewItemOut)
def resolve_review_item(
    item_id: str,
    payload: ReviewResolveIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    item = (
        db.query(ReviewItem)
        .filter(ReviewItem.id == item_id, ReviewItem.tenant_id == api_key.tenant_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Review item not found")
    if item.status == "resolved":
        raise HTTPException(status_code=409, detail="Already resolved")

    item.status = "resolved"
    item.resolution = {"verdict": payload.verdict, "notes": payload.notes}
    db.commit()
    return item


@router.post("/v1/traces/{trace_id}/feedback")
def submit_feedback(
    trace_id: str,
    payload: FeedbackIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Attach user/business feedback to a decision (FR-EV-008).

    Negative feedback also escalates the trace to the human review queue —
    closing the loop from production signal to review to dataset.
    """
    trace = (
        db.query(DecisionTrace)
        .filter(
            DecisionTrace.id == trace_id,
            DecisionTrace.tenant_id == api_key.tenant_id,
        )
        .first()
    )
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    trace.feedback = payload.model_dump()
    if payload.rating == "down":
        db.add(
            ReviewItem(
                tenant_id=trace.tenant_id,
                trace_id=trace.id,
                reason="negative_user_feedback",
            )
        )
    db.commit()
    return {"trace_id": trace_id, "feedback": trace.feedback}
