HEADERS_JSON = {"Content-Type": "application/json"}


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_complete_requires_api_key(client):
    resp = client.post("/v1/ai/complete", json={"input": "hi"})
    assert resp.status_code == 401


def test_complete_rejects_bad_api_key(client):
    resp = client.post(
        "/v1/ai/complete",
        json={"input": "hi"},
        headers={"X-Helios-API-Key": "nope"},
    )
    assert resp.status_code == 403


def test_complete_mock_and_trace_roundtrip(client, api_key):
    # 1. Make a completion via the mock provider.
    resp = client.post(
        "/v1/ai/complete",
        json={"input": "Hello Project Helios"},
        headers={"X-Helios-API-Key": api_key},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["output"].startswith("[mock:")
    assert "Hello Project Helios" in body["output"]
    assert body["model"] == {"provider": "mock", "model_id": "mock-model-1"}
    assert body["cost_usd"] == 0.0
    assert body["latency_ms"] >= 0
    assert body["policy"]["action"] == "allow"

    trace_id = body["trace_id"]

    # 2. Fetch the exact trace back.
    resp2 = client.get(
        f"/v1/traces/{trace_id}",
        headers={"X-Helios-API-Key": api_key},
    )
    assert resp2.status_code == 200, resp2.text
    trace = resp2.json()
    assert trace["id"] == trace_id
    assert trace["model_provider"] == "mock"
    assert trace["status"] == "success"
    assert trace["input_payload"]["input"] == "Hello Project Helios"

    # 3. It should appear in the tenant's trace list.
    resp3 = client.get("/v1/traces", headers={"X-Helios-API-Key": api_key})
    assert resp3.status_code == 200
    ids = [t["id"] for t in resp3.json()]
    assert trace_id in ids


def test_trace_not_found(client, api_key):
    resp = client.get(
        "/v1/traces/does-not-exist",
        headers={"X-Helios-API-Key": api_key},
    )
    assert resp.status_code == 404


def test_unsupported_provider_records_error_trace(client, api_key):
    resp = client.post(
        "/v1/ai/complete",
        json={"input": "hi", "provider": "not-a-real-provider"},
        headers={"X-Helios-API-Key": api_key},
    )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    error_trace_id = detail["trace_id"]

    # Even failures are decisions: the error trace must be retrievable.
    resp2 = client.get(
        f"/v1/traces/{error_trace_id}",
        headers={"X-Helios-API-Key": api_key},
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "error"
