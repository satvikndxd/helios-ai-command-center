"""
EVIDENCE + human-oversight tests: WHY explanations from real state,
decision records, approval expiry, comments, oversight report + bypass.
"""

import os

import httpx
import pytest

from helios.tools import github as github_tools
from helios.tools.filesystem import workspace_root


def H(api_key):
    return {"X-Helios-API-Key": api_key}


def _stub_github(responses):
    def factory():
        def handler(request):
            key = f"{request.method} {request.url.path}"
            return httpx.Response(200, json=responses.get(key, {}))
        return httpx.Client(base_url="https://api.github.test",
                            transport=httpx.MockTransport(handler))
    return factory


def _ensure_model(client, api_key, provider="scripted", model_id="mock-model-1"):
    """Register a fully-cleared approved model (idempotent across tests)."""
    client.post("/v1/models", json={
        "provider": provider, "model_id": model_id, "status": "approved",
        "allowed_data_classes": ["public", "internal", "confidential",
                                 "sensitive", "pii"],
        "allowed_environments": ["dev", "staging", "production"]},
        headers=H(api_key))  # 422 if already registered — fine


def _system(client, api_key, sid, **over):
    _ensure_model(client, api_key)
    # L2 (supervised) so production writes require approval rather than deny
    payload = {"system_id": sid, "environment": "production", "autonomy_level": 2,
               "owner": "platform"}
    payload.update(over)
    client.post("/v1/systems", json=payload, headers=H(api_key))


def _script_session(client, api_key, sid, steps):
    session = client.post("/v1/agent/sessions", json={
        "name": "t", "system_id": sid, "model_provider": "scripted"},
        headers=H(api_key)).json()
    import json
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages",
                      json={"content": "go\nSCRIPT:" + json.dumps(steps)},
                      headers=H(api_key)).json()
    return session, run


# --- WHY explanations ------------------------------------------------------


def test_why_allowed_explanation_from_real_state(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    with open(os.path.join(workspace_root(), "w.txt"), "w") as fh:
        fh.write("data")
    _system(client, api_key, "why-allow", environment="dev", autonomy_level=2)
    _, run = _script_session(client, api_key, "why-allow", [
        {"type": "tool_call", "tool": "fs.read", "args": {"path": "w.txt"}},
        {"type": "final", "content": "done"},
    ])
    decisions = client.get("/v1/decisions?system_id=why-allow&kind=tool_call",
                           headers=H(api_key)).json()["decisions"]
    tool_dec = next(d for d in decisions if d["action"] == "fs.read")
    why = client.get(f"/v1/decisions/{tool_dec['id']}/why", headers=H(api_key)).json()
    assert why["verdict"] == "ALLOWED"
    assert any(c["label"] == "AI system registered" and c["ok"] for c in why["checks"])
    assert any(c["label"] == "Policy matched" for c in why["checks"])
    assert why["passed"] >= 1


def test_why_blocked_explanation(client, api_key):
    # unapproved model -> model-use denied -> WHY = BLOCKED
    client.post("/v1/systems", json={"system_id": "why-block", "environment": "dev",
                "autonomy_level": 2}, headers=H(api_key))
    session = client.post("/v1/agent/sessions", json={
        "name": "t", "system_id": "why-block", "model_provider": "scripted",
        "model_id": "ghost-unregistered"}, headers=H(api_key)).json()
    client.post(f"/v1/agent/sessions/{session['id']}/messages",
                json={"content": 'go\nSCRIPT:[{"type":"final","content":"x"}]'},
                headers=H(api_key))
    decisions = client.get("/v1/decisions?system_id=why-block&kind=model_use",
                           headers=H(api_key)).json()["decisions"]
    dec = decisions[0]
    why = client.get(f"/v1/decisions/{dec['id']}/why", headers=H(api_key)).json()
    assert why["verdict"] == "BLOCKED"
    assert "Why was" in why["question"]


# --- oversight report + bypass detection -----------------------------------


def test_oversight_report_tracks_involvement(client, api_key):
    github_tools.set_client_factory(_stub_github({
        "PUT /repos/acme/api/pulls/3/merge": {"merged": True},
    }))
    try:
        _system(client, api_key, "oversight-sys", environment="production",
                autonomy_level=2)
        session, run = _script_session(client, api_key, "oversight-sys", [
            {"type": "tool_call", "tool": "github.merge_pr",
             "args": {"repo": "acme/api", "number": 3, "base": "main"}},
            {"type": "final", "content": "merged"},
        ])
        assert run["state"] == "awaiting_approval"

        # before decision: oversight report shows it awaiting a human
        report = client.get("/v1/systems/oversight-sys/oversight",
                            headers=H(api_key)).json()
        assert report["summary"]["awaiting"] >= 1
        assert report["bypass_detected"] is False

        approval_id = run["pending"]["approval_id"]
        client.post(f"/v1/agent/approvals/{approval_id}/decide",
                    json={"decision": "approved", "decided_by": "sec-lead",
                          "comment": "verified the diff"}, headers=H(api_key))
        client.post(f"/v1/agent/runs/{run['id']}/resume", headers=H(api_key))

        report2 = client.get("/v1/systems/oversight-sys/oversight",
                             headers=H(api_key)).json()
        assert report2["summary"]["involved"] >= 1
        assert any(e["reviewer"] == "sec-lead" for e in report2["human_involved"])
    finally:
        github_tools.set_client_factory(None)


def test_approval_comment_recorded(client, api_key):
    github_tools.set_client_factory(_stub_github({}))
    try:
        _system(client, api_key, "comment-sys")
        _, run = _script_session(client, api_key, "comment-sys", [
            {"type": "tool_call", "tool": "github.merge_pr",
             "args": {"repo": "x/y", "number": 1, "base": "main"}},
            {"type": "final", "content": "x"}])
        aid = run["pending"]["approval_id"]
        r = client.post(f"/v1/agent/approvals/{aid}/decide",
                        json={"decision": "denied", "decided_by": "rev",
                              "comment": "risky, rejecting"}, headers=H(api_key)).json()
        assert r["comments"][0]["text"] == "risky, rejecting"
        assert r["comments"][0]["author"] == "rev"
    finally:
        github_tools.set_client_factory(None)


# --- approval expiry --------------------------------------------------------


def test_expired_approval_does_not_authorize(client, api_key, db=None):
    """An approval whose expiry has passed cannot back an execution."""
    from datetime import datetime, timezone, timedelta
    from helios.db import SessionLocal
    from helios.models import ApprovalRequest

    github_tools.set_client_factory(_stub_github({
        "PUT /repos/acme/api/pulls/8/merge": {"merged": True},
    }))
    try:
        _system(client, api_key, "expiry-sys")
        _, run = _script_session(client, api_key, "expiry-sys", [
            {"type": "tool_call", "tool": "github.merge_pr",
             "args": {"repo": "acme/api", "number": 8, "base": "main"}},
            {"type": "final", "content": "x"}])
        aid = run["pending"]["approval_id"]
        client.post(f"/v1/agent/approvals/{aid}/decide",
                    json={"decision": "approved", "decided_by": "rev"},
                    headers=H(api_key))

        # force the approval to be already expired
        sess = SessionLocal()
        appr = sess.get(ApprovalRequest, aid)
        appr.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        sess.commit()
        sess.close()

        resumed = client.post(f"/v1/agent/runs/{run['id']}/resume",
                              headers=H(api_key)).json()
        # the expired approval no longer authorizes -> back to awaiting approval
        assert resumed["state"] == "awaiting_approval"
        assert resumed["pending"]["approval_id"] != aid
    finally:
        github_tools.set_client_factory(None)
