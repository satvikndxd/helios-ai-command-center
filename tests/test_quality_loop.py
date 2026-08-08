import asyncio

from helios.db import SessionLocal
from helios.evaluators import default_pipeline
from helios.models import DecisionTrace, ReviewItem
from helios.worker import process_batch


REFUND_DOC = (
    "Refund Policy. Enterprise customers may request refunds within 30 days "
    "of purchase. Refunds require approval from the account manager."
)


def _ingest(client, key, title, content):
    resp = client.post(
        "/v1/knowledge/documents",
        json={"title": title, "content": content},
        headers={"X-Helios-API-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Groundedness evaluator
# ---------------------------------------------------------------------------

def _pseudo_trace(output, context_chunks, citations):
    return DecisionTrace(
        tenant_id="t",
        application_id="a",
        task_type="answer",
        risk_level="low",
        input_payload={},
        request_payload={
            "retrieved_context": [{"content": c} for c in context_chunks]
        },
        model_provider="mock",
        model_id="m",
        output_text=output,
        citations=citations,
        tool_calls=[],
        cost_usd=0.0,
        latency_ms=10,
        status="success",
    )


def test_groundedness_supported_claims_pass():
    trace = _pseudo_trace(
        "Enterprise customers may request refunds within 30 days of purchase.",
        [REFUND_DOC],
        citations=[{"index": 1}],
    )
    scores = default_pipeline().run(trace)
    g = scores["groundedness"]
    assert g["passed"] is True
    assert g["score"] == 1.0
    assert g["details"]["hallucination_risk"] < 0.5


def test_groundedness_fabricated_claims_fail():
    trace = _pseudo_trace(
        "The moon landing was faked in a studio. Quantum computers cure cancer.",
        [REFUND_DOC],
        citations=[],
    )
    scores = default_pipeline().run(trace)
    g = scores["groundedness"]
    assert g["passed"] is False
    assert g["details"]["hallucination_risk"] >= 0.5


def test_groundedness_skipped_without_context():
    trace = _pseudo_trace("Anything at all.", [], citations=[])
    trace.request_payload = {}
    scores = default_pipeline().run(trace)
    assert scores["groundedness"]["details"].get("skipped")


# ---------------------------------------------------------------------------
# Review queue + feedback loop
# ---------------------------------------------------------------------------

def test_negative_feedback_escalates_to_review_queue(client, api_key):
    resp = client.post(
        "/v1/ai/complete",
        json={"input": "give me an answer"},
        headers={"X-Helios-API-Key": api_key},
    )
    trace_id = resp.json()["trace_id"]

    fb = client.post(
        f"/v1/traces/{trace_id}/feedback",
        json={"rating": "down", "comment": "wrong answer"},
        headers={"X-Helios-API-Key": api_key},
    )
    assert fb.status_code == 200

    queue = client.get(
        "/v1/review/queue", headers={"X-Helios-API-Key": api_key}
    ).json()
    matching = [i for i in queue if i["trace_id"] == trace_id]
    assert matching and matching[0]["reason"] == "negative_user_feedback"

    # Resolve it.
    item_id = matching[0]["id"]
    resolved = client.post(
        f"/v1/review/{item_id}/resolve",
        json={"verdict": "reject", "notes": "confirmed bad"},
        headers={"X-Helios-API-Key": api_key},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["resolution"]["verdict"] == "reject"


def test_feedback_stored_on_trace(client, api_key):
    resp = client.post(
        "/v1/ai/complete",
        json={"input": "another one"},
        headers={"X-Helios-API-Key": api_key},
    )
    trace_id = resp.json()["trace_id"]
    client.post(
        f"/v1/traces/{trace_id}/feedback",
        json={"rating": "up", "outcome": "ticket_resolved"},
        headers={"X-Helios-API-Key": api_key},
    )
    trace = client.get(
        f"/v1/traces/{trace_id}", headers={"X-Helios-API-Key": api_key}
    ).json()
    assert trace["feedback"]["rating"] == "up"
    assert trace["feedback"]["outcome"] == "ticket_resolved"


# ---------------------------------------------------------------------------
# Forge: datasets from production traces
# ---------------------------------------------------------------------------

def test_build_and_export_failure_dataset(client, api_key):
    # Produce a guaranteed failure: blocked high-risk PII request.
    blocked = client.post(
        "/v1/ai/complete",
        json={"input": "SSN 123-45-6789 please summarize", "risk_level": "high"},
        headers={"X-Helios-API-Key": api_key},
    )
    assert blocked.status_code == 403

    built = client.post(
        "/v1/datasets/build",
        json={"name": "failure-cases", "source": "failures", "limit": 50},
        headers={"X-Helios-API-Key": api_key},
    )
    assert built.status_code == 201, built.text
    ds = built.json()
    assert ds["version"] == 1
    assert ds["item_count"] >= 1

    # Version increments with lineage on rebuild.
    rebuilt = client.post(
        "/v1/datasets/build",
        json={"name": "failure-cases", "source": "failures", "limit": 50},
        headers={"X-Helios-API-Key": api_key},
    ).json()
    assert rebuilt["version"] == 2

    # JSONL export.
    export = client.get(
        f"/v1/datasets/{ds['id']}/export", headers={"X-Helios-API-Key": api_key}
    )
    assert export.status_code == 200
    lines = export.text.strip().split("\n")
    assert len(lines) == ds["item_count"]
    import json as _json

    row = _json.loads(lines[0])
    assert {"input", "labels", "dataset"} <= set(row)
    assert row["dataset"] == "failure-cases:v1"


# ---------------------------------------------------------------------------
# Worker escalation on failed evaluation
# ---------------------------------------------------------------------------

def test_worker_escalates_failed_trace_to_review(client, api_key):
    # A trace with empty-ish output can't be produced via mock easily, so use
    # a refusal output path: ask mock to echo a refusal phrase.
    resp = client.post(
        "/v1/ai/complete",
        json={"input": "I'm sorry, but I can't help with that."},
        headers={"X-Helios-API-Key": api_key},
    )
    trace_id = resp.json()["trace_id"]

    asyncio.run(process_batch(batch_size=50))

    db = SessionLocal()
    try:
        trace = db.get(DecisionTrace, trace_id)
        assert trace.evaluation_scores["refusal_detection"]["passed"] is False
        items = (
            db.query(ReviewItem).filter(ReviewItem.trace_id == trace_id).all()
        )
        assert items, "failed evaluation should create a review item"
        assert "refusal_detection" in items[0].reason
    finally:
        db.close()
