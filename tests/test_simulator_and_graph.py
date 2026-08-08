from helios.graph import extract_entities, infer_type


def _ingest(client, key, title, content):
    resp = client.post(
        "/v1/knowledge/documents",
        json={"title": title, "content": content},
        headers={"X-Helios-API-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

def test_simulation_replay_produces_report(client, api_key):
    # Seed some successful traffic.
    for i in range(3):
        client.post(
            "/v1/ai/complete",
            json={"input": f"replay me {i}"},
            headers={"X-Helios-API-Key": api_key},
        )

    run = client.post(
        "/v1/simulations/run",
        json={"candidate_provider": "mock", "limit": 10},
        headers={"X-Helios-API-Key": api_key},
    )
    assert run.status_code == 201, run.text
    report = run.json()["report"]
    assert report["replayed"] >= 3
    assert "candidate_failure_rate" in report
    assert "baseline_failure_rate" in report
    assert report["recommendation"] in {"canary_1_percent", "do_not_deploy"}

    # Fetch by id.
    fetched = client.get(
        f"/v1/simulations/{run.json()['id']}", headers={"X-Helios-API-Key": api_key}
    )
    assert fetched.status_code == 200
    assert fetched.json()["report"] == report


def test_simulation_with_no_traffic_422(client, other_tenant_api_key):
    # globex may have traces from other tests; filter to a task type it never used.
    resp = client.post(
        "/v1/simulations/run",
        json={"candidate_provider": "mock", "task_type": "never-used-task"},
        headers={"X-Helios-API-Key": other_tenant_api_key},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Knowledge graph MVP
# ---------------------------------------------------------------------------

def test_extract_entities_and_types():
    text = (
        "The Refund Policy applies to Enterprise Customers. "
        "The Payment API depends on the Auth Service."
    )
    entities = dict(extract_entities(text))
    assert "Refund Policy" in entities
    assert entities["Refund Policy"] == "Policy"
    assert entities["Payment API"] == "Service"
    assert infer_type("Checkout Outage") == "Incident"


def test_ingestion_builds_graph_with_provenance(client, api_key):
    doc = _ingest(
        client,
        api_key,
        "Payments Runbook",
        "The Payment API depends on the Auth Service. See the Refund Policy.",
    )

    entities = client.get(
        "/v1/knowledge/entities", headers={"X-Helios-API-Key": api_key}
    ).json()
    names = {e["name"] for e in entities}
    assert {"Payment API", "Auth Service", "Refund Policy"} <= names

    payment_api = next(e for e in entities if e["name"] == "Payment API")
    docs = client.get(
        f"/v1/knowledge/entities/{payment_api['id']}/documents",
        headers={"X-Helios-API-Key": api_key},
    ).json()
    titles = {d["title"] for d in docs["documents"]}
    assert doc["title"] in titles
    assert all(d["relationship"] == "mentioned_in" for d in docs["documents"])


def test_entity_dedup_across_documents(client, api_key):
    _ingest(client, api_key, "Doc One", "The Billing Service handles invoices.")
    _ingest(client, api_key, "Doc Two", "The Billing Service also emits events.")

    entities = client.get(
        "/v1/knowledge/entities?limit=500", headers={"X-Helios-API-Key": api_key}
    ).json()
    billing = [e for e in entities if e["name"] == "Billing Service"]
    assert len(billing) == 1, "entity must be deduplicated across documents"

    docs = client.get(
        f"/v1/knowledge/entities/{billing[0]['id']}/documents",
        headers={"X-Helios-API-Key": api_key},
    ).json()
    assert len(docs["documents"]) == 2
