"""
Phase 3 — Model Registry + model governance tests (IDENTITY/POLICY planes).

Covers: registration, approval lifecycle (accountable identity required),
provider/data/environment compatibility decisions, unknown-model deny for
governed systems, legacy compatibility for unbound sessions, runtime preflight
(denied model calls block the run with recorded evidence), and governance
events for approval changes.
"""

import json

from helios.db import SessionLocal
from helios.models import TraceEvent


def _headers(api_key):
    return {"X-Helios-API-Key": api_key}


def _register_system(client, api_key, system_id="model-test-agent", **overrides):
    payload = {
        "system_id": system_id,
        "name": "Model Test Agent",
        "owner": "platform",
        "environment": "production",
        "autonomy_level": 3,
        "risk_class": "HIGH",
        **overrides,
    }
    response = client.post("/v1/systems", json=payload, headers=_headers(api_key))
    assert response.status_code == 201, response.text
    return response.json()


def _register_model(client, api_key, **overrides):
    payload = {
        "provider": "anthropic",
        "model_id": "claude-x",
        "version": "17",
        "capabilities": ["chat", "tools"],
        "region": "us-east",
        "pricing": {"input_per_1k_usd": 0.003, "output_per_1k_usd": 0.015},
        "risk_class": "MEDIUM",
        **overrides,
    }
    return client.post("/v1/models", json=payload, headers=_headers(api_key))


# --- registry CRUD + approval lifecycle -------------------------------------


def test_register_model_defaults_to_pending(client, api_key):
    response = _register_model(client, api_key)
    assert response.status_code == 201, response.text
    model = response.json()
    assert model["approval_status"] == "pending"
    assert model["provider"] == "anthropic"
    assert model["version"] == "17"
    assert model["pricing"]["input_per_1k_usd"] == 0.003

    # exact duplicate rejected
    assert _register_model(client, api_key).status_code == 400

    # a new version of the same model is a distinct asset
    v18 = _register_model(client, api_key, version="18")
    assert v18.status_code == 201


def test_approval_requires_accountable_identity(client, api_key):
    model = _register_model(client, api_key, model_id="claude-approval").json()

    # registering directly as approved without approved_by is refused
    response = _register_model(client, api_key, model_id="claude-y",
                               approval_status="approved")
    assert response.status_code == 400
    assert "approved_by" in response.json()["detail"]

    # approve endpoint requires `by`
    response = client.post(f"/v1/models/{model['id']}/approve", json={},
                           headers=_headers(api_key))
    assert response.status_code in (400, 422)

    response = client.post(f"/v1/models/{model['id']}/approve",
                           json={"by": "security-lead"},
                           headers=_headers(api_key))
    assert response.status_code == 200
    approved = response.json()
    assert approved["approval_status"] == "approved"
    assert approved["approved_by"] == "security-lead"
    assert approved["approved_at"]


def test_approval_change_is_recorded_as_governance_event(client, api_key):
    model = _register_model(client, api_key, model_id="claude-events").json()
    client.post(f"/v1/models/{model['id']}/approve", json={"by": "cto"},
                headers=_headers(api_key))
    client.post(f"/v1/models/{model['id']}/deprecate",
                json={"by": "cto", "reason": "replaced by v18"},
                headers=_headers(api_key))

    db = SessionLocal()
    try:
        events = (
            db.query(TraceEvent)
            .filter(TraceEvent.event_type == "model_governance")
            .order_by(TraceEvent.seq)
            .all()
        )
        payloads = [e.payload for e in events
                    if e.payload.get("model_asset_id") == model["id"]]
        kinds = [p["event"] for p in payloads]
        assert kinds == ["model_registered", "model_approval_changed",
                         "model_approval_changed"]
        assert payloads[1]["from"] == "pending" and payloads[1]["to"] == "approved"
        assert payloads[1]["by"] == "cto"
        assert payloads[2]["to"] == "deprecated"
        assert payloads[2]["reason"] == "replaced by v18"
    finally:
        db.close()


