"""
Phase 6 — AI Decision Record tests (EVIDENCE plane).

Covers: automatic projection at run terminals, record completeness (one
record per action + run outcome), evidence provenance (every cited event id
resolves), idempotent rebuilds (upsert convergence across the approval
flow), model-preflight records, the WHY explanation endpoint (generated from
recorded state), filters, and tenant isolation.
"""

import json
import os
import uuid

from helios.tools.filesystem import workspace_root


def _headers(api_key):
    return {"X-Helios-API-Key": api_key}


def _script(steps):
    return "run this plan\nSCRIPT:" + json.dumps(steps)


def _setup(client, api_key, system_id, *, model_classes=None, environment="dev"):
    r = client.post("/v1/systems", json={
        "system_id": system_id, "name": system_id, "owner": "team",
        "environment": environment, "autonomy_level": 2,
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
    }, headers=_headers(api_key))
    assert r.status_code == 201, r.text
    return r.json()


def _decisions(client, api_key, **params):
    response = client.get("/v1/decisions", params=params, headers=_headers(api_key))
    assert response.status_code == 200, response.text
    return response.json()["decisions"]


def _events(client, api_key, run_id):
    return client.get(f"/v1/agent/runs/{run_id}/events",
                      headers=_headers(api_key)).json()["events"]


# --- projection + completeness -------------------------------------------------


