import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from helios.db import SessionLocal
from helios.evaluators import default_pipeline
from helios.models import DecisionTrace, EvaluationJob, ReviewItem


def _escalate_if_needed(db: Session, trace: DecisionTrace, scores: dict) -> None:
    """
    Route low-quality / high-risk decisions to human review (FR-EV-005).

    Triggers: any failed evaluator, or hallucination risk >= 0.5.
    """
    reasons = [name for name, s in scores.items() if not s.get("passed")]
    risk = scores.get("groundedness", {}).get("details", {}).get("hallucination_risk")
    if risk is not None and risk >= 0.5 and "hallucination_risk" not in reasons:
        reasons.append(f"hallucination_risk={risk}")
    if not reasons:
        return
    db.add(
        ReviewItem(
            tenant_id=trace.tenant_id,
            trace_id=trace.id,
            reason=", ".join(reasons)[:255],
        )
    )

logger = logging.getLogger("helios.worker")


# Production (Postgres): atomically claim a batch of pending jobs. FOR UPDATE
# SKIP LOCKED lets N workers run concurrently without ever grabbing the same
# row — the row-level locks a worker holds are skipped by its peers.
_CLAIM_SQL_PG = text(
    """
    UPDATE evaluation_jobs
    SET status = 'processing', attempts = attempts + 1, updated_at = now()
    WHERE id IN (
        SELECT id FROM evaluation_jobs
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT :batch_size
        FOR UPDATE SKIP LOCKED
    )
    RETURNING id, trace_id;
    """
)


def _claim_jobs(db: Session, batch_size: int) -> list[tuple[str, str]]:
    """
    Claim up to `batch_size` pending jobs, marking them 'processing'.

    Returns a list of (job_id, trace_id). Dialect-aware so the exact SKIP LOCKED
    queue runs on Postgres while tests run on SQLite with zero services.
    """
    dialect = db.bind.dialect.name

    if dialect == "postgresql":
        rows = db.execute(_CLAIM_SQL_PG, {"batch_size": batch_size}).fetchall()
        db.commit()
        return [(r[0], r[1]) for r in rows]

    # Portable fallback (SQLite / others): select-then-update in one transaction.
    # No true row locking, which is fine for single-worker local/test usage.
    jobs = (
        db.query(EvaluationJob)
        .filter(EvaluationJob.status == "pending")
        .order_by(EvaluationJob.created_at.asc())
        .limit(batch_size)
        .all()
    )
    claimed: list[tuple[str, str]] = []
    for job in jobs:
        job.status = "processing"
        job.attempts += 1
        claimed.append((job.id, job.trace_id))
    db.commit()
    return claimed


async def process_batch(batch_size: int = 10) -> int:
    """
    Claim and evaluate one batch of jobs. Returns the number processed.

    Each job is finalized in its own commit so a single bad trace can't roll
    back the whole batch.
    """
    db = SessionLocal()
    try:
        claimed = _claim_jobs(db, batch_size)
        if not claimed:
            return 0

        pipeline = default_pipeline()

        for job_id, trace_id in claimed:
            job = db.get(EvaluationJob, job_id)
            trace = db.get(DecisionTrace, trace_id)

            if trace is None:
                if job is not None:
                    job.status = "failed"
                    job.last_error = "trace_not_found"
                db.commit()
                continue

            try:
                scores = pipeline.run(trace)
                trace.evaluation_scores = scores
                _escalate_if_needed(db, trace, scores)
                if job is not None:
                    job.status = "completed"
                    job.last_error = None
                db.commit()
                logger.info("evaluated trace %s: %s", trace_id, scores)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                job = db.get(EvaluationJob, job_id)
                if job is not None:
                    job.status = "failed"
                    job.last_error = str(exc)
                    db.commit()
                logger.exception("evaluation failed for trace %s", trace_id)

        return len(claimed)
    finally:
        db.close()


async def run_worker_loop(poll_interval: float = 2.0, batch_size: int = 10) -> None:
    """Long-running loop: drain jobs, sleep only when the queue is empty."""
    logger.info("Helios evaluation worker started.")
    while True:
        processed = await process_batch(batch_size=batch_size)
        if processed == 0:
            await asyncio.sleep(poll_interval)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_worker_loop())


if __name__ == "__main__":
    main()
