"""
Phase 2 — AI System Registry tests (IDENTITY plane).

Covers: registration, strict validation, ownership, lifecycle (incl. blocked
requires a reason), versioning snapshots, search/filters, session binding
(AgentSession.system_id), evidence stamping (TraceEvent.system_id), tenant
isolation, and duplicate rejection.
"""

import json

from helios.db import SessionLocal
from helios.models import AgentSession, TraceEvent


def _headers(api_key):
    return {"X-Helios-API-Key": api_key}


def _register(client, api_key, **overrides):
    payload = {
        "system_id": "claims-agent",
        "name": "Claims Triage Agent",
        "owner": "claims-platform",
        "purpose": "claims triage",
        "environment": "production",
        "autonomy_level": 3,
        "risk_class": "HIGH",
        "models": [{"provider": "anthropic", "model_id": "claude-x", "version": "17"}],
        "tools": ["github.create_pr", "filesystem.read"],
        "data_classes": ["pii", "CONFIDENTIAL", "public"],
        "policies": ["production-autonomy"],
        "oversight": {"production_writes": "approval"},
        **overrides,
    }
    response = client.post("/v1/systems", json=payload, headers=_headers(api_key))
    return response


# --- registration & validation ----------------------------------------------


def test_register_system_normalizes_and_snapshots(client, api_key):
    response = _register(client, api_key, system_id="claims-agent")
    assert response.status_code == 201, response.text
    system = response.json()

    assert system["system_id"] == "claims-agent"
    assert system["owner"] == "claims-platform"
    assert system["environment"] == "production"
    assert system["autonomy_level"] == 3
    assert system["autonomy_label"] == "L3 — CONDITIONAL AUTONOMY"
    assert system["risk_class"] == "HIGH"
    assert system["lifecycle"] == "draft"
    assert system["version"] == 1
    # data classes normalized: canonical spelling, severity-ordered
    assert system["data_classes"] == ["PII", "CONFIDENTIAL", "PUBLIC"]
    assert system["oversight"] == {"production_writes": "approval"}

    # registration produced version 1 snapshot
    versions = client.get("/v1/systems/claims-agent/versions",
                          headers=_headers(api_key)).json()
    assert versions["versions"][0]["version"] == 1
    assert versions["versions"][0]["snapshot"]["owner"] == "claims-platform"
    assert versions["versions"][0]["change_summary"] == "registered"


def test_register_rejects_invalid_fields(client, api_key):
    # unknown data class
    response = _register(client, api_key, system_id="bad-data",
                         data_classes=["TOP_SECRET_ALIEN"])
    assert response.status_code == 400
    assert "data class" in response.json()["detail"]

    # autonomy out of range
    response = _register(client, api_key, system_id="bad-auto", autonomy_level=9)
    assert response.status_code in (400, 422)

    # bad environment
    response = _register(client, api_key, system_id="bad-env", environment="mars")
    assert response.status_code in (400, 422)

    # bad system_id characters
    response = _register(client, api_key, system_id="bad id!")
    assert response.status_code in (400, 422)

    # cannot register directly as blocked
    response = _register(client, api_key, system_id="bad-life", lifecycle="blocked")
    assert response.status_code == 400


def test_duplicate_system_id_rejected(client, api_key):
    assert _register(client, api_key, system_id="dup-agent").status_code == 201
    second = _register(client, api_key, system_id="dup-agent")
    assert second.status_code == 400
    assert "already registered" in second.json()["detail"]


def test_legacy_autonomy_strings_accepted(client, api_key):
    response = _register(client, api_key, system_id="legacy-auto",
                         autonomy_level="autonomous")
    assert response.status_code == 201
    assert response.json()["autonomy_level"] == 4

    response = _register(client, api_key, system_id="legacy-sup",
                         autonomy_level="L2")
    assert response.status_code == 201
    assert response.json()["autonomy_level"] == 2


# --- updates & versioning ----------------------------------------------------