def test_identity_fields_immutable(client, api_key):
    model = _register_model(client, api_key, model_id="claude-immutable").json()
    response = client.patch(f"/v1/models/{model['id']}",
                            json={"provider": "openai"},
                            headers=_headers(api_key))
    # provider is not a mutable field on the patch schema: nothing to update
    assert response.status_code == 400
    assert "no fields" in response.json()["detail"]

    from helios.governance import models_registry
    db = SessionLocal()
    try:
        from helios.models import ModelAsset
        asset = db.get(ModelAsset, model["id"])
        try:
            models_registry.update_model(db, asset, {"model_id": "other"})
            assert False, "identity mutation must raise"
        except models_registry.ModelRegistryError as exc:
            assert "immutable" in str(exc)
        # metadata updates work
        asset = models_registry.update_model(
            db, asset, {"allowed_data_classes": ["public", "internal"]})
        assert asset.allowed_data_classes == ["INTERNAL", "PUBLIC"]
    finally:
        db.close()


# --- governance compatibility decisions --------------------------------------


def test_check_unknown_model_denies_for_governed_allows_flagged_for_legacy(
        client, api_key):
    governed = client.post(
        "/v1/models/check",
        json={"provider": "openai", "model_id": "gpt-mystery",
              "environment": "production", "governed": True},
        headers=_headers(api_key)).json()
    assert governed["decision"] == "deny"
    assert any("not registered" in c["reason"] for c in governed["checks"])

    legacy = client.post(
        "/v1/models/check",
        json={"provider": "openai", "model_id": "gpt-mystery",
              "environment": "production", "governed": False},
        headers=_headers(api_key)).json()
    assert legacy["decision"] == "allow"
    failed = [c for c in legacy["checks"] if not c["passed"]]
    assert failed and "legacy" in failed[0]["reason"]


def test_check_data_and_environment_compatibility(client, api_key):
    model = _register_model(
        client, api_key, model_id="claude-scoped",
        allowed_data_classes=["PUBLIC", "INTERNAL"],
        allowed_environments=["staging"],
    ).json()
    client.post(f"/v1/models/{model['id']}/approve", json={"by": "sec"},
                headers=_headers(api_key))

    # PUBLIC + staging -> allowed
    ok = client.post("/v1/models/check", json={
        "provider": "anthropic", "model_id": "claude-scoped",
        "data_classes": ["PUBLIC"], "environment": "staging"},
        headers=_headers(api_key)).json()
    assert ok["decision"] == "allow"

    # CONFIDENTIAL data -> denied (above ceiling)
    denied = client.post("/v1/models/check", json={
        "provider": "anthropic", "model_id": "claude-scoped",
        "data_classes": ["CONFIDENTIAL"], "environment": "staging"},
        headers=_headers(api_key)).json()
    assert denied["decision"] == "deny"
    assert any("ABOVE" in c["reason"] for c in denied["checks"])

    # production environment -> denied
    denied = client.post("/v1/models/check", json={
        "provider": "anthropic", "model_id": "claude-scoped",
        "data_classes": ["PUBLIC"], "environment": "production"},
        headers=_headers(api_key)).json()
    assert denied["decision"] == "deny"
    assert any(c["check"] == "environment" and not c["passed"]
               for c in denied["checks"])


def test_pending_and_deprecated_models_denied(client, api_key):
    pending = _register_model(client, api_key, model_id="claude-pending").json()
    result = client.post("/v1/models/check", json={
        "provider": "anthropic", "model_id": "claude-pending"},
        headers=_headers(api_key)).json()
    assert result["decision"] == "deny"
    assert any("pending" in c["reason"] for c in result["checks"])

    dep = _register_model(client, api_key, model_id="claude-dep",
                          approved_by="sec", approval_status="approved").json()
    client.post(f"/v1/models/{dep['id']}/deprecate",
                json={"by": "sec", "reason": "eol"}, headers=_headers(api_key))
    result = client.post("/v1/models/check", json={
        "provider": "anthropic", "model_id": "claude-dep"},
        headers=_headers(api_key)).json()
    assert result["decision"] == "deny"
    assert any("deprecated" in c["reason"] for c in result["checks"])


