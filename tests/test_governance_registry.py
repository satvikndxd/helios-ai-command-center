"""
IDENTITY + POLICY plane tests: AI System Registry and Model Registry.

System registration, ownership, lifecycle, risk classification, versioned
updates, tenant isolation; model approval lifecycle, provider/data/env
restrictions, unknown-model-not-approved, model-use gating in the runtime.
"""

import json

import pytest


def H(api_key):
    return {"X-Helios-API-Key": api_key}


# --- AI System Registry ----------------------------------------------------


def test_register_and_inspect_system(client, api_key):
    payload = {
        "system_id": "claims-agent",
        "name": "Claims Triage Agent",
        "owner": "claims-platform",
        "purpose": "claims triage",
        "environment": "production",
        "autonomy_level": 3,
        "risk_class": "high",
        "data_classes": ["pii", "confidential"],
    }
    r = client.post("/v1/systems", json=payload, headers=H(api_key))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["system_id"] == "claims-agent"
    assert body["autonomy_level"] == 3
    assert body["autonomy_label"] == "conditional autonomy"
    assert body["lifecycle"] == "active"
    assert body["revisions"][0]["change"] == "registered"

    got = client.get("/v1/systems/claims-agent", headers=H(api_key)).json()
    assert got["owner"] == "claims-platform"
    assert got["risk_class"] == "high"


def test_duplicate_system_rejected(client, api_key):
    client.post("/v1/systems", json={"system_id": "dup-sys"}, headers=H(api_key))
    r = client.post("/v1/systems", json={"system_id": "dup-sys"}, headers=H(api_key))
    assert r.status_code == 422
    assert "already exists" in r.json()["detail"]


def test_system_update_is_versioned_and_audited(client, api_key):
    client.post("/v1/systems", json={"system_id": "vers-sys", "owner": "a"},
                headers=H(api_key))
    r = client.patch("/v1/systems/vers-sys", json={"owner": "b", "risk_class": "critical"},
                     headers=H(api_key))
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 2
    assert body["owner"] == "b"
    last_rev = body["revisions"][-1]
    assert last_rev["changed"]["owner"] == {"from": "a", "to": "b"}

    events = client.get("/v1/systems/vers-sys/events", headers=H(api_key)).json()["events"]
    kinds = [e["name"] for e in events]
    assert "system_registered" in kinds
    assert "system_updated" in kinds


def test_system_lifecycle_archive_blocks_update(client, api_key):
    client.post("/v1/systems", json={"system_id": "arch-sys"}, headers=H(api_key))
    r = client.post("/v1/systems/arch-sys/lifecycle", json={"lifecycle": "archived"},
                    headers=H(api_key))
    assert r.json()["lifecycle"] == "archived"
    r = client.patch("/v1/systems/arch-sys", json={"owner": "x"}, headers=H(api_key))
    assert r.status_code == 422


def test_invalid_autonomy_level_rejected(client, api_key):
    r = client.post("/v1/systems", json={"system_id": "bad", "autonomy_level": 9},
                    headers=H(api_key))
    assert r.status_code == 422


def test_system_tenant_isolation(client, api_key, other_tenant_api_key):
    client.post("/v1/systems", json={"system_id": "tenant-a-sys"}, headers=H(api_key))
    r = client.get("/v1/systems/tenant-a-sys", headers=H(other_tenant_api_key))
    assert r.status_code == 404


def test_system_search(client, api_key):
    client.post("/v1/systems", json={"system_id": "search-billing",
                "purpose": "invoice reconciliation"}, headers=H(api_key))
    r = client.get("/v1/systems?q=invoice", headers=H(api_key)).json()
    assert any(s["system_id"] == "search-billing" for s in r["systems"])


# --- Model Registry --------------------------------------------------------


def _register_model(client, api_key, **over):
    payload = {"provider": "anthropic", "model_id": "claude-x", "version": "17",
               "status": "approved", "allowed_data_classes": ["public", "internal"],
               "allowed_environments": ["dev", "staging", "production"]}
    payload.update(over)
    return client.post("/v1/models", json=payload, headers=H(api_key))


def test_register_model_and_evaluate_public(client, api_key):
    r = _register_model(client, api_key)
    assert r.status_code == 201
    assert r.json()["status"] == "approved"

    ev = client.post("/v1/models/evaluate", json={
        "provider": "anthropic", "model_id": "claude-x",
        "data_classes": ["public"], "environment": "production"},
        headers=H(api_key)).json()
    assert ev["allowed"] is True
    assert ev["status"] == "allowed"


