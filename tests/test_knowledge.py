import asyncio

from helios.chunking import chunk_text
from helios.config import settings
from helios.db import SessionLocal
from helios.models import DecisionTrace
from helios.retrieval import search


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def test_chunk_text_short_input_single_chunk():
    assert chunk_text("hello world", size=500, overlap=50) == ["hello world"]


def test_chunk_text_empty_input():
    assert chunk_text("   ", size=500, overlap=50) == []


def test_chunk_text_splits_with_overlap():
    text = "a" * 1200
    chunks = chunk_text(text, size=500, overlap=50)
    # step = 450 -> starts at 0, 450, 900 -> 3 chunks
    assert len(chunks) == 3
    assert len(chunks[0]) == 500
    assert len(chunks[1]) == 500
    assert len(chunks[2]) == 300
    # Overlap: last 50 chars of chunk N == first 50 chars of chunk N+1
    assert chunks[0][-50:] == chunks[1][:50]


def test_chunk_text_rejects_bad_params():
    import pytest

    with pytest.raises(ValueError):
        chunk_text("x", size=0)
    with pytest.raises(ValueError):
        chunk_text("x", size=100, overlap=100)


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

REFUND_DOC = (
    "Refund Policy. Enterprise customers may request refunds within 30 days "
    "of purchase. Refunds for enterprise plans require approval from the "
    "account manager. Refund requests must include the original invoice."
)

KUBERNETES_DOC = (
    "Deployment Guide. Our services run on Kubernetes clusters. Pods are "
    "scheduled across nodes and horizontal autoscaling adjusts replicas "
    "based on CPU utilization metrics."
)


def _ingest(client, key: str, title: str, content: str) -> dict:
    resp = client.post(
        "/v1/knowledge/documents",
        json={"title": title, "content": content},
        headers={"X-Helios-API-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_ingest_document_creates_chunks(client, api_key):
    doc = _ingest(client, api_key, "Refund Policy", REFUND_DOC)
    assert doc["title"] == "Refund Policy"
    assert doc["chunk_count"] >= 1

    # Long content produces multiple chunks.
    long_doc = _ingest(client, api_key, "Long Doc", "word " * 500)  # 2500 chars
    assert long_doc["chunk_count"] > 1

    listing = client.get(
        "/v1/knowledge/documents", headers={"X-Helios-API-Key": api_key}
    ).json()
    titles = {d["title"] for d in listing}
    assert {"Refund Policy", "Long Doc"} <= titles


def test_ingest_requires_auth(client):
    resp = client.post(
        "/v1/knowledge/documents", json={"title": "t", "content": "c"}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Retrieval (SQLite Python-cosine fallback path)
# ---------------------------------------------------------------------------

def _tenant_id_for(client, key: str) -> str:
    """Grab the tenant id via a trace (avoids poking the DB directly)."""
    resp = client.post(
        "/v1/ai/complete",
        json={"input": "whoami"},
        headers={"X-Helios-API-Key": key},
    )
    trace_id = resp.json()["trace_id"]
    trace = client.get(
        f"/v1/traces/{trace_id}", headers={"X-Helios-API-Key": key}
    ).json()
    return trace["tenant_id"]


def test_search_ranks_relevant_document_first(client, api_key):
    _ingest(client, api_key, "Refund Policy", REFUND_DOC)
    _ingest(client, api_key, "Deployment Guide", KUBERNETES_DOC)
    tenant_id = _tenant_id_for(client, api_key)

    db = SessionLocal()
    try:
        results = asyncio.run(
            search(db, tenant_id, "what is the refund policy?", settings, top_k=3)
        )
    finally:
        db.close()

    assert results, "expected at least one retrieved chunk"
    # The refund chunk must outrank the kubernetes chunk for a refund query.
    assert results[0].document_title == "Refund Policy"
    assert results[0].score > 0


def test_search_is_tenant_isolated(client, api_key, other_tenant_api_key):
    # acme ingests a secret; globex must NEVER retrieve it.
    _ingest(client, api_key, "Acme Secret Plan", "The secret acme launch code is 42.")
    globex_tenant = _tenant_id_for(client, other_tenant_api_key)

    db = SessionLocal()
    try:
        results = asyncio.run(
            search(db, globex_tenant, "secret acme launch code", settings, top_k=5)
        )
    finally:
        db.close()

    assert all(r.document_title != "Acme Secret Plan" for r in results), (
        "CROSS-TENANT LEAK: globex retrieved acme's document"
    )


# ---------------------------------------------------------------------------
# Gateway integration (RAG end-to-end via mock provider + mock embeddings)
# ---------------------------------------------------------------------------

def test_complete_with_knowledge_base_grounds_and_cites(client, api_key):
    _ingest(client, api_key, "Refund Policy", REFUND_DOC)

    resp = client.post(
        "/v1/ai/complete",
        json={"input": "What is the refund window?", "use_knowledge_base": True},
        headers={"X-Helios-API-Key": api_key},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Mock provider echoes its prompt -> proves the context was injected.
    assert "Context:" in body["output"]
    assert "Refund Policy" in body["output"]
    assert "What is the refund window?" in body["output"]

    # Citations returned to caller.
    assert body["citations"], "expected citations when use_knowledge_base=true"
    top = body["citations"][0]
    assert {"index", "chunk_id", "document_id", "title", "score"} <= set(top)
    assert top["title"] == "Refund Policy"

    # Citations + retrieved context persisted on the trace.
    trace = client.get(
        f"/v1/traces/{body['trace_id']}", headers={"X-Helios-API-Key": api_key}
    ).json()
    assert trace["citations"] == body["citations"]
    assert trace["request_payload"]["retrieved_context"]


def test_complete_without_flag_skips_retrieval(client, api_key):
    _ingest(client, api_key, "Refund Policy", REFUND_DOC)

    resp = client.post(
        "/v1/ai/complete",
        json={"input": "What is the refund window?"},
        headers={"X-Helios-API-Key": api_key},
    )
    body = resp.json()
    assert "Context:" not in body["output"]
    assert body["citations"] == []


def test_complete_with_empty_knowledge_base_still_works(client, other_tenant_api_key):
    # globex has no documents; the flag should not break the request.
    resp = client.post(
        "/v1/ai/complete",
        json={"input": "anything at all", "use_knowledge_base": True},
        headers={"X-Helios-API-Key": other_tenant_api_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["citations"] == []
    assert "Context:" not in body["output"]
