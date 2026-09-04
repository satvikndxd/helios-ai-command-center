"""
Agent runtime tests: sessions, run state machine, approvals-in-the-loop,
cancellation, retry, replay — and the flagship end-to-end GitHub workflow.

The `scripted` provider drives deterministic agent behavior; everything
else (broker, permissions, risk, policy, approvals, execution, trace) runs
for real through the HTTP API.
"""

import json
import os

import httpx
import pytest

from helios.broker.registry import default_registry
from helios.db import SessionLocal
from helios.models import TraceEvent
from helios.tools import github as github_tools
from helios.tools.filesystem import workspace_root


HEADERS = None  # set per-test via _headers


def _headers(api_key):
    return {"X-Helios-API-Key": api_key}


def _create_session(client, api_key, **overrides):
    payload = {"name": "test", "model_provider": "scripted", **overrides}
    response = client.post("/v1/agent/sessions", json=payload,
                           headers=_headers(api_key))
    assert response.status_code == 201, response.text
    return response.json()


def _script(steps) -> str:
    return "run this plan\nSCRIPT:" + json.dumps(steps)


def _send(client, api_key, session_id, steps):
    response = client.post(
        f"/v1/agent/sessions/{session_id}/messages",
        json={"content": _script(steps)},
        headers=_headers(api_key),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _stub_github(responses):
    def factory():
        def handler(request: httpx.Request) -> httpx.Response:
            key = f"{request.method} {request.url.path}"
            body = responses.get(key)
            if body is None:
                return httpx.Response(404, json={"message": f"no stub for {key}"})
            return httpx.Response(200, json=body)
        return httpx.Client(base_url="https://api.github.test",
                            transport=httpx.MockTransport(handler))
    return factory


# --- sessions --------------------------------------------------------------


def test_session_lifecycle_and_fork(client, api_key):
    session = _create_session(client, api_key, github_repo="acme/api")
    assert any(g["scope"] == "github.merge" for g in session["grants"])

    run = _send(client, api_key, session["id"],
                [{"type": "final", "content": "hello!"}])
    assert run["state"] == "completed"
    assert run["output_text"] == "hello!"

    # session persists history
    detail = client.get(f"/v1/agent/sessions/{session['id']}",
                        headers=_headers(api_key)).json()
    assert detail["message_count"] >= 2
    assert detail["runs"][0]["state"] == "completed"

    # fork carries history
    fork = client.post(f"/v1/agent/sessions/{session['id']}/fork",
                       headers=_headers(api_key)).json()
    assert fork["forked_from"] == session["id"]
    assert fork["message_count"] == detail["message_count"]


def test_session_tenant_isolation(client, api_key, other_tenant_api_key):
    session = _create_session(client, api_key)
    response = client.get(f"/v1/agent/sessions/{session['id']}",
                          headers=_headers(other_tenant_api_key))
    assert response.status_code == 404


# --- the loop --------------------------------------------------------------


def test_agent_reads_file_through_broker(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    with open(os.path.join(workspace_root(), "notes.txt"), "w") as fh:
        fh.write("the answer is 42")

    session = _create_session(client, api_key)
    run = _send(client, api_key, session["id"], [
        {"type": "tool_call", "tool": "fs.read", "args": {"path": "notes.txt"},
         "reasoning": "read the notes"},
        {"type": "final", "content": "done reading"},
    ])
    assert run["state"] == "completed"

    events = client.get(f"/v1/agent/runs/{run['id']}/events",
                        headers=_headers(api_key)).json()["events"]
    types = [e["event_type"] for e in events]
    for expected in ("state_change", "model_call", "tool_proposal",
                     "permission_evaluation", "risk_evaluation",
                     "policy_evaluation", "tool_execution", "outcome"):
        assert expected in types, f"missing {expected} in {types}"
    execution = next(e for e in events if e["event_type"] == "tool_execution")
    assert "the answer is 42" in json.dumps(execution["payload"])


def test_denied_tool_feeds_back_and_run_continues(client, api_key):
    session = _create_session(client, api_key, grants=[])  # no permissions at all
    run = _send(client, api_key, session["id"], [
        {"type": "tool_call", "tool": "fs.read", "args": {"path": "x.txt"}},
        {"type": "final", "content": "I could not read the file."},
    ])
    assert run["state"] == "completed"
    detail = client.get(f"/v1/agent/sessions/{session['id']}",
                        headers=_headers(api_key)).json()
    tool_messages = [m for m in detail["messages"] if m["role"] == "tool"]
    assert any("denied" in m["content"] for m in tool_messages)


def test_awaiting_approval_state_and_deny_blocks(client, api_key):
    session = _create_session(client, api_key, github_repo="acme/api",
                              environment="production")
    run = _send(client, api_key, session["id"], [
        {"type": "tool_call", "tool": "github.merge_pr",
         "args": {"repo": "acme/api", "number": 5, "base": "main"}},
        {"type": "final", "content": "merged"},
    ])
    assert run["state"] == "awaiting_approval"   # never generic "working"
    assert run["pending"]["tool"] == "github.merge_pr"
    assert run["pending"]["risk"]["risk"] == "critical"
    approval_id = run["pending"]["approval_id"]

    # resume without a decision -> still waiting
    still = client.post(f"/v1/agent/runs/{run['id']}/resume",
                        headers=_headers(api_key)).json()
    assert still["state"] == "awaiting_approval"

    decided = client.post(f"/v1/agent/approvals/{approval_id}/decide",
                          json={"decision": "denied", "decided_by": "sec-team"},
                          headers=_headers(api_key))
    assert decided.status_code == 200

    resumed = client.post(f"/v1/agent/runs/{run['id']}/resume",
                          headers=_headers(api_key)).json()
    assert resumed["state"] == "blocked"

    # a blocked run can be retried
    retried = client.post(f"/v1/agent/runs/{run['id']}/retry",
                          headers=_headers(api_key))
    assert retried.status_code == 201


def test_approval_then_resume_executes_exact_payload(client, api_key):
    github_tools.set_client_factory(_stub_github({
        "PUT /repos/acme/api/pulls/6/merge": {"merged": True, "sha": "beef"},
    }))
    try:
        session = _create_session(client, api_key, github_repo="acme/api",
                                  environment="production")
        run = _send(client, api_key, session["id"], [
            {"type": "tool_call", "tool": "github.merge_pr",
             "args": {"repo": "acme/api", "number": 6, "base": "main"}},
            {"type": "final", "content": "merge complete"},
        ])
        assert run["state"] == "awaiting_approval"
        approval_id = run["pending"]["approval_id"]

        client.post(f"/v1/agent/approvals/{approval_id}/decide",
                    json={"decision": "approved", "decided_by": "release-mgr"},
                    headers=_headers(api_key))
        resumed = client.post(f"/v1/agent/runs/{run['id']}/resume",
                              headers=_headers(api_key)).json()
        assert resumed["state"] == "completed"
        assert resumed["output_text"] == "merge complete"

        events = client.get(f"/v1/agent/runs/{run['id']}/events",
                            headers=_headers(api_key)).json()["events"]
        approvals = [e for e in events if e["event_type"] == "approval"]
        assert any(e["status"] == "pending" for e in approvals)
        assert any(e["status"] == "approved" for e in approvals)
        executions = [e for e in events if e["event_type"] == "tool_execution"]
        assert any(e["payload"].get("result", {}).get("merged") for e in executions)
    finally:
        github_tools.set_client_factory(None)


def test_approve_for_session_covers_repeat_calls(client, api_key):
    github_tools.set_client_factory(_stub_github({
        "PUT /repos/acme/api/pulls/11/merge": {"merged": True},
        "PUT /repos/acme/api/pulls/12/merge": {"merged": True},
    }))
    try:
        session = _create_session(client, api_key, github_repo="acme/api",
                                  environment="production")
        run = _send(client, api_key, session["id"], [
            {"type": "tool_call", "tool": "github.merge_pr",
             "args": {"repo": "acme/api", "number": 11, "base": "main"}},
            {"type": "tool_call", "tool": "github.merge_pr",
             "args": {"repo": "acme/api", "number": 12, "base": "main"}},
            {"type": "final", "content": "both merged"},
        ])
        approval_id = run["pending"]["approval_id"]
        client.post(f"/v1/agent/approvals/{approval_id}/decide",
                    json={"decision": "approve_session", "decided_by": "lead"},
                    headers=_headers(api_key))
        resumed = client.post(f"/v1/agent/runs/{run['id']}/resume",
                              headers=_headers(api_key)).json()
        # the second merge is covered by the session approval -> completes
        assert resumed["state"] == "completed"
        assert resumed["output_text"] == "both merged"
    finally:
        github_tools.set_client_factory(None)


def test_edited_args_rebind_approval(client, api_key):
    github_tools.set_client_factory(_stub_github({
        "POST /repos/acme/api/pulls": {"number": 77, "html_url": "u"},
    }))
    try:
        # create_pr in production is approval-gated and args_editable
        session = _create_session(client, api_key, github_repo="acme/api",
                                  environment="production")
        run = _send(client, api_key, session["id"], [
            {"type": "tool_call", "tool": "github.create_pr",
             "args": {"repo": "acme/api", "title": "WIP!!", "head": "fix",
                      "base": "main"}},
            {"type": "final", "content": "pr open"},
        ])
        assert run["state"] == "awaiting_approval"
        approval_id = run["pending"]["approval_id"]
        edited = {"repo": "acme/api", "title": "Fix: timeout handling",
                  "head": "fix", "base": "main"}
        response = client.post(
            f"/v1/agent/approvals/{approval_id}/decide",
            json={"decision": "approved", "decided_by": "lead",
                  "edited_args": edited},
            headers=_headers(api_key))
        assert response.status_code == 200
        resumed = client.post(f"/v1/agent/runs/{run['id']}/resume",
                              headers=_headers(api_key)).json()
        assert resumed["state"] == "completed"
    finally:
        github_tools.set_client_factory(None)


def test_cancellation(client, api_key):
    session = _create_session(client, api_key, github_repo="acme/api",
                              environment="production")
    run = _send(client, api_key, session["id"], [
        {"type": "tool_call", "tool": "github.merge_pr",
         "args": {"repo": "acme/api", "number": 1, "base": "main"}},
        {"type": "final", "content": "x"},
    ])
    assert run["state"] == "awaiting_approval"
    cancelled = client.post(f"/v1/agent/runs/{run['id']}/cancel",
                            headers=_headers(api_key)).json()
    assert cancelled["state"] == "cancelled"
    # the pending approval was voided, not left dangling
    approvals = client.get("/v1/approvals?status=expired",
                           headers=_headers(api_key)).json()["approvals"]
    assert any(a["action"] == "github.merge_pr" for a in approvals)
    # terminal runs refuse further cancellation
    again = client.post(f"/v1/agent/runs/{run['id']}/cancel",
                        headers=_headers(api_key))
    assert again.status_code == 409


# --- replay ----------------------------------------------------------------


def test_replay_against_candidate_policy(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    with open(os.path.join(workspace_root(), "r.txt"), "w") as fh:
        fh.write("data")
    session = _create_session(client, api_key)
    run = _send(client, api_key, session["id"], [
        {"type": "tool_call", "tool": "fs.read", "args": {"path": "r.txt"}},
        {"type": "tool_call", "tool": "fs.write",
         "args": {"path": "out.txt", "content": "hi"}},
        {"type": "final", "content": "done"},
    ])
    assert run["state"] == "completed"

    # same policy -> no changes
    same = client.post(f"/v1/agent/runs/{run['id']}/replay", json={},
                       headers=_headers(api_key)).json()
    assert same["proposals"] == 2
    assert same["changes"] == []
    assert same["original"] == {"executed": 2}

    # candidate policy: every write requires approval
    strict = {
        "version": "candidate-strict-v2",
        "rules": [
            {"id": "approve_all_writes",
             "match": {"capability": ["write", "execute", "destructive"]},
             "effect": "require_approval",
             "reason": "candidate: all writes need approval"},
            {"id": "allow_reads", "match": {"max_risk": "medium"},
             "effect": "allow", "reason": "reads flow"},
        ],
    }
    diff = client.post(f"/v1/agent/runs/{run['id']}/replay",
                       json={"policy": strict},
                       headers=_headers(api_key)).json()
    assert diff["candidate"] == {"executed": 1, "approval_required": 1}
    assert len(diff["changes"]) == 1
    assert diff["changes"][0]["tool"] == "fs.write"
    assert diff["changes"][0]["candidate_rule"] == "approve_all_writes"


# --- external ingestion ----------------------------------------------------


def test_otel_ingestion_maps_spans_to_trace(client, api_key):
    payload = {
        "resourceSpans": [{
            "scopeSpans": [{
                "spans": [
                    {"traceId": "abc123", "spanId": "s1", "name": "llm.call",
                     "startTimeUnixNano": "1000000000",
                     "endTimeUnixNano": "1250000000",
                     "attributes": [{"key": "model",
                                     "value": {"stringValue": "gpt-x"}}]},
                    {"traceId": "abc123", "spanId": "s2", "parentSpanId": "s1",
                     "name": "tool.search", "attributes": []},
                ]
            }]
        }]
    }
    response = client.post("/v1/ingest/otel", json=payload,
                           headers=_headers(api_key))
    assert response.status_code == 201
    body = response.json()
    assert body["ingested"] == 2
    run_id = body["runs"][0]

    events = client.get(f"/v1/ingest/runs/{run_id}/events",
                        headers=_headers(api_key)).json()["events"]
    assert len(events) == 2
    assert events[0]["event_type"] == "external_span"
    assert events[0]["payload"]["attributes"]["model"] == "gpt-x"
    assert events[0]["latency_ms"] == 250


# --- flagship end-to-end ---------------------------------------------------


def test_flagship_github_workflow_end_to_end(client, api_key):
    """
    The whole story in one test:

    agent reads repo -> edits file -> runs tests -> creates branch ->
    commits -> creates PR (dev: allowed) -> requests merge into main ->
    HELIOS scores CRITICAL -> approval required -> human approves ->
    HELIOS executes the merge -> complete hierarchical trace recorded.
    """
    root = workspace_root()
    os.makedirs(root, exist_ok=True)
    # a tiny git repo inside the workspace
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "work"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@t.io"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "agent"], cwd=root, check=True)

    github_tools.set_client_factory(_stub_github({
        "GET /repos/acme/api": {"full_name": "acme/api", "default_branch": "main",
                                "description": "svc", "open_issues_count": 1,
                                "language": "py", "private": False},
        "POST /repos/acme/api/pulls": {"number": 42, "html_url": "http://pr/42"},
        "PUT /repos/acme/api/pulls/42/merge": {"merged": True, "sha": "cafe42"},
    }))
    try:
        session = _create_session(client, api_key, github_repo="acme/api")
        run = _send(client, api_key, session["id"], [
            {"type": "tool_call", "tool": "github.get_repo",
             "args": {"repo": "acme/api"}, "reasoning": "inspect the repo"},
            {"type": "tool_call", "tool": "fs.write",
             "args": {"path": "fix.py", "content": "def fix():\n    return 42\n"}},
            {"type": "tool_call", "tool": "shell.run",
             "args": {"command": "python3 -c \"import fix\" "}},
            {"type": "tool_call", "tool": "git.branch",
             "args": {"name": "agent/fix-42"}},
            {"type": "tool_call", "tool": "git.commit",
             "args": {"message": "fix: return 42", "add_all": True}},
            {"type": "tool_call", "tool": "github.create_pr",
             "args": {"repo": "acme/api", "title": "fix: return 42",
                      "head": "agent/fix-42", "base": "main"}},
            {"type": "tool_call", "tool": "github.merge_pr",
             "args": {"repo": "acme/api", "number": 42, "base": "main"}},
            {"type": "final", "content": "Fixed, tested, PR #42 merged."},
        ])

        # the run stopped exactly at the dangerous step
        assert run["state"] == "awaiting_approval"
        pending = run["pending"]
        assert pending["tool"] == "github.merge_pr"
        assert pending["risk"]["risk"] in ("high", "critical")
        assert "protected branch 'main'" in pending["risk"]["reasons"]

        # the approval carries everything a human needs
        approvals = client.get("/v1/approvals?status=pending",
                               headers=_headers(api_key)).json()["approvals"]
        approval = next(a for a in approvals
                        if a["id"] == pending["approval_id"])
        assert approval["action"] == "github.merge_pr"
        assert approval["summary"]["resource"]["github.repo"] == "acme/api"
        assert approval["summary"]["risk"]["reasons"]

        # human approves; HELIOS executes; run completes
        client.post(f"/v1/agent/approvals/{approval['id']}/decide",
                    json={"decision": "approved", "decided_by": "release-mgr"},
                    headers=_headers(api_key))
        resumed = client.post(f"/v1/agent/runs/{run['id']}/resume",
                              headers=_headers(api_key)).json()
        assert resumed["state"] == "completed"
        assert "merged" in resumed["output_text"]

        # complete hierarchical trace
        events = client.get(f"/v1/agent/runs/{run['id']}/events",
                            headers=_headers(api_key)).json()["events"]
        proposals = [e for e in events if e["event_type"] == "tool_proposal"]
        assert [p["name"] for p in proposals] == [
            "github.get_repo", "fs.write", "shell.run", "git.branch",
            "git.commit", "github.create_pr", "github.merge_pr",
            "github.merge_pr",  # re-entry after approval
        ]
        # every proposal has its policy evaluation child
        for proposal in proposals:
            children = [e for e in events if e["parent_id"] == proposal["id"]]
            assert any(c["event_type"] == "policy_evaluation" for c in children)
        # the merge execution really happened, exactly once
        merges = [e for e in events
                  if e["event_type"] == "tool_execution"
                  and e["name"] == "github.merge_pr"
                  and e["status"] == "ok"]
        assert len(merges) == 1
        assert merges[0]["payload"]["result"]["merged"] is True

        # and replay can explain the whole run under the same policy
        replay = client.post(f"/v1/agent/runs/{run['id']}/replay", json={},
                             headers=_headers(api_key)).json()
        assert replay["proposals"] == len(proposals)
    finally:
        github_tools.set_client_factory(None)
        import shutil
        shutil.rmtree(os.path.join(root, ".git"), ignore_errors=True)