def test_lookup_endpoint(client, api_key):
    _register_model(client, api_key, model_id="claude-lookup")
    found = client.get("/v1/models/lookup",
                       params={"provider": "anthropic", "model_id": "claude-lookup",
                               "version": "17"},
                       headers=_headers(api_key))
    assert found.status_code == 200
    missing = client.get("/v1/models/lookup",
                         params={"provider": "anthropic", "model_id": "nope"},
                         headers=_headers(api_key))
    assert missing.status_code == 404
    assert "NOT APPROVED" in missing.json()["detail"]


def test_model_tenant_isolation(client, api_key, other_tenant_api_key):
    model = _register_model(client, api_key, model_id="claude-isolated").json()
    assert client.get(f"/v1/models/{model['id']}/approve",
                      headers=_headers(other_tenant_api_key)).status_code in (404, 405)
    listing = client.get("/v1/models", headers=_headers(other_tenant_api_key)).json()
    assert model["id"] not in {m["id"] for m in listing["models"]}
    check = client.post("/v1/models/check", json={
        "provider": "anthropic", "model_id": "claude-isolated", "governed": True},
        headers=_headers(other_tenant_api_key)).json()
    assert check["decision"] == "deny"  # other tenant has no such model


# --- runtime preflight ---------------------------------------------------------


def _script(steps):
    return "run this plan\nSCRIPT:" + json.dumps(steps)


def test_governed_session_denies_unregistered_model(client, api_key):
    system = _register_system(client, api_key, system_id="preflight-deny")
    session = client.post("/v1/agent/sessions", json={
        "name": "s", "model_provider": "scripted", "model_id": "model-x",
        "system_id": system["system_id"], "environment": "production",
    }, headers=_headers(api_key)).json()

    run = client.post(
        f"/v1/agent/sessions/{session['id']}/messages",
        json={"content": _script([{"type": "final", "content": "hi"}])},
        headers=_headers(api_key)).json()

    assert run["state"] == "blocked"
    assert "model governance denied" in run["error"]["message"]

    events = client.get(f"/v1/agent/runs/{run['id']}/events",
                        headers=_headers(api_key)).json()
    rows = events["events"] if isinstance(events, dict) else events
    preflights = [e for e in rows if e["name"].startswith("model_preflight")]
    assert preflights and preflights[0]["status"] == "denied"
    outcomes = [e for e in rows if e["name"] == "model_call_denied"]
    assert outcomes and outcomes[0]["status"] == "blocked"
    assert any("unknown model" in r for r in outcomes[0]["payload"]["reasons"])


def test_governed_session_allows_approved_model(client, api_key):
    system = _register_system(client, api_key, system_id="preflight-allow")
    model = _register_model(client, api_key, provider="scripted",
                            model_id="model-x", version="17",
                            approved_by="sec", approval_status="approved").json()
    session = client.post("/v1/agent/sessions", json={
        "name": "s", "model_provider": "scripted", "model_id": "model-x",
        "system_id": system["system_id"], "environment": "production",
    }, headers=_headers(api_key)).json()

    run = client.post(
        f"/v1/agent/sessions/{session['id']}/messages",
        json={"content": _script([{"type": "final", "content": "done"}])},
        headers=_headers(api_key)).json()
    assert run["state"] == "completed", run

    events = client.get(f"/v1/agent/runs/{run['id']}/events",
                        headers=_headers(api_key)).json()
    rows = events["events"] if isinstance(events, dict) else events
    preflight = [e for e in rows if e["name"].startswith("model_preflight")][0]
    assert preflight["status"] == "ok"
    assert preflight["payload"]["decision"] == "allow"
    model_calls = [e for e in rows if e["event_type"] == "model_call"]
    assert model_calls and model_calls[0]["payload"]["preflight"]["decision"] == "allow"