def test_unknown_model_not_approved(client, api_key):
    ev = client.post("/v1/models/evaluate", json={
        "provider": "who", "model_id": "ghost"}, headers=H(api_key)).json()
    assert ev["allowed"] is False
    assert ev["status"] == "not_approved"
    assert "not in the registry" in ev["reasons"][0]


def test_model_denied_for_uncleared_data_class(client, api_key):
    _register_model(client, api_key, model_id="claude-narrow",
                    allowed_data_classes=["public"])
    ev = client.post("/v1/models/evaluate", json={
        "provider": "anthropic", "model_id": "claude-narrow",
        "data_classes": ["confidential"], "environment": "dev"},
        headers=H(api_key)).json()
    assert ev["allowed"] is False
    assert ev["status"] == "denied"
    assert "confidential" in ev["reasons"][0]


def test_model_denied_for_environment(client, api_key):
    _register_model(client, api_key, model_id="claude-devonly",
                    allowed_environments=["dev"])
    ev = client.post("/v1/models/evaluate", json={
        "provider": "anthropic", "model_id": "claude-devonly",
        "environment": "production"}, headers=H(api_key)).json()
    assert ev["allowed"] is False
    assert ev["status"] == "denied"


def test_proposed_model_is_not_approved(client, api_key):
    _register_model(client, api_key, model_id="claude-proposed", status="proposed")
    ev = client.post("/v1/models/evaluate", json={
        "provider": "anthropic", "model_id": "claude-proposed"},
        headers=H(api_key)).json()
    assert ev["allowed"] is False
    assert ev["status"] == "not_approved"


def test_model_status_change_emits_event(client, api_key):
    r = _register_model(client, api_key, model_id="claude-lifecycle", status="proposed")
    model_pk = r.json()["id"]
    client.post(f"/v1/models/{model_pk}/status", json={"status": "approved"},
                headers=H(api_key))
    got = client.get(f"/v1/models/{model_pk}", headers=H(api_key)).json()
    assert got["status"] == "approved"
    assert got["decided_by"]


# --- model governance gate in the runtime ----------------------------------


def _create_system(client, api_key, **over):
    payload = {"system_id": "gated-sys", "environment": "production",
               "autonomy_level": 2, "data_classes": []}
    payload.update(over)
    return client.post("/v1/systems", json=payload, headers=H(api_key)).json()


def test_runtime_blocks_unapproved_model(client, api_key):
    _create_system(client, api_key, system_id="blocks-model")
    # session bound to the system, using an UNREGISTERED provider/model
    session = client.post("/v1/agent/sessions", json={
        "name": "t", "system_id": "blocks-model",
        "model_provider": "scripted", "model_id": "unregistered-model"},
        headers=H(api_key)).json()
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages",
                      json={"content": "hi\nSCRIPT:[{\"type\":\"final\",\"content\":\"x\"}]"},
                      headers=H(api_key)).json()
    assert run["state"] == "blocked"
    assert run["error"]["kind"] == "model_governance"


def test_runtime_allows_approved_model_and_records_decision(client, api_key):
    _register_model(client, api_key, provider="scripted", model_id="approved-scripted",
                    allowed_data_classes=["public", "internal"],
                    allowed_environments=["dev", "staging", "production"])
    _create_system(client, api_key, system_id="allows-model", environment="dev")
    session = client.post("/v1/agent/sessions", json={
        "name": "t", "system_id": "allows-model",
        "model_provider": "scripted", "model_id": "approved-scripted"},
        headers=H(api_key)).json()
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages",
                      json={"content": "hi\nSCRIPT:[{\"type\":\"final\",\"content\":\"ok\"}]"},
                      headers=H(api_key)).json()
    assert run["state"] == "completed"

    # a model_use decision record exists
    decisions = client.get(f"/v1/decisions?system_id=allows-model",
                           headers=H(api_key)).json()["decisions"]
    assert any(d["kind"] == "model_use" and d["decision"] == "allow" for d in decisions)


def test_session_inherits_system_environment_and_autonomy(client, api_key):
    _create_system(client, api_key, system_id="inherit-sys",
                   environment="production", autonomy_level=3)
    session = client.post("/v1/agent/sessions", json={
        "name": "t", "system_id": "inherit-sys", "model_provider": "scripted"},
        headers=H(api_key)).json()
    assert session["environment"] == "production"
    assert session["autonomy_level"] == 3
    assert session["autonomy"] == "autonomous"
