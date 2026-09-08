"""
Phase 7 — Human oversight tests (POLICY/EVIDENCE planes).

Covers: approval expiration (sweep + broker refusal + decide-after-expiry),
session-approval scope binding (environment/system/max_uses/expiry), comments,
delegation enforcement (only the delegate decides), escalation, HUMAN_REVIEW
trace evidence, oversight compliance analysis (involved / required / bypassed /
requirement gaps), and payload-tampering invalidation (V1 invariant retest
under the expanded model).
"""

import json
import os
from datetime import datetime, timedelta, timezone

from helios.db import SessionLocal
from helios.models import ApprovalRequest, DecisionRecord
from helios.tools.filesystem import workspace_root


def _headers(api_key):
    return {"X-Helios-API-Key": api_key}


def _script(steps):
    return "run this plan\nSCRIPT:" + json.dumps(steps)


def _setup(client, api_key, system_id, *, environment="dev", oversight=None,
           model_classes=None):
    r = client.post("/v1/systems", json={
        "system_id": system_id, "name": system_id, "owner": "team",
        "environment": environment, "autonomy_level": 2,
        "oversight": oversight or {},
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


def _pii_write_run(client, api_key, session, path="ov-write.txt"):
    """A write whose args contain PII -> risk HIGH -> approval required."""
    return client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.write",
             "args": {"path": path, "content": "email jane.doe@example.com"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()


# --- expiration ------------------------------------------------------------------


def test_approval_expiration_blocks_execution_and_decide(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    session = _setup(client, api_key, "ov-expire", model_classes=["PUBLIC", "PII"])
    run = _pii_write_run(client, api_key, session, path="ov-expire.txt")
    assert run["state"] == "awaiting_approval"
    approval_id = run["pending"]["approval_id"]

    # approve with an immediate expiry
    decided = client.post(f"/v1/agent/approvals/{approval_id}/decide", json={
        "decision": "approved", "decided_by": "ops", "expires_in_s": 0,
    }, headers=_headers(api_key))
    assert decided.status_code == 200
    assert decided.json()["expires_at"]

    # the sweep marks it expired; the queue reflects reality
    listing = client.get("/v1/approvals", params={"status": "expired"},
                         headers=_headers(api_key)).json()
    assert approval_id in {a["id"] for a in listing["approvals"]}

    # resume cannot execute on an expired approval: the broker refuses to
    # reuse it and re-gates the action with a FRESH pending request
    run2 = client.post(f"/v1/agent/runs/{run['id']}/resume",
                       headers=_headers(api_key)).json()
    assert run2["state"] == "awaiting_approval"
    assert run2["pending"]["approval_id"] != approval_id

    # a pending-but-expired approval cannot be decided late
    db = SessionLocal()
    try:
        fresh = db.get(ApprovalRequest, run2["pending"]["approval_id"])
        fresh.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()
    finally:
        db.close()
    client.get("/v1/approvals", headers=_headers(api_key))  # triggers sweep
    late = client.post(f"/v1/agent/approvals/{run2['pending']['approval_id']}/decide",
                       json={"decision": "approved", "decided_by": "ops"},
                       headers=_headers(api_key))
    assert late.status_code == 409
    assert "expired" in late.json()["detail"]


# --- session approval scope -------------------------------------------------------


def test_session_approval_max_uses(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    session = _setup(client, api_key, "ov-maxuses",
                     model_classes=["PUBLIC", "PII"])
    run = _pii_write_run(client, api_key, session, path="ov-mu-1.txt")
    assert run["state"] == "awaiting_approval"
    approval_id = run["pending"]["approval_id"]

    decided = client.post(f"/v1/agent/approvals/{approval_id}/decide", json={
        "decision": "approve_session", "decided_by": "ops", "max_uses": 1,
    }, headers=_headers(api_key))
    assert decided.status_code == 200

    # first write consumes the standing approval and executes
    run = client.post(f"/v1/agent/runs/{run['id']}/resume",
                      headers=_headers(api_key)).json()
    assert run["state"] == "completed", run

    session_detail = client.get(f"/v1/agent/sessions/{session['id']}",
                                headers=_headers(api_key)).json()
    entry = session_detail["session_approvals"][0]
    assert entry["max_uses"] == 1 and entry["uses"] == 1
    assert entry["environment"] == "dev"          # scope-bound by default
    assert entry["system_id"] == "ov-maxuses"

    # a second, different PII write is gated again (max_uses exhausted)
    run2 = _pii_write_run(client, api_key, session, path="ov-mu-2.txt")
    assert run2["state"] == "awaiting_approval"


def test_session_approval_expiry(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    session = _setup(client, api_key, "ov-sess-exp",
                     model_classes=["PUBLIC", "PII"])
    run = _pii_write_run(client, api_key, session, path="ov-se-1.txt")
    approval_id = run["pending"]["approval_id"]
    client.post(f"/v1/agent/approvals/{approval_id}/decide", json={
        "decision": "approve_session", "decided_by": "ops", "expires_in_s": 0,
    }, headers=_headers(api_key))

    # the standing approval is already expired: resume re-gates with a fresh
    # payload-bound request instead of executing
    run2 = client.post(f"/v1/agent/runs/{run['id']}/resume",
                       headers=_headers(api_key)).json()
    assert run2["state"] == "awaiting_approval"
    assert run2["pending"]["approval_id"] != approval_id


# --- comments, delegation, escalation ------------------------------------------------


def test_comments_delegation_and_escalation(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    session = _setup(client, api_key, "ov-delegate",
                     model_classes=["PUBLIC", "PII"])
    run = _pii_write_run(client, api_key, session, path="ov-del.txt")
    approval_id = run["pending"]["approval_id"]

    # comment on the pending request (audit thread)
    commented = client.post(f"/v1/agent/approvals/{approval_id}/comments", json={
        "by": "ops", "text": "checking with privacy team"},
        headers=_headers(api_key))
    assert commented.status_code == 200
    assert commented.json()["comments"][0]["text"] == "checking with privacy team"

    # delegate to alice
    delegated = client.post(f"/v1/agent/approvals/{approval_id}/delegate", json={
        "to": "alice", "by": "ops", "reason": "on call"},
        headers=_headers(api_key))
    assert delegated.status_code == 200
    assert delegated.json()["delegated_to"] == "alice"
    assert any("delegated to alice" in c["text"]
               for c in delegated.json()["comments"])

    # bob may NOT decide a delegation bound to alice
    refused = client.post(f"/v1/agent/approvals/{approval_id}/decide", json={
        "decision": "approved", "decided_by": "bob"},
        headers=_headers(api_key))
    assert refused.status_code == 403
    assert "alice" in refused.json()["detail"]

    # alice escalates to a manager (recorded with reason)
    escalated = client.post(f"/v1/agent/approvals/{approval_id}/escalate", json={
        "to": "manager", "by": "alice", "reason": "above my pay grade"},
        headers=_headers(api_key))
    assert escalated.status_code == 200
    assert escalated.json()["delegated_to"] == "manager"
    assert escalated.json()["summary"]["escalated"] is True

    # escalation without a reason is refused
    bad = client.post(f"/v1/agent/approvals/{approval_id}/escalate", json={
        "to": "cto", "by": "manager"}, headers=_headers(api_key))
    assert bad.status_code == 400

    # the manager decides; decision + comment land in the audit trail
    decided = client.post(f"/v1/agent/approvals/{approval_id}/decide", json={
        "decision": "approved", "decided_by": "manager",
        "comment": "reviewed evidence, ok"}, headers=_headers(api_key))
    assert decided.status_code == 200
    body = decided.json()
    assert body["decision_kind"] == "approval"
    assert any("reviewed evidence" in c["text"] for c in body["comments"])

    run2 = client.post(f"/v1/agent/runs/{run['id']}/resume",
                       headers=_headers(api_key)).json()
    assert run2["state"] == "completed"

    # HUMAN_REVIEW evidence sits under the proposal in the trace hierarchy
    events = client.get(f"/v1/agent/runs/{run['id']}/events",
                        headers=_headers(api_key)).json()["events"]
    reviews = [e for e in events if e["event_type"] == "human_review"]
    assert reviews
    review = reviews[0]
    assert review["payload"]["decided_by"] == "manager"
    assert review["payload"]["decision"] == "approved"
    proposal_ids = {e["id"] for e in events if e["event_type"] == "tool_proposal"}
    assert review["parent_id"] in proposal_ids


def test_denial_recorded_with_comment_and_kind(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    session = _setup(client, api_key, "ov-deny", model_classes=["PUBLIC", "PII"])
    run = _pii_write_run(client, api_key, session, path="ov-deny.txt")
    approval_id = run["pending"]["approval_id"]
    decided = client.post(f"/v1/agent/approvals/{approval_id}/decide", json={
        "decision": "denied", "decided_by": "sec", "comment": "no PII on disk"},
        headers=_headers(api_key))
    assert decided.json()["decision_kind"] == "denial"
    run2 = client.post(f"/v1/agent/runs/{run['id']}/resume",
                       headers=_headers(api_key)).json()
    assert run2["state"] == "blocked"
    events = client.get(f"/v1/agent/runs/{run['id']}/events",
                        headers=_headers(api_key)).json()["events"]
    reviews = [e for e in events if e["event_type"] == "human_review"]
    assert reviews and reviews[0]["payload"]["decision"] == "denied"


# --- payload tampering (V1 invariant under the expanded model) ------------------------


def test_payload_tampering_still_invalidates_approval(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    # production session: shell.run is high-risk there -> approval required
    session = _setup(client, api_key, "ov-tamper", environment="production")
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "shell.run",
             "args": {"command": "echo audited > tamper.log"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "awaiting_approval"
    approval_id = run["pending"]["approval_id"]
    original_hash = run["pending"]["args_hash"]

    client.post(f"/v1/agent/approvals/{approval_id}/decide", json={
        "decision": "approved", "decided_by": "ops"}, headers=_headers(api_key))

    # tamper with the pending payload behind HELIOS's back
    db = SessionLocal()
    try:
        from helios.models import AgentRun
        row = db.get(AgentRun, run["id"])
        pending = dict(row.pending)
        pending["args"] = {"command": "echo pwned > /tmp/pwned"}
        row.pending = pending
        db.commit()
    finally:
        db.close()

    # resume re-invokes with the tampered args -> hash no longer matches the
    # approval -> a NEW pending request is created, nothing executes
    run2 = client.post(f"/v1/agent/runs/{run['id']}/resume",
                       headers=_headers(api_key)).json()
    assert run2["state"] == "awaiting_approval"
    assert run2["pending"]["approval_id"] != approval_id
    assert run2["pending"]["args_hash"] != original_hash
    assert not os.path.exists("/tmp/pwned")


# --- compliance analysis ----------------------------------------------------------------


def test_oversight_analysis_involved_and_required(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    session = _setup(client, api_key, "ov-analysis",
                     model_classes=["PUBLIC", "PII"],
                     oversight={"high_risk": "approval"})
    run = _pii_write_run(client, api_key, session, path="ov-an.txt")
    approval_id = run["pending"]["approval_id"]
    client.post(f"/v1/agent/approvals/{approval_id}/decide", json={
        "decision": "approved", "decided_by": "privacy-lead"},
        headers=_headers(api_key))
    run = client.post(f"/v1/agent/runs/{run['id']}/resume",
                      headers=_headers(api_key)).json()
    assert run["state"] == "completed"

    report = client.get("/v1/systems/ov-analysis/oversight",
                        headers=_headers(api_key)).json()
    assert report["system_id"] == "ov-analysis"
    assert report["requirements"] == {"high_risk": "approval"}
    assert report["required_count"] >= 1
    assert any(i["reviewer"] == "privacy-lead" for i in report["involved"])
    assert report["bypassed"] == []
    assert report["compliant"] is True
    assert report["approvals"]["total"] >= 1
    assert report["approvals"]["avg_decision_time_s"] is not None


def test_oversight_analysis_flags_requirement_gap(client, api_key):
    """
    The org declared 'writes need approval', but the active policies allowed a
    dev write without oversight — HELIOS surfaces the gap between declared
    requirements and enforced reality, citing the record.
    """
    os.makedirs(workspace_root(), exist_ok=True)
    session = _setup(client, api_key, "ov-gap", oversight={"writes": "approval"})
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.write",
             "args": {"path": "ov-gap.txt", "content": "plain notes"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "completed"

    report = client.get("/v1/systems/ov-gap/oversight",
                        headers=_headers(api_key)).json()
    assert report["compliant"] is False
    gaps = report["requirement_gaps"]
    assert gaps and gaps[0]["requirement"] == "writes"
    assert gaps[0]["action"] == "fs.write"
    assert gaps[0]["record_id"]
    # the gap cites a real decision record
    records = client.get("/v1/decisions", params={"system_id": "ov-gap"},
                         headers=_headers(api_key)).json()["decisions"]
    assert gaps[0]["record_id"] in {r["id"] for r in records}


def test_oversight_analysis_detects_bypass(client, api_key):
    """A required-oversight execution with no human involvement is flagged."""
    session = _setup(client, api_key, "ov-bypass")
    db = SessionLocal()
    try:
        # fabricate the enforcement-failure shape directly (the broker itself
        # cannot produce it — that is the point of the detector)
        db.add(DecisionRecord(
            tenant_id=_tenant_of(db, api_key), system_id="ov-bypass",
            run_id="bypass-run", source_event_id="bypass-event",
            kind="tool_action", action="fs.write", decision="EXECUTED",
            actor={"environment": "dev"},
            oversight={"required": True, "actual": "none",
                       "reviewer": None, "approval_id": None},
            evidence={"trace_event_ids": ["bypass-event"]},
            policy={}, outcome={}, data_classification={},
        ))
        db.commit()
    finally:
        db.close()

    report = client.get("/v1/systems/ov-bypass/oversight",
                        headers=_headers(api_key)).json()
    assert report["compliant"] is False
    assert report["bypassed"] and report["bypassed"][0]["run_id"] == "bypass-run"


def _tenant_of(db, api_key_raw):
    from helios.cli import get_or_create_tenant
    return get_or_create_tenant(db, "acme").id