def test_update_bumps_version_and_keeps_history(client, api_key):
    _register(client, api_key, system_id="versioned")

    response = client.patch(
        "/v1/systems/versioned",
        json={"owner": "new-team", "autonomy_level": 4,
              "author": "cto", "change_summary": "ownership transfer"},
        headers=_headers(api_key),
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["owner"] == "new-team"
    assert updated["autonomy_level"] == 4
    assert updated["version"] == 2

    versions = client.get("/v1/systems/versioned/versions",
                          headers=_headers(api_key)).json()["versions"]
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["author"] == "cto"
    assert versions[0]["change_summary"] == "ownership transfer"
    # version 1 still shows the ORIGINAL owner
    assert versions[1]["snapshot"]["owner"] == "claims-platform"

    single = client.get("/v1/systems/versioned/versions/1",
                        headers=_headers(api_key))
    assert single.status_code == 200
    assert single.json()["snapshot"]["autonomy_level"] == 3


def test_archived_systems_are_immutable(client, api_key):
    _register(client, api_key, system_id="to-archive")
    assert client.post("/v1/systems/to-archive/archive",
                       headers=_headers(api_key)).status_code == 200

    response = client.patch("/v1/systems/to-archive", json={"owner": "x"},
                            headers=_headers(api_key))
    assert response.status_code == 400
    assert "archived" in response.json()["detail"]

    # unarchive back to draft re-enables editing (activation of this L3
    # production posture goes through the Phase-4 deployment gate)
    assert client.post("/v1/systems/to-archive/lifecycle",
                       json={"state": "draft"},
                       headers=_headers(api_key)).status_code == 200
    assert client.patch("/v1/systems/to-archive", json={"owner": "y"},
                        headers=_headers(api_key)).status_code == 200


def test_blocked_requires_reason(client, api_key):
    _register(client, api_key, system_id="blockable")

    response = client.post("/v1/systems/blockable/lifecycle",
                           json={"state": "blocked"},
                           headers=_headers(api_key))
    assert response.status_code == 400
    assert "reason" in response.json()["detail"]

    response = client.post(
        "/v1/systems/blockable/lifecycle",
        json={"state": "blocked",
              "reason": {"policy": "production-autonomy", "rule_id": "deny_l5",
                         "detail": "L5 autonomy blocked in production"}},
        headers=_headers(api_key),
    )
    assert response.status_code == 200
    blocked = response.json()
    assert blocked["lifecycle"] == "blocked"
    assert blocked["blocked_reason"]["rule_id"] == "deny_l5"


# --- search -------------------------------------------------------------------


def test_search_and_filters(client, api_key):
    _register(client, api_key, system_id="search-payments", owner="pay-team",
              environment="production", risk_class="CRITICAL")
    _register(client, api_key, system_id="search-docs", owner="docs-team",
              environment="dev", risk_class="LOW")

    all_systems = client.get("/v1/systems", headers=_headers(api_key)).json()
    ids = {s["system_id"] for s in all_systems["systems"]}
    assert {"search-payments", "search-docs"} <= ids

    by_owner = client.get("/v1/systems", params={"owner": "pay-team"},
                          headers=_headers(api_key)).json()
    assert {s["system_id"] for s in by_owner["systems"]} == {"search-payments"}

    by_risk = client.get("/v1/systems", params={"risk_class": "critical"},
                         headers=_headers(api_key)).json()
    assert "search-payments" in {s["system_id"] for s in by_risk["systems"]}

    by_query = client.get("/v1/systems", params={"q": "docs"},
                          headers=_headers(api_key)).json()
    assert "search-docs" in {s["system_id"] for s in by_query["systems"]}


# --- tenant isolation ----------------------------------------------------------


def test_tenant_isolation(client, api_key, other_tenant_api_key):
    _register(client, api_key, system_id="isolated-agent")

    # other tenant cannot see it
    listing = client.get("/v1/systems", headers=_headers(other_tenant_api_key)).json()
    assert "isolated-agent" not in {s["system_id"] for s in listing["systems"]}

    # other tenant cannot fetch or mutate it
    assert client.get("/v1/systems/isolated-agent",
                      headers=_headers(other_tenant_api_key)).status_code == 404
    assert client.patch("/v1/systems/isolated-agent", json={"owner": "evil"},
                        headers=_headers(other_tenant_api_key)).status_code == 404

    # the same system_id MAY exist in another tenant (uniqueness is per-tenant)
    assert _register(client, other_tenant_api_key,
                     system_id="isolated-agent").status_code == 201


# --- session + evidence binding -------------------------------------------------


def test_session_binds_to_registered_system(client, api_key):
    _register(client, api_key, system_id="bound-agent")
    # Phase 3: a governed (system-bound) session may only use registered,
    # approved models — register the scripted model this session will use.
    model = client.post("/v1/models", json={
        "provider": "scripted", "model_id": "mock-model-1",
        "approval_status": "approved", "approved_by": "sec",
    }, headers=_headers(api_key))
    assert model.status_code == 201, model.text

    # binding to an unknown system is refused
    response = client.post("/v1/agent/sessions",
                           json={"name": "s", "model_provider": "scripted",
                                 "system_id": "ghost-system"},
                           headers=_headers(api_key))
    assert response.status_code == 404
    assert "not registered" in response.json()["detail"]

    # binding to the registered system works
    response = client.post("/v1/agent/sessions",
                           json={"name": "s", "model_provider": "scripted",
                                 "system_id": "bound-agent",
                                 "environment": "production"},
                           headers=_headers(api_key))
    assert response.status_code == 201, response.text
    session = response.json()
    assert session["system_id"] == "bound-agent"

    # evidence from a run on this session is stamped with the system id
    content = "do it\nSCRIPT:" + json.dumps(
        [{"type": "final", "content": "done"}]
    )
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages",
                      json={"content": content},
                      headers=_headers(api_key)).json()
    assert run["state"] == "completed"

    db = SessionLocal()
    try:
        events = db.query(TraceEvent).filter(TraceEvent.run_id == run["id"]).all()
        assert events, "run produced no evidence"
        assert all(e.system_id == "bound-agent" for e in events)
        stored = db.get(AgentSession, session["id"])
        assert stored.system_id == "bound-agent"
    finally:
        db.close()


def test_fork_inherits_system_binding(client, api_key):
    _register(client, api_key, system_id="forked-agent")
    client.post("/v1/models", json={
        "provider": "scripted", "model_id": "fork-model",
        "approval_status": "approved", "approved_by": "sec",
    }, headers=_headers(api_key))
    session = client.post("/v1/agent/sessions",
                          json={"name": "s", "model_provider": "scripted",
                                "model_id": "fork-model",
                                "system_id": "forked-agent"},
                          headers=_headers(api_key)).json()
    fork = client.post(f"/v1/agent/sessions/{session['id']}/fork",
                       headers=_headers(api_key)).json()
    assert fork["system_id"] == "forked-agent"


def test_unbound_sessions_still_work(client, api_key):
    """V1 compatibility: sessions without a registered system are unaffected."""
    session = client.post("/v1/agent/sessions",
                          json={"name": "legacy", "model_provider": "scripted"},
                          headers=_headers(api_key)).json()
    assert session["system_id"] is None
    content = "hi\nSCRIPT:" + json.dumps([{"type": "final", "content": "hello"}])
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages",
                      json={"content": content},
                      headers=_headers(api_key)).json()
    assert run["state"] == "completed"
