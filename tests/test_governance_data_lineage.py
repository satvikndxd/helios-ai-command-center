"""
Phase 5 — Data classification + lineage tests (POLICY/EVIDENCE planes).

Covers: inbound data_access/retrieval evidence with detected classes,
outbound destination evidence, lineage completeness (every edge cites real
event ids), allowed vs blocked data-to-model flows driven by DETECTED content
(not declarations), system-level lineage aggregation, and secret
non-persistence in classification evidence.
"""

import json
import os
import uuid

import httpx

from helios.tools import github as github_tools
from helios.tools.filesystem import workspace_root


def _headers(api_key):
    return {"X-Helios-API-Key": api_key}


def _script(steps):
    return "run this plan\nSCRIPT:" + json.dumps(steps)


def _setup_system(client, api_key, system_id, *, model_classes=None,
                  environment="dev", declared=None):
    """Register system + approved scripted model; return a bound session."""
    r = client.post("/v1/systems", json={
        "system_id": system_id, "name": system_id, "owner": "data-team",
        "environment": environment, "autonomy_level": 2,
        "data_classes": declared or [],
    }, headers=_headers(api_key))
    assert r.status_code == 201, r.text
    model_id = f"{system_id}-model"
    r = client.post("/v1/models", json={
        "provider": "scripted", "model_id": model_id,
        "approval_status": "approved", "approved_by": "sec",
        "allowed_data_classes": model_classes or [],
    }, headers=_headers(api_key))
    assert r.status_code == 201, r.text
    r = client.post("/v1/agent/sessions", json={
        "name": "s", "model_provider": "scripted", "model_id": model_id,
        "system_id": system_id, "environment": environment,
        "github_repo": "acme/api",
    }, headers=_headers(api_key))
    assert r.status_code == 201, r.text
    return r.json()


def _seed_file(name, content):
    root = workspace_root()
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, name)
    with open(path, "w") as fh:
        fh.write(content)
    return path


def _events(client, api_key, run_id):
    return client.get(f"/v1/agent/runs/{run_id}/events",
                      headers=_headers(api_key)).json()["events"]


def _lineage(client, api_key, run_id):
    response = client.get(f"/v1/agent/runs/{run_id}/lineage",
                          headers=_headers(api_key))
    assert response.status_code == 200, response.text
    return response.json()


# --- inbound evidence ---------------------------------------------------------


def test_read_produces_classified_data_access_evidence(client, api_key):
    _seed_file("contacts.txt", "reach jane.doe@example.com about the invoice")
    session = _setup_system(client, api_key, "lineage-read",
                            model_classes=["PUBLIC", "PII"])

    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.read",
             "args": {"path": "contacts.txt"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "completed", run

    events = _events(client, api_key, run["id"])
    accesses = [e for e in events if e["event_type"] == "data_access"]
    assert accesses, "read produced no data_access evidence"
    inbound = accesses[0]["payload"]
    assert inbound["direction"] == "inbound"
    assert inbound["source_kind"] == "file"
    assert "PII" in inbound["classes_detected"]
    assert inbound["pii_counts"]["email"] == 1
    # classification evidence never contains the raw PII value
    assert "jane.doe@example.com" not in json.dumps(inbound)


def test_network_read_produces_retrieval_evidence(client, api_key):
    session = _setup_system(client, api_key, "lineage-retrieval",
                            model_classes=["PUBLIC", "PII"])

    def factory():
        def handler(request):
            return httpx.Response(200, json={
                "full_name": "acme/api", "private": False,
                "description": "internal api"})
        return httpx.Client(base_url="https://api.github.test",
                            transport=httpx.MockTransport(handler))

    github_tools.set_client_factory(factory)
    try:
        run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
            "content": _script([
                {"type": "tool_call", "tool": "github.get_repo",
                 "args": {"repo": "acme/api"}},
                {"type": "final", "content": "done"},
            ])}, headers=_headers(api_key)).json()
    finally:
        github_tools.set_client_factory(None)
    assert run["state"] == "completed", run

    events = _events(client, api_key, run["id"])
    retrievals = [e for e in events if e["event_type"] == "retrieval"]
    assert retrievals, "network read produced no retrieval evidence"
    payload = retrievals[0]["payload"]
    assert payload["direction"] == "inbound"
    assert payload["source_kind"] == "repo"
    assert payload["source"].get("github.repo") == "acme/api"


