"""
Phase 11 — Governance dashboard tests: TUI renderers (pure functions over API
payloads) and the audit report API.

Renderers are tested without a network: every view must render exactly what
the API payload contains — no invented state, graceful on empties.
"""

import json
import os

from helios.tui.governance import (
    render_approvals_queue,
    render_audit_summary,
    render_changes,
    render_decisions,
    render_drift,
    render_evaluations,
    render_models_table,
    render_policies,
    render_replay_report,
    render_system_overview,
    render_systems_table,
)


def _headers(api_key):
    return {"X-Helios-API-Key": api_key}


def _script(steps):
    return "run this plan\nSCRIPT:" + json.dumps(steps)


# --- renderers ------------------------------------------------------------------


def test_render_systems_table():
    out = render_systems_table({"systems": [
        {"system_id": "claims-agent", "owner": "claims-platform",
         "environment": "production", "autonomy_level": 3,
         "risk_class": "HIGH", "lifecycle": "active", "version": 4},
    ]})
    assert "claims-agent" in out
    assert "claims-platform" in out
    assert "L3" in out
    assert "active" in out
    # empty registry renders honestly
    assert "none" in render_systems_table({"systems": []}).lower()


def test_render_system_overview_from_audit_report():
    report = {
        "system": {"system_id": "claims-agent", "name": "Claims Triage",
                   "owner": "claims-platform", "organization": "acme",
                   "purpose": "claims triage", "environment": "production",
                   "lifecycle": "active", "autonomy_level": 3,
                   "risk_class": "HIGH", "data_classes": ["PII", "CONFIDENTIAL"],
                   "version": 7},
        "models": [{"declared": {"provider": "anthropic", "model_id": "model-x",
                                 "version": "17"},
                    "registered": True, "approval_status": "approved",
                    "approved_by": "sec"}],
        "policies": [{"name": "production-autonomy", "version": "v7",
                      "applies_to_system": True, "rules": 3, "scope": {}}],
        "evaluation": {"score": 0.942, "passed": True},
        "governance_score": {"overall": 91.0, "state": "ok", "components": []},
        "drift": {"baseline": {"id": "b1"}, "signals": {"open": [{"id": "s1"}],
                                                         "open_count": 1}},
        "violations": [{"record_id": "r1"}, {"record_id": "r2"}],
        "activity": {"approvals": {"by_status": {"pending": 1, "approved": 5}}},
    }
    out = render_system_overview(report)
    assert "claims-agent" in out
    assert "claims-platform" in out
    assert "L3" in out
    assert "model-x v17" in out and "APPROVED" in out
    assert "PII / CONFIDENTIAL" in out
    assert "1 active" in out                 # policies
    assert "94.2%" in out                    # evaluation
    assert "91.0 / 100" in out               # governance
    assert "DETECTED" in out                 # drift warning
    assert "2 recent" in out                 # violations
    assert "1 active" in out                 # approvals


def test_render_overview_handles_missing_evidence():
    out = render_system_overview({
        "system": {"system_id": "bare", "owner": "o", "lifecycle": "draft",
                   "autonomy_level": 1, "risk_class": "LOW"},
        "models": [], "policies": [], "evaluation": None,
        "governance_score": {"overall": None, "state": "insufficient_evidence"},
        "drift": {"baseline": None, "signals": None},
        "violations": [], "activity": {"approvals": {"by_status": {}}},
    })
    assert "insufficient evidence" in out
    assert "no baseline" in out
    assert "not evaluated" in out
    assert "none declared" in out


def test_render_models_policies_decisions():
    models = render_models_table({"models": [
        {"provider": "anthropic", "model_id": "claude-x", "version": "17",
         "approval_status": "approved", "risk_class": "MEDIUM",
         "allowed_data_classes": ["PUBLIC", "PII"],
         "allowed_environments": ["staging"], "approved_by": "sec"},
        {"provider": "openai", "model_id": "gpt-y", "version": "latest",
         "approval_status": "not_approved", "risk_class": "HIGH",
         "allowed_data_classes": [], "allowed_environments": [],
         "approved_by": None},
    ]})
    assert "claude-x" in models and "approved" in models
    assert "not_approved" in models
    assert "PUBLIC,PII" in models

    policies = render_policies({
        "policies": [{"version": "helios-default-v1", "rules": [1, 2, 3]}],
        "governance_policies": [
            {"name": "helios-governance-default", "version": "v1", "rules": [1]},
        ],
    })
    assert "helios-default-v1" in policies
    assert "builtin" in policies

    decisions = render_decisions({"decisions": [
        {"created_at": "2026-09-08T10:00:00", "system_id": "claims-agent",
         "action": "github.merge_pr", "decision": "APPROVED", "risk": "critical",
         "oversight": {"actual": "approved"}, "run_id": "abcdefgh12345"},
    ]})
    assert "github.merge_pr" in decisions
    assert "APPROVED" in decisions