def test_completed_run_projects_tool_and_outcome_records(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    with open(os.path.join(workspace_root(), "dec-read.txt"), "w") as fh:
        fh.write("plain build notes")
    session = _setup(client, api_key, "dec-basic")

    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.read",
             "args": {"path": "dec-read.txt"}},
            {"type": "tool_call", "tool": "fs.write",
             "args": {"path": "dec-out.txt", "content": "summary"}},
            {"type": "final", "content": "all done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "completed", run

    records = _decisions(client, api_key, run_id=run["id"])
    kinds = {}
    for record in records:
        kinds.setdefault(record["kind"], []).append(record)
    assert len(kinds["tool_action"]) == 2
    assert len(kinds["run_outcome"]) == 1
    assert all(r["decision"] == "EXECUTED" for r in kinds["tool_action"])
    assert kinds["run_outcome"][0]["decision"] == "COMPLETED"

    # every record is bound to identity + evidence
    for record in records:
        assert record["system_id"] == "dec-basic"
        assert record["schema_version"] == "helios-decision-record-v1"
        assert record["actor"]["environment"] == "dev"
        assert record["evidence"]["trace_event_ids"]

    # tool records carry policy + risk + classification projections
    write_record = [r for r in kinds["tool_action"] if r["action"] == "fs.write"][0]
    assert write_record["policy"]["tool_policy"]["rule_id"] == "allow_low_medium"
    assert write_record["risk"] in ("low", "medium")
    assert write_record["data_classification"]["effective"] == "PUBLIC"
    assert write_record["oversight"] == {
        "required": False, "actual": "none", "reviewer": None, "approval_id": None}
    assert write_record["model"] and write_record["model"].startswith("scripted/")

    # run outcome aggregates counts
    outcome = kinds["run_outcome"][0]
    assert outcome["evidence"]["model_call_count"] >= 3
    assert outcome["evidence"]["tool_proposal_count"] == 2


def test_evidence_provenance_all_cited_events_exist(client, api_key):
    session = _setup(client, api_key, "dec-provenance")
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.read",
             "args": {"path": "nope.txt"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()

    event_ids = {e["id"] for e in _events(client, api_key, run["id"])}
    records = _decisions(client, api_key, run_id=run["id"])
    assert records
    for record in records:
        cited = record["evidence"]["trace_event_ids"]
        assert cited, f"record without evidence: {record['id']}"
        assert set(cited) <= event_ids, \
            f"record cites unknown events: {set(cited) - event_ids}"
        assert record["source_event_id"] in event_ids


def test_denied_action_record_explains_policy_stage(client, api_key):
    session = _setup(client, api_key, "dec-denied")
    client.post("/v1/policies", json={
        "name": f"deny-shell-{uuid.uuid4().hex[:8]}", "version": "v1",
        "status": "active", "scope": {"system_ids": ["dec-denied"]},
        "rules": [{"id": "no-shell", "when": {"subject": ["tool_action"],
                                              "tool": "shell.*"},
                   "effect": "deny", "reason": "shell is forbidden"}],
    }, headers=_headers(api_key))

    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "shell.run",
             "args": {"command": "echo hi"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "completed"

    records = _decisions(client, api_key, run_id=run["id"], kind="tool_action")
    denied = [r for r in records if r["decision"] == "DENIED"]
    assert denied
    record = denied[0]
    assert record["policy"]["governance"]["decision"] == "deny"
    assert record["policy"]["governance"]["matched_rules"][0]["rule_id"] == "no-shell"
    assert "shell is forbidden" in record["outcome"]["reason"]

    explanation = client.get(f"/v1/decisions/{record['id']}/explanation",
                             headers=_headers(api_key)).json()
    checks = {c["check"]: c for c in explanation["why"]}
    assert checks["governance_policy"]["passed"] is False
    assert "no-shell" in checks["governance_policy"]["reason"]
    assert checks["permissions"]["passed"] is True   # grant existed; policy denied
    assert checks["outcome"]["passed"] is False
    assert explanation["decision"] == "DENIED"


# --- approval lifecycle convergence --------------------------------------------


def test_approval_flow_records_converge_via_upsert(client, api_key):
    session = _setup(client, api_key, "dec-approval",
                     model_classes=["PUBLIC", "PII"])
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.write",
             "args": {"path": "dec-pii.txt",
                      "content": "contact jane.doe@example.com"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    # PII in write args escalates risk -> approval required
    assert run["state"] == "awaiting_approval", run

    records = _decisions(client, api_key, run_id=run["id"], kind="tool_action")
    assert len(records) == 1
    assert records[0]["decision"] == "APPROVAL_REQUIRED"
    assert records[0]["oversight"]["required"] is True
    assert records[0]["oversight"]["actual"] == "pending"
    record_id = records[0]["id"]
    source_event = records[0]["source_event_id"]

    # human approves and the run resumes
    approval_id = run["pending"]["approval_id"]
    assert client.post(f"/v1/agent/approvals/{approval_id}/decide",
                       json={"decision": "approved", "decided_by": "privacy-lead"},
                       headers=_headers(api_key)).status_code == 200
    run = client.post(f"/v1/agent/runs/{run['id']}/resume",
                      headers=_headers(api_key)).json()
    assert run["state"] == "completed", run

    # the gated record CONVERGED (upsert) to the human decision, and the
    # post-approval execution produced its own record — one logical decision,
    # fully evidenced: gate record (APPROVED by reviewer) + execution record
    records = _decisions(client, api_key, run_id=run["id"], kind="tool_action")
    assert len(records) == 2
    by_id = {r["id"]: r for r in records}
    gate = by_id[record_id]
    assert gate["source_event_id"] == source_event
    assert gate["decision"] == "APPROVED"
    assert gate["oversight"]["required"] is True
    assert gate["oversight"]["actual"] == "approved"
    assert gate["oversight"]["reviewer"] == "privacy-lead"
    assert gate["oversight"]["approval_id"] == approval_id
    executed = [r for r in records if r["decision"] == "EXECUTED"]
    assert executed
    assert executed[0]["oversight"]["reviewer"] == "privacy-lead"

    # rebuild is idempotent: no new records, updates only
    rebuilt = client.post("/v1/decisions/rebuild", json={"run_id": run["id"]},
                          headers=_headers(api_key)).json()
    assert rebuilt["created"] == 0
    assert rebuilt["updated"] == 3  # gate + execution + run_outcome
    assert len(_decisions(client, api_key, run_id=run["id"])) == 3

    # explanation reflects the human decision
    explanation = client.get(f"/v1/decisions/{record_id}/explanation",
                             headers=_headers(api_key)).json()
    checks = {c["check"]: c for c in explanation["why"]}
    assert checks["human_oversight"]["passed"] is True
    assert "privacy-lead" in checks["human_oversight"]["reason"]


def test_denial_by_human_recorded(client, api_key):
    session = _setup(client, api_key, "dec-human-deny",
                     model_classes=["PUBLIC", "PII"])
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.write",
             "args": {"path": "dec-deny.txt",
                      "content": "ssn 111-22-3333 inside"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "awaiting_approval"
    approval_id = run["pending"]["approval_id"]
    client.post(f"/v1/agent/approvals/{approval_id}/decide",
                json={"decision": "denied", "decided_by": "security"},
                headers=_headers(api_key))
    run = client.post(f"/v1/agent/runs/{run['id']}/resume",
                      headers=_headers(api_key)).json()
    assert run["state"] == "blocked"

    records = _decisions(client, api_key, run_id=run["id"])
    tool_records = [r for r in records if r["kind"] == "tool_action"]
    assert tool_records[0]["decision"] == "DENIED"
    assert tool_records[0]["oversight"]["actual"] == "denied"
    assert tool_records[0]["oversight"]["reviewer"] == "security"
    outcome = [r for r in records if r["kind"] == "run_outcome"][0]
    assert outcome["decision"] == "BLOCKED"


# --- model preflight records -----------------------------------------------------


def test_model_preflight_denial_record(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    with open(os.path.join(workspace_root(), "dec-pii-source.txt"), "w") as fh:
        fh.write("card 4111 1111 1111 1111 on file")
    session = _setup(client, api_key, "dec-preflight", model_classes=["PUBLIC"])

    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.read",
             "args": {"path": "dec-pii-source.txt"}},
            {"type": "final", "content": "never"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "blocked"

    records = _decisions(client, api_key, run_id=run["id"], kind="model_preflight")
    assert len(records) == 1
    record = records[0]
    assert record["decision"] == "DENIED"
    assert record["data_class"] == "PII"
    assert record["action"].startswith("model_call:scripted/")

    explanation = client.get(f"/v1/decisions/{record['id']}/explanation",
                             headers=_headers(api_key)).json()
    checks = {c["check"]: c for c in explanation["why"]}
    assert checks["model_data_classification"]["passed"] is False
    assert "ABOVE" in checks["model_data_classification"]["reason"]
    assert checks["model_approval"]["passed"] is True


# --- filters + isolation ----------------------------------------------------------


def test_decision_filters(client, api_key):
    session = _setup(client, api_key, "dec-filters")
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([{"type": "final", "content": "ok"}])},
        headers=_headers(api_key)).json()

    assert _decisions(client, api_key, run_id=run["id"], kind="run_outcome")
    assert not _decisions(client, api_key, run_id=run["id"], kind="tool_action")
    assert _decisions(client, api_key, system_id="dec-filters",
                      decision="completed")
    assert not _decisions(client, api_key, system_id="dec-filters",
                          decision="DENIED")
    assert not _decisions(client, api_key, system_id="no-such-system")


def test_decision_tenant_isolation(client, api_key, other_tenant_api_key):
    session = _setup(client, api_key, "dec-isolated")
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([{"type": "final", "content": "ok"}])},
        headers=_headers(api_key)).json()
    record = _decisions(client, api_key, run_id=run["id"])[0]

    assert not _decisions(client, other_tenant_api_key, run_id=run["id"])
    assert client.get(f"/v1/decisions/{record['id']}",
                      headers=_headers(other_tenant_api_key)).status_code == 404
    assert client.get(f"/v1/decisions/{record['id']}/explanation",
                      headers=_headers(other_tenant_api_key)).status_code == 404
    assert client.post("/v1/decisions/rebuild", json={"run_id": run["id"]},
                       headers=_headers(other_tenant_api_key)).status_code == 404
