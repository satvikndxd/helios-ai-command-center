"""
Phases W2-W4 + self-evolution tests — fully offline.

MCP: trust lifecycle, tool allowlist, budgets, version drift.
Browser: vault roundtrip, cookie non-leakage, domain allowlist, approvals.
Actions: typed registry, approval binding to exact payload, idempotency.
Schedules: change detection without external writes.
Evolution: mine -> propose -> human approve -> apply -> rollback.
"""

import json

import pytest

from helios.models import BrowserSession, McpServer
from helios.web.mcp import McpBroker, McpBudget, McpCallDenied, validate_arguments
from helios.web.vault import KEY_ENV, VaultError, decrypt_profile, encrypt_profile
from helios.web.browser import BrowserDenied, BrowserWorker
from helios.web.types import UNTRUSTED


# -- MCP (Phase W2) --------------------------------------------------------


class _McpResponse:
    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload)

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _McpClient:
    def __init__(self, payload=None, health=None):
        self._payload = payload or {"content": [{"type": "text", "text": "mcp says hi"}]}
        self._health = health or {"status": "ok", "version": "1.0.0"}
        self.calls = []

    def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json))
        return _McpResponse(self._payload)

    def get(self, url, headers=None):
        self.calls.append(("GET", url))
        return _McpResponse(self._health)


def _server(**overrides):
    defaults = dict(
        tenant_id="t1",
        name="reach",
        endpoint="http://mcp.local",
        pinned_version="1.0.0",
        trust_status="approved",
        tool_allowlist=["search"],
        budgets={},
    )
    defaults.update(overrides)
    return McpServer(**defaults)


def test_mcp_untrusted_server_cannot_be_called():
    broker = McpBroker(_server(trust_status="untrusted"), client=_McpClient())
    with pytest.raises(McpCallDenied, match="untrusted"):
        broker.call("search", {"q": "x"})


def test_mcp_tool_allowlist_enforced():
    broker = McpBroker(_server(), client=_McpClient())
    with pytest.raises(McpCallDenied, match="allowlist"):
        broker.call("delete_everything", {})


def test_mcp_call_returns_sanitized_untrusted_document():
    payload = {"content": [{"type": "text", "text": "result sk-abcdefghijklmnop1234 done"}]}
    broker = McpBroker(_server(), client=_McpClient(payload=payload))
    doc = broker.call("search", {"q": "x"})
    assert doc.trust == UNTRUSTED
    assert "sk-abcdefghijklmnop1234" not in doc.content
    assert doc.source_adapter == "mcp:reach"


def test_mcp_budget_max_calls():
    broker = McpBroker(_server(budgets={"max_calls": 2}), client=_McpClient())
    budget = McpBudget({"max_calls": 2})
    broker.call("search", {}, budget)
    broker.call("search", {}, budget)
    with pytest.raises(McpCallDenied, match="max_calls"):
        broker.call("search", {}, budget)


def test_mcp_argument_validation():
    with pytest.raises(McpCallDenied):
        validate_arguments(["not", "a", "dict"])
    with pytest.raises(McpCallDenied):
        validate_arguments({"big": "x" * 20_000})
    assert validate_arguments({"q": "ok"}) == {"q": "ok"}


def test_mcp_version_drift_marks_degraded():
    client = _McpClient(health={"version": "2.0.0"})
    broker = McpBroker(_server(pinned_version="1.0.0"), client=client)
    health = broker.health()
    assert health["status"] == "degraded"
    assert "version_drift" in health["detail"]


# -- Vault + browser (Phase W3) --------------------------------------------


def test_vault_roundtrip_and_integrity(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "unit-test-key")
    blob = encrypt_profile({"cookies": {"session": "secret-cookie-value"}})
    assert "secret-cookie-value" not in blob
    assert decrypt_profile(blob)["cookies"]["session"] == "secret-cookie-value"

    tampered = blob[:-6] + ("AAAAAA" if not blob.endswith("AAAAAA") else "BBBBBB")
    with pytest.raises(VaultError):
        decrypt_profile(tampered)