def test_render_queue_views_and_empty_states():
    approvals = render_approvals_queue({"approvals": [
        {"id": "aaaaaaaa1234", "action": "github.merge_pr", "risk": "critical",
         "status": "pending", "system_id": "release-agent",
         "decision_kind": "approval", "delegated_to": None,
         "expires_at": "2026-09-09T10:00:00"},
    ]})
    assert "release-agent" in approvals and "pending" in approvals
    assert "none" in render_approvals_queue({"approvals": []}).lower()

    changes = render_changes({"changes": [
        {"id": "cccccccc1234", "change_type": "policy", "title": "Gate writes",
         "author": "lead", "state": "human_review",
         "created_at": "2026-09-08T09:00:00"},
    ]})
    assert "Gate writes" in changes and "human_review" in changes

    drift = render_drift({"signals": [
        {"id": "d1", "system_id": "claims-agent", "signal": "governance_drift",
         "severity": "critical", "occurrences": 3, "status": "open",
         "detail": {"rule": "approval_rate drop > 0.2"},
         "last_seen": "2026-09-08T11:00:00"},
    ]})
    assert "governance_drift" in drift and "critical" in drift

    evaluations = render_evaluations({"evaluations": [
        {"created_at": "2026-09-08T08:00:00", "system_id": "claims-agent",
         "kind": "governance", "status": "completed", "score": 0.94,
         "passed": True},
    ]})
    assert "94.0%" in evaluations


def test_render_replay_report():
    out = render_replay_report({
        "system_id": "release-agent", "runs_replayed": 3,
        "candidates": {"governance_set": "prod-writes-v2"},
        "summary": {"tool_layer": {"executed_delta": -3, "denied_delta": 0,
                                   "approval_delta": 2},
                    "governance_layer": {"newly_blocked": 0, "newly_gated": 2,
                                         "newly_allowed": 0}},
        "governance_layer": {"changes": [
            {"tool": "github.merge_pr", "original": "allow",
             "candidate": "require_approval",
             "candidate_reason": "production merges need eyes"}]},
    })
    assert "release-agent" in out
    assert "-3" in out and "+2" in out
    assert "github.merge_pr" in out


# --- audit report API ---------------------------------------------------------------


def test_audit_report_api(client, api_key):
    os.makedirs("/tmp", exist_ok=True)
    from helios.tools.filesystem import workspace_root
    os.makedirs(workspace_root(), exist_ok=True)

    r = client.post("/v1/systems", json={
        "system_id": "audit-agent", "name": "Audit Agent", "owner": "audit-team",
        "organization": "acme", "purpose": "release engineering",
        "description": "Audited agent", "environment": "dev",
        "autonomy_level": 2, "risk_class": "MEDIUM",
        "models": [{"provider": "scripted", "model_id": "audit-model"}],
        "tools": ["fs.read"], "data_classes": ["INTERNAL"],
        "policies": ["production-autonomy"],
        "oversight": {"writes": "approval"},
    }, headers=_headers(api_key))
    assert r.status_code == 201, r.text
    client.post("/v1/models", json={
        "provider": "scripted", "model_id": "audit-model",
        "approval_status": "approved", "approved_by": "sec",
    }, headers=_headers(api_key))
    session = client.post("/v1/agent/sessions", json={
        "name": "s", "model_provider": "scripted", "model_id": "audit-model",
        "system_id": "audit-agent", "environment": "dev",
    }, headers=_headers(api_key)).json()
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.write",
             "args": {"path": "audit-note.txt", "content": "notes"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "completed", run
    client.post("/v1/systems/audit-agent/evaluate", json={},
                headers=_headers(api_key))
    client.post("/v1/systems/audit-agent/drift/baseline", json={"by": "sre"},
                headers=_headers(api_key))

    report = client.get("/v1/systems/audit-agent/audit",
                        headers=_headers(api_key))
    assert report.status_code == 200, report.text
    report = report.json()

    assert report["report"] == "helios-audit-report-v1"
    assert "not a legal or regulatory compliance certification" in report["disclaimer"]

    system = report["system"]
    assert system["system_id"] == "audit-agent"
    assert system["owner"] == "audit-team"
    assert system["purpose"] == "release engineering"
    assert system["oversight_requirements"] == {"writes": "approval"}

    # models resolved against the registry
    model = report["models"][0]
    assert model["registered"] is True
    assert model["approval_status"] == "approved"
    assert model["approved_by"] == "sec"

    # activity derived from real records
    assert report["activity"]["runs"]["total"] == 1
    assert report["activity"]["decisions"]["total"] >= 2
    assert report["activity"]["decisions"]["by_decision"].get("EXECUTED", 0) >= 1

    # oversight analysis flags the declared-requirement gap (write executed
    # without approval while {"writes": "approval"} is declared)
    oversight = report["human_oversight"]
    assert oversight["requirements"] == {"writes": "approval"}
    assert oversight["requirement_gaps"], "declared requirement gap missing"

    # score, drift, evaluation sections present and consistent
    assert report["governance_score"]["components"]
    assert report["drift"]["baseline"] is not None
    assert report["drift"]["signals"]["open_count"] == 0
    assert report["evaluation"] is not None
    assert report["evaluation"]["score"] is not None
    assert "passed" in report["evaluation"]

    # data flows are event-backed
    assert report["data_flows"]["destinations"] >= 1
    assert "INTERNAL" in report["data_flows"]["data_classes_seen"] or \
        report["data_flows"]["sources"] >= 0

    # the audit summary renderer consumes the real report
    out = render_audit_summary(report)
    assert "audit-agent" in out
    assert "GOVERNANCE STATUS COMPONENTS" in out
    assert "HUMAN OVERSIGHT" in out
    assert "DATA FLOWS" in out


def test_audit_report_tenant_isolation_and_404(client, api_key, other_tenant_api_key):
    assert client.get("/v1/audit/audit-agent",
                      headers=_headers(other_tenant_api_key)).status_code == 404
    assert client.get("/v1/audit/no-such-system",
                      headers=_headers(api_key)).status_code == 404
    assert client.get("/v1/audit/audit-agent",
                      headers=_headers(api_key)).status_code == 200
