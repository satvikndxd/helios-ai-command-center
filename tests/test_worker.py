import asyncio

from helios.db import SessionLocal
from helios.models import DecisionTrace, EvaluationJob
from helios.worker import process_batch


def _pending_job_count(trace_id: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(EvaluationJob)
            .filter(
                EvaluationJob.trace_id == trace_id,
                EvaluationJob.status == "pending",
            )
            .count()
        )
    finally:
        db.close()


def test_completion_enqueues_job(client, api_key):
    resp = client.post(
        "/v1/ai/complete",
        json={"input": "hello worker"},
        headers={"X-Helios-API-Key": api_key},
    )
    assert resp.status_code == 200
    trace_id = resp.json()["trace_id"]

    # The hot path must have enqueued exactly one pending job.
    assert _pending_job_count(trace_id) == 1


def test_worker_evaluates_trace_end_to_end(client, api_key):
    # 1. Produce a real trace + job through the gateway.
    resp = client.post(
        "/v1/ai/complete",
        json={"input": "evaluate me"},
        headers={"X-Helios-API-Key": api_key},
    )
    trace_id = resp.json()["trace_id"]

    # 2. Run the worker once.
    processed = asyncio.run(process_batch())
    assert processed >= 1

    # 3. The trace now carries evaluation scores; the job is completed.
    db = SessionLocal()
    try:
        trace = db.get(DecisionTrace, trace_id)
        assert trace.evaluation_scores is not None
        scores = trace.evaluation_scores

        # All three heuristic evaluators ran.
        assert set(scores.keys()) == {
            "empty_output",
            "latency_sla",
            "refusal_detection",
        }
        # Mock output is non-empty and not a refusal.
        assert scores["empty_output"]["passed"] is True
        assert scores["refusal_detection"]["passed"] is True

        job = (
            db.query(EvaluationJob)
            .filter(EvaluationJob.trace_id == trace_id)
            .one()
        )
        assert job.status == "completed"
        assert job.attempts == 1
    finally:
        db.close()

    # 4. Nothing left to do -> idempotent, returns 0.
    assert asyncio.run(process_batch()) == 0


def test_worker_flags_empty_and_refusal_outputs():
    """Directly exercise the pipeline's failure signals on a synthetic trace."""
    from helios.evaluators import default_pipeline

    db = SessionLocal()
    try:
        refusal = DecisionTrace(
            tenant_id="t",
            application_id="a",
            task_type="completion",
            risk_level="low",
            input_payload={},
            request_payload={},
            model_provider="mock",
            model_id="mock-model-1",
            output_text="I'm sorry, but I can't help with that.",
            cost_usd=0.0,
            latency_ms=10,
            citations=[],
            tool_calls=[],
            status="success",
        )
        scores = default_pipeline().run(refusal)
        assert scores["refusal_detection"]["passed"] is False
        assert scores["empty_output"]["passed"] is True

        empty = DecisionTrace(
            tenant_id="t",
            application_id="a",
            task_type="completion",
            risk_level="low",
            input_payload={},
            request_payload={},
            model_provider="mock",
            model_id="mock-model-1",
            output_text="",
            cost_usd=0.0,
            latency_ms=9999,
            citations=[],
            tool_calls=[],
            status="success",
        )
        scores = default_pipeline().run(empty)
        assert scores["empty_output"]["passed"] is False
        assert scores["latency_sla"]["passed"] is False  # 9999 > 5000
    finally:
        db.close()