def test_vault_fails_closed_without_key(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(VaultError, match="disabled"):
        encrypt_profile({"cookies": {}})


class _PageClient:
    def __init__(self):
        self.last_cookies = None

    def get(self, url, cookies=None):
        self.last_cookies = cookies

        class R:
            text = "<html><head><title>Private</title></head><body><p>account page</p></body></html>"

            def raise_for_status(self):
                pass

        return R()


def test_browser_session_domain_allowlist_and_cookie_isolation(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "unit-test-key")
    session = BrowserSession(
        tenant_id="t1",
        user_id="alex",
        source="linkedin",
        domain_allowlist=["linkedin.com"],
        encrypted_profile=encrypt_profile({"cookies": {"li_at": "cookie-123"}}),
    )
    client = _PageClient()
    worker = BrowserWorker(client=client)

    with pytest.raises(BrowserDenied):
        worker.read_page("https://evil.example.com/x", session=session)
    assert worker.events[-1]["event"] == "blocked"

    doc = worker.read_page("https://www.linkedin.com/in/alex", session=session)
    # Cookie reached the outbound request inside the worker...
    assert client.last_cookies == {"li_at": "cookie-123"}
    # ...but never the document, events, or warnings.
    dumped = json.dumps(doc.model_dump(mode="json")) + json.dumps(worker.events)
    assert "cookie-123" not in dumped
    assert doc.trust == UNTRUSTED
    assert any(e["event"] == "navigate" for e in worker.events)


# -- Actions + approvals + idempotency (Phase W4) ---------------------------


def _mint_headers(api_key):
    return {"X-Helios-API-Key": api_key}


def test_action_approval_binding_and_idempotency(client, api_key):
    headers = _mint_headers(api_key)
    args = {"repo": "acme/app", "title": "Bug", "body": "details"}

    # Execute without approval -> denied.
    r = client.post(
        "/v1/actions/execute",
        headers=headers,
        json={"action": "github_open_issue", "args": args, "idempotency_key": "k1"},
    )
    assert r.status_code == 403

    # Propose -> approve.
    r = client.post(
        "/v1/actions/propose",
        headers=headers,
        json={"action": "github_open_issue", "args": args},
    )
    assert r.status_code == 201
    approval_id = r.json()["approval_id"]

    # Approval is bound to the EXACT payload: different args stay denied.
    client.post(
        f"/v1/approvals/{approval_id}/decide",
        headers=headers,
        json={"decision": "approved", "decided_by": "alex"},
    )
    r = client.post(
        "/v1/actions/execute",
        headers=headers,
        json={
            "action": "github_open_issue",
            "args": {**args, "repo": "acme/OTHER"},
            "idempotency_key": "k2",
        },
    )
    assert r.status_code == 403

    # Exact payload executes...
    r = client.post(
        "/v1/actions/execute",
        headers=headers,
        json={"action": "github_open_issue", "args": args, "idempotency_key": "k1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["replayed"] is False
    assert body["result"]["prepared"] == "github_open_issue"

    # ...and the retry with the same idempotency key replays, not re-executes.
    r = client.post(
        "/v1/actions/execute",
        headers=headers,
        json={"action": "github_open_issue", "args": args, "idempotency_key": "k1"},
    )
    assert r.json()["replayed"] is True


def test_unknown_action_is_rejected(client, api_key):
    r = client.post(
        "/v1/actions/propose",
        headers=_mint_headers(api_key),
        json={"action": "rm_rf_everything", "args": {}},
    )
    assert r.status_code == 422


def test_scheduled_research_change_detection(client, api_key):
    from helios.main import app
    from helios.routes.web import get_broker
    from helios.web.broker import WebAccessBroker
    from tests.test_web_access import _StubAdapter, _doc

    headers = _mint_headers(api_key)

    first = WebAccessBroker(adapters=[_StubAdapter("reddit", docs=[_doc("reddit", "old post")])])
    second = WebAccessBroker(adapters=[_StubAdapter("reddit", docs=[_doc("reddit", "new post")])])

    r = client.post(
        "/v1/schedules", headers=headers, json={"query": "product X", "interval_minutes": 30}
    )
    schedule_id = r.json()["id"]

    app.dependency_overrides[get_broker] = lambda: first
    try:
        report1 = client.post(f"/v1/schedules/{schedule_id}/run", headers=headers).json()["report"]
        assert report1["change_detected"] is False  # first run = baseline

        app.dependency_overrides[get_broker] = lambda: second
        report2 = client.post(f"/v1/schedules/{schedule_id}/run", headers=headers).json()["report"]
        assert report2["change_detected"] is True
        assert report2["new_documents"] == 1
    finally:
        app.dependency_overrides.pop(get_broker, None)


# -- Self-evolution --------------------------------------------------------


def test_evolution_mine_propose_approve_apply_rollback(client, api_key):
    headers = _mint_headers(api_key)

    # Generate failure evidence: two provider errors via an unsupported provider.
    for _ in range(2):
        client.post(
            "/v1/ai/complete",
            headers=headers,
            json={"input": "hello", "provider": "definitely-not-real"},
        )

    # 1-3: mine + cluster + propose.
    r = client.post("/v1/evolution/analyze", headers=headers)
    assert r.status_code == 200
    created = r.json()["created"]
    assert any(p["kind"] == "routing_fallback" for p in created)
    proposal = next(p for p in created if p["kind"] == "routing_fallback")
    assert proposal["evidence"]["occurrences"] >= 2
    assert proposal["status"] == "proposed"

    # Re-analysis dedupes instead of spamming.
    r = client.post("/v1/evolution/analyze", headers=headers)
    assert all(p["kind"] != "routing_fallback" for p in r.json()["created"])

    # 4-5: human approves -> applied + visible in live evolution state.
    r = client.post(
        f"/v1/evolution/proposals/{proposal['id']}/approve",
        headers=headers,
        json={"decided_by": "alex"},
    )
    assert r.json()["status"] == "applied"
    state = client.get("/v1/evolution/state", headers=headers).json()["state"]
    assert any(
        f.get("proposal_id") == proposal["id"] for f in state["routing_fallbacks"]
    )

    # Rollback restores the previous state.
    r = client.post(
        f"/v1/evolution/proposals/{proposal['id']}/rollback",
        headers=headers,
        json={"decided_by": "alex"},
    )
    assert r.json()["status"] == "rolled_back"
    state = client.get("/v1/evolution/state", headers=headers).json()["state"]
    assert all(
        f.get("proposal_id") != proposal["id"] for f in state.get("routing_fallbacks", [])
    )


def test_mcp_api_trust_lifecycle(client, api_key):
    headers = _mint_headers(api_key)

    r = client.post(
        "/v1/mcp/servers",
        headers=headers,
        json={"name": "reach", "endpoint": "http://mcp.local", "tool_allowlist": ["search"]},
    )
    assert r.status_code == 201
    server = r.json()
    assert server["trust_status"] == "untrusted"

    # Calling an untrusted server is refused.
    r = client.post(
        "/v1/mcp/call",
        headers=headers,
        json={"server_id": server["id"], "tool": "search", "arguments": {"q": "x"}},
    )
    assert r.status_code == 403

    # Approve, then the allowlist still gates tools.
    client.post(
        f"/v1/mcp/servers/{server['id']}/trust",
        headers=headers,
        json={"trust_status": "approved"},
    )
    r = client.post(
        "/v1/mcp/call",
        headers=headers,
        json={"server_id": server["id"], "tool": "not_allowed", "arguments": {}},
    )
    assert r.status_code == 403


def test_browser_api_requires_vault_and_approval(client, api_key, monkeypatch):
    headers = _mint_headers(api_key)

    # No vault key -> sessions fail closed.
    monkeypatch.delenv(KEY_ENV, raising=False)
    r = client.post(
        "/v1/browser/sessions",
        headers=headers,
        json={
            "user_id": "alex",
            "source": "linkedin",
            "domain_allowlist": ["linkedin.com"],
            "cookies": {"li_at": "cookie-xyz"},
        },
    )
    assert r.status_code == 503

    # With a key: session connects and NEVER echoes cookie material.
    monkeypatch.setenv(KEY_ENV, "unit-test-key")
    r = client.post(
        "/v1/browser/sessions",
        headers=headers,
        json={
            "user_id": "alex",
            "source": "linkedin",
            "domain_allowlist": ["linkedin.com"],
            "cookies": {"li_at": "cookie-xyz"},
        },
    )
    assert r.status_code == 201
    assert "cookie-xyz" not in r.text
    session_id = r.json()["id"]

    # Authenticated read without an approval -> 403 approval_required.
    r = client.post(
        "/v1/browser/read",
        headers=headers,
        json={"url": "https://www.linkedin.com/in/alex", "session_id": session_id},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["approval_required"] is True