def test_governed_session_denies_data_ceiling_violation(client, api_key):
    """System declares CONFIDENTIAL data; model approved for PUBLIC only -> deny."""
    system = _register_system(client, api_key, system_id="preflight-data",
                              data_classes=["CONFIDENTIAL"])
    _register_model(client, api_key, provider="scripted", model_id="cheap-model",
                    allowed_data_classes=["PUBLIC"],
                    approved_by="sec", approval_status="approved")
    session = client.post("/v1/agent/sessions", json={
        "name": "s", "model_provider": "scripted", "model_id": "cheap-model",
        "system_id": system["system_id"], "environment": "production",
    }, headers=_headers(api_key)).json()

    run = client.post(
        f"/v1/agent/sessions/{session['id']}/messages",
        json={"content": _script([{"type": "final", "content": "done"}])},
        headers=_headers(api_key)).json()
    assert run["state"] == "blocked"

    events = client.get(f"/v1/agent/runs/{run['id']}/events",
                        headers=_headers(api_key)).json()
    rows = events["events"] if isinstance(events, dict) else events
    preflight = [e for e in rows if e["name"].startswith("model_preflight")][0]
    data_checks = [c for c in preflight["payload"]["checks"]
                   if c["check"] == "data_classification"]
    assert data_checks and not data_checks[0]["passed"]
    assert "CONFIDENTIAL" in data_checks[0]["reason"]
    # classification evidence: declared classes are recorded
    assert "CONFIDENTIAL" in preflight["payload"]["data_classification"]["declared"]


def test_detected_pii_raises_effective_classification(client, api_key):
    """PII in the transcript is detected and can trip the model data ceiling."""
    system = _register_system(client, api_key, system_id="preflight-pii",
                              data_classes=["PUBLIC"])
    _register_model(client, api_key, provider="scripted", model_id="public-model",
                    allowed_data_classes=["PUBLIC"],
                    approved_by="sec", approval_status="approved")
    session = client.post("/v1/agent/sessions", json={
        "name": "s", "model_provider": "scripted", "model_id": "public-model",
        "system_id": system["system_id"], "environment": "production",
    }, headers=_headers(api_key)).json()

    # user message contains an SSN -> detected PII -> above PUBLIC ceiling
    content = ("handle claim for ssn 123-45-6789\nSCRIPT:"
               + json.dumps([{"type": "final", "content": "ok"}]))
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages",
                      json={"content": content}, headers=_headers(api_key)).json()
    assert run["state"] == "blocked"

    events = client.get(f"/v1/agent/runs/{run['id']}/events",
                        headers=_headers(api_key)).json()
    rows = events["events"] if isinstance(events, dict) else events
    preflight = [e for e in rows if e["name"].startswith("model_preflight")][0]
    classification = preflight["payload"]["data_classification"]
    assert "PII" in classification["detected"]
    assert classification["pii_counts"].get("ssn") == 1
    # the raw SSN must NOT be persisted in the classification evidence
    assert "123-45-6789" not in json.dumps(classification)


def test_legacy_unbound_session_unregistered_model_still_runs(client, api_key):
    """V1 compatibility: no system binding + unknown model => runs, flagged."""
    session = client.post("/v1/agent/sessions", json={
        "name": "legacy", "model_provider": "scripted", "model_id": "whatever-9",
        "environment": "dev",
    }, headers=_headers(api_key)).json()
    run = client.post(
        f"/v1/agent/sessions/{session['id']}/messages",
        json={"content": _script([{"type": "final", "content": "works"}])},
        headers=_headers(api_key)).json()
    assert run["state"] == "completed"
    assert run["output_text"] == "works"

    events = client.get(f"/v1/agent/runs/{run['id']}/events",
                        headers=_headers(api_key)).json()
    rows = events["events"] if isinstance(events, dict) else events
    preflight = [e for e in rows if e["name"].startswith("model_preflight")][0]
    assert preflight["status"] == "ok"
    assert any(not c["passed"] and "legacy" in c["reason"]
               for c in preflight["payload"]["checks"])