def test_write_produces_outbound_destination_evidence(client, api_key):
    session = _setup_system(client, api_key, "lineage-write",
                            model_classes=["PUBLIC", "INTERNAL"],
                            declared=["INTERNAL"])
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.write",
             "args": {"path": "out/report.txt",
                      "content": "quarterly build summary attached"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "completed", run

    events = _events(client, api_key, run["id"])
    outbound = [e for e in events if e["event_type"] == "data_access"
                and e["payload"].get("direction") == "outbound"]
    assert outbound
    payload = outbound[0]["payload"]
    assert payload["source_kind"] == "file"
    # the system's declared class flows through to the destination evidence
    assert payload["data_class"] == "INTERNAL"
    assert "INTERNAL" in payload["classes"]
    assert payload["destination"].get("filesystem.path", "").endswith("out/report.txt")

    graph = _lineage(client, api_key, run["id"])
    dest_edges = [e for e in graph["edges"] if e["kind"] == "output_to_destination"]
    assert dest_edges and dest_edges[0]["data_class"] == "INTERNAL"
    dest_nodes = [n for n in graph["nodes"] if n["kind"] == "destination"]
    assert dest_nodes


def test_pii_write_escalates_to_approval_then_records_outbound_flow(client, api_key):
    """
    Detected PII raises contextual risk -> the write requires approval.
    After the human approves, the outbound flow is recorded with class PII.
    """
    session = _setup_system(client, api_key, "lineage-pii-write",
                            model_classes=["PUBLIC", "PII"])
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.write",
             "args": {"path": "out/notify.txt",
                      "content": "notify john.smith@corp.com immediately"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "awaiting_approval", run

    # the approval summary carries the classification evidence for the human
    approval_id = run["pending"]["approval_id"]
    approval = [a for a in client.get("/v1/approvals",
                headers=_headers(api_key)).json()["approvals"]
                if a["id"] == approval_id][0]
    assert approval["summary"]["data_classification"]["effective"] == "PII"
    assert approval["summary"]["data_classification"]["pii_counts"]["email"] == 1
    assert any("sensitive data classes" in r
               for r in approval["summary"]["risk"]["reasons"])

    # human approves -> resume -> execution + outbound evidence
    assert client.post(f"/v1/agent/approvals/{approval_id}/decide",
                       json={"decision": "approved", "decided_by": "privacy-lead"},
                       headers=_headers(api_key)).status_code == 200
    run = client.post(f"/v1/agent/runs/{run['id']}/resume",
                      headers=_headers(api_key)).json()
    assert run["state"] == "completed", run

    events = _events(client, api_key, run["id"])
    outbound = [e for e in events if e["event_type"] == "data_access"
                and e["payload"].get("direction") == "outbound"]
    assert outbound and outbound[0]["payload"]["data_class"] == "PII"
    approvals = [e for e in events if e["event_type"] == "approval"
                 and e["status"] == "approved"]
    assert approvals, "approval not recorded in evidence"
    graph = _lineage(client, api_key, run["id"])
    dest_edges = [e for e in graph["edges"] if e["kind"] == "output_to_destination"]
    assert dest_edges and dest_edges[0]["data_class"] == "PII"


# --- lineage graph -------------------------------------------------------------


def test_lineage_allowed_flow_cites_real_events(client, api_key):
    _seed_file("users.txt", "user bob@corp.com enrolled")
    session = _setup_system(client, api_key, "lineage-allowed",
                            model_classes=["PUBLIC", "PII"])
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.read", "args": {"path": "users.txt"}},
            {"type": "final", "content": "summarized"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "completed", run

    graph = _lineage(client, api_key, run["id"])
    events = _events(client, api_key, run["id"])
    event_ids = {e["id"] for e in events}

    kinds = {n["kind"] for n in graph["nodes"]}
    assert {"run", "system", "source", "model"} <= kinds
    assert graph["system_id"] == "lineage-allowed"

    data_edges = [e for e in graph["edges"] if e["kind"] == "data_to_model"]
    assert data_edges, "no source->model edge derived"
    edge = data_edges[0]
    assert edge["data_class"] == "PII"          # detected from file content
    assert edge["allowed"] is True
    # EVERY edge cites events that actually exist (lineage completeness)
    for e in graph["edges"]:
        assert e["event_ids"], f"edge without evidence: {e}"
        assert set(e["event_ids"]) <= event_ids, f"edge cites unknown events: {e}"
    assert graph["violations"] == []
    assert "PII" in graph["summary"]["data_classes_seen"]


def test_lineage_blocked_flow_when_detected_pii_exceeds_model_ceiling(
        client, api_key):
    """Detected content — not declarations — drives the block, and lineage shows it."""
    _seed_file("payroll-users.txt", "ssn 987-65-4321 belongs to alice")
    session = _setup_system(client, api_key, "lineage-blocked",
                            model_classes=["PUBLIC"])  # ceiling below PII
    # The script itself is PII-free: the sensitive content enters ONLY through
    # the tool result, so the block on the second model call is caused by
    # detected data flow — not by the request text.
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.read",
             "args": {"path": "payroll-users.txt"}},
            {"type": "final", "content": "never reached"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "blocked"
    events = _events(client, api_key, run["id"])
    # sanity: the first model call happened, the second was denied
    assert any(e["event_type"] == "model_call" for e in events)
    denied_preflight = [e for e in events if e["event_type"] == "policy_evaluation"
                        and e["status"] == "denied"]
    assert denied_preflight, "no denied model preflight recorded"

    graph = _lineage(client, api_key, run["id"])
    blocked = [e for e in graph["edges"]
               if e["kind"] == "data_to_model" and not e["allowed"]]
    assert blocked, f"blocked flow missing from lineage: {graph['edges']}"
    assert blocked[0]["data_class"] == "PII"
    assert blocked[0]["decision"] == "deny"
    assert any("ceiling" in r or "ABOVE" in r for r in blocked[0]["reasons"])
    assert graph["summary"]["violation_count"] >= 1
    assert denied_preflight[0]["id"] in blocked[0]["event_ids"]


def test_lineage_denied_tool_flow_recorded_as_violation(client, api_key):
    session = _setup_system(client, api_key, "lineage-tool-denied")
    client.post("/v1/policies", json={
        "name": f"no-shell-{uuid.uuid4().hex[:8]}", "version": "v1",
        "status": "active", "scope": {"system_ids": ["lineage-tool-denied"]},
        "rules": [{"id": "deny-shell", "when": {"subject": ["tool_action"],
                                                "tool": "shell.*"},
                   "effect": "deny", "reason": "no shell"}],
    }, headers=_headers(api_key))
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "shell.run",
             "args": {"command": "echo hello"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "completed"

    graph = _lineage(client, api_key, run["id"])
    tool_edges = [e for e in graph["edges"] if e["kind"] == "model_to_tool"]
    assert tool_edges
    denied = [e for e in tool_edges if not e["allowed"]]
    assert denied, "denied tool flow missing from lineage"
    assert any("governance" in r or "no shell" in r for r in denied[0]["reasons"])
    assert graph["summary"]["violation_count"] >= 1


def test_system_lineage_aggregates_runs(client, api_key):
    _seed_file("agg.txt", "plain content")
    session = _setup_system(client, api_key, "lineage-system")
    for _ in range(2):
        run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
            "content": _script([
                {"type": "tool_call", "tool": "fs.read",
                 "args": {"path": "agg.txt"}},
                {"type": "final", "content": "done"},
            ])}, headers=_headers(api_key)).json()
        assert run["state"] == "completed"

    response = client.get("/v1/systems/lineage-system/lineage",
                          headers=_headers(api_key))
    assert response.status_code == 200
    graph = response.json()
    assert graph["system_id"] == "lineage-system"
    assert graph["runs"] == 2
    assert graph["summary"]["sources"] >= 1
    # merged edges retain run attribution + event citations
    assert all("run_id" in e and e["event_ids"] for e in graph["edges"])
    # nodes merged: one source node shared across both runs
    source_nodes = [n for n in graph["nodes"] if n["kind"] == "source"]
    assert len(source_nodes) == 1
