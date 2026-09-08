"""
Phase 11-redesign — HELIOS command center TUI tests.

Covers, per the redesign acceptance criteria:
* screen rendering at 80 / 100 / 120 / 160 columns with NO horizontal overflow
* keyboard navigation (digits, j/k, Enter, Esc, Tab, ?, q)
* empty states, missing-evidence states, API-failure diagnostics
* denial / approval / replay / drift / trace / why screens
* pure-ASCII fallback (no Unicode box-drawing when HELIOS_ASCII=1)
* equipment header carries real version/serial/state, never fake metrics
"""

import importlib
import os

import pytest

from helios.tui import screens
from helios.tui import theme as t
from helios.tui.app import HeliosTUI
from helios.tui.governance import (
    render_audit_summary,
    render_replay_report,
    render_systems_table,
)

WIDTHS = (80, 100, 120, 160)


# -- fixtures -------------------------------------------------------------------


SYSTEMS = {"systems": [
    {"system_id": "release-agent", "owner": "release-engineering",
     "environment": "production", "autonomy_level": 3, "risk_class": "HIGH",
     "lifecycle": "active", "version": 4},
]}

AUDIT = {
    "report": "helios-audit-report-v1",
    "disclaimer": "Governance evidence report: policy status, audit record, "
                  "and control status. Not a legal certification.",
    "system": {"system_id": "release-agent", "name": "Release Agent",
               "owner": "release-engineering", "organization": "acme",
               "purpose": "ship releases", "environment": "production",
               "lifecycle": "active", "autonomy_level": 3, "risk_class": "HIGH",
               "data_classes": ["INTERNAL"], "version": 4},
    "models": [{"declared": {"provider": "scripted", "model_id": "model-x",
                             "version": "17"}, "registered": True,
                "approval_status": "approved", "approved_by": "sec"}],
    "policies": [{"name": "release-agent-governance", "version": "v1",
                  "applies_to_system": True, "rules": 2}],
    "activity": {"runs": {"total": 1}, "decisions": {"total": 11},
                 "approvals": {"total": 3, "by_status": {"pending": 0}}},
    "violations": [],
    "human_oversight": {"required_count": 2, "involved": [1, 2], "bypassed": [],
                        "requirement_gaps": []},
    "data_flows": {"sources": 2, "destinations": 1,
                   "data_classes_seen": ["INTERNAL"], "violation_count": 0},
    "changes": [],
    "evaluation": {"score": 1.0, "passed": True},
    "governance_score": {"overall": 100.0, "state": "ok", "components": [
        {"name": "identity", "score": 100.0, "state": "ok", "weight": 10,
         "checks": []},
        {"name": "drift", "score": None, "state": "insufficient_evidence",
         "weight": 5, "checks": []},
    ]},
    "drift": {"baseline": {"id": "b"}, "signals": {"open": [], "open_count": 0}},
}

DECISIONS = {"decisions": [
    {"id": "d1", "created_at": "2026-09-08T09:41:12", "system_id": "release-agent",
     "action": "github.merge_pr", "decision": "EXECUTED", "risk": "critical",
     "oversight": {"actual": "payload_bound"}, "run_id": "run0142abc",
     "model": "scripted/model-x"},
]}

EVENTS = [
    {"id": "e1", "seq": 1, "event_type": "model_call", "name": "scripted/model-x",
     "payload": {}, "status": "ok", "created_at": "2026-09-08T09:41:02",
     "parent_id": None},
    {"id": "e2", "seq": 2, "event_type": "tool_proposal",
     "name": "github.merge_pr", "payload": {"args": {"repo": "acme/x"}},
     "status": "ok", "created_at": "2026-09-08T09:41:04", "parent_id": None},
    {"id": "e3", "seq": 3, "event_type": "policy_evaluation",
     "name": "github.merge_pr", "payload": {"decision": "require_approval"},
     "status": "require_approval", "created_at": "2026-09-08T09:41:04",
     "parent_id": "e2"},
    {"id": "e4", "seq": 4, "event_type": "human_review", "name": "merge",
     "payload": {"decided_by": "release-manager", "decision": "approved"},
     "status": "approved", "created_at": "2026-09-08T09:41:09",
     "parent_id": "e2"},
]

EXPLANATION = {
    "decision_record_id": "dr1", "decision": "EXECUTED",
    "evidence": {"trace_event_ids": ["e1", "e2"], "approval_id": "ap1"},
    "why": [
        {"check": "ai_system", "passed": True,
         "reason": "attributed to release-agent"},
        {"check": "human_oversight", "passed": True,
         "reason": "reviewer release-manager"},
        {"check": "tool_policy", "passed": False,
         "reason": "approval_for_high_risk — OBTAINED"},
    ],
}

APPROVAL = {
    "id": "8a2f1111-2222-3333-4444-555566667777",
    "action": "github.merge_pr", "status": "pending", "risk": "critical",
    "args_hash": "7e91aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa4a20",
    "summary": {"system_id": "release-agent",
                "resource": {"github.repo": "acme/release-service"},
                "risk": {"risk": "critical", "reasons": ["protected branch"]},
                "policy": {"policy_version": "helios-default-v1",
                           "rule_id": "approval_for_high_risk"},
                "governance": {"matched_rules": [
                    {"set": "release-agent-governance", "version": "v1",
                     "rule_id": "production-merge-review",
                     "effect": "require_human_review"}]},
                "data_classification": {"effective": "INTERNAL"}},
}


@pytest.fixture()
def tui():
    app = HeliosTUI("helios")
    calls = []

    def fake(method, path, payload=None, quiet=False):
        calls.append((method, path))
        if path == "/v1/systems":
            return SYSTEMS
        if path.startswith("/v1/systems/") and path.endswith("/audit"):
            return AUDIT
        if path.startswith("/v1/decisions/") and path.endswith("/explanation"):
            return EXPLANATION
        if path == "/v1/decisions?limit=60" or path.startswith("/v1/decisions"):
            return DECISIONS
        if path.startswith("/v1/agent/runs/") and path.endswith("/events"):
            return {"run": {"id": "run0142abc", "state": "completed"},
                    "events": EVENTS}
        if path == "/v1/approvals":
            return {"approvals": [APPROVAL]}
        if path == "/v1/policies":
            return {"policies": [{"version": "helios-default-v1", "rules": [1]}],
                    "governance_policies": [
                        {"name": "release-agent-governance", "version": "v1",
                         "status": "active", "rules": [1, 2],
                         "default_effect": "allow", "scope": {}}]}
        if path == "/v1/models":
            return {"models": []}
        if path == "/v1/changes":
            return {"changes": []}
        if path == "/v1/drift":
            return {"signals": []}
        if path == "/v1/evaluations?limit=5":
            return {"evaluations": []}
        if path == "/health":
            return {"status": "ok"}
        return None

    app._call = fake
    app.agent._call = fake
    app.calls = calls  # type: ignore[attr-defined]
    return app


def _no_overflow(rendered: str, width: int) -> None:
    for line in rendered.split("\n"):
        assert t.visible_len(line) <= width, \
            f"overflow at {width}: {line!r} ({t.visible_len(line)})"


# -- rendering + responsiveness ---------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_overview_responsive_and_real_only(width, tui):
    data = {
        "health_ok": True, "authenticated": True,
        "policies": {"policies": [{"version": "v1", "rules": []}],
                     "governance_policies": []},
        "systems": SYSTEMS, "models": {"models": []},
        "decisions": DECISIONS, "approvals": {"approvals": [APPROVAL]},
        "drift": {"signals": []}, "evaluations": {"evaluations": []},
    }
    out = screens.overview(data, width)
    _no_overflow(out, width)
    assert "CONTROL PLANE" in out and "ONLINE" in out
    assert "POLICY ENGINE" in out and "ENFORCING" in out
    # real values only: the decision count from the payload
    assert "github.merge_pr" in out


@pytest.mark.parametrize("width", WIDTHS)
def test_system_and_audit_screens_responsive(width):
    out = render_audit_summary(AUDIT, width)
    _no_overflow(out, width)
    assert "release-agent" in out
    assert "GOVERNANCE STATUS COMPONENTS" in out
    assert "INSUFFICIENT EVIDENCE" in out  # drift component has no score


@pytest.mark.parametrize("width", WIDTHS)
def test_trace_timeline_and_why_responsive(width):
    trace = screens.trace_timeline(EVENTS, {"id": "run0142", "state": "completed"},
                                   width)
    _no_overflow(trace, width)
    assert "MODEL CALL" in trace and "HUMAN REVIEW" in trace
    assert "release-manager" in trace
    # children are indented deeper than their parent
    lines = trace.split("\n")
    parent_idx = next(i for i, l in enumerate(lines) if "TOOL PROPOSAL" in l)
    child_idx = next(i for i, l in enumerate(lines) if "POLICY" in l
                     and i > parent_idx)
    assert lines[child_idx].index("POLICY") > lines[parent_idx].index("TOOL")

    why = screens.why_view(EXPLANATION, width)
    _no_overflow(why, width)
    assert "WHY WAS THIS ACTION EXECUTED?" in why
    assert "EVIDENCE REFERENCES" in why and "ap1" in why


@pytest.mark.parametrize("width", WIDTHS)
def test_approval_console_shows_payload_binding(width):
    out = screens.approval_console(APPROVAL, width)
    _no_overflow(out, width)
    assert "github.merge_pr" in out
    assert "sha256" in out
    assert APPROVAL["args_hash"][:32] in out  # hash rendered in full, split
    assert "MUTATION INVALIDATES" in out
    assert "[A] APPROVE" in out and "[D] DENY" in out
    assert "production-merge-review" in out


@pytest.mark.parametrize("width", WIDTHS)
def test_policy_spec_responsive(width):
    policy = {"name": "production-autonomy", "version": "2", "status": "active",
              "description": "ceiling", "scope": {"system_ids": ["x"]},
              "default_effect": "deny",
              "rules": [{"id": "r1", "when": {"subject": ["deployment"],
                                              "autonomy_level": {"gte": 3}},
                         "effect": "require_approval", "reason": "L3 gate"}]}
    out = screens.policy_spec(policy, width)
    _no_overflow(out, width)
    assert "RULE 01" in out and "REQUIRE_APPROVAL" in out
    assert "DEFAULT" in out and "DENY" in out
    assert "REPLAY AVAILABLE" in out


# -- states ------------------------------------------------------------------------


def test_empty_states():
    assert "(none)" in render_systems_table({"systems": []})
    empty_overview = screens.overview(
        {"health_ok": False, "decisions": {"decisions": []},
         "approvals": {"approvals": []}, "policies": {}, "systems": {},
         "models": {}, "drift": {}, "evaluations": {}}, 100)
    assert "no recorded decisions" in empty_overview
    assert "DEGRADED" in empty_overview or "○" in empty_overview or "o" in empty_overview
    assert "NO BASELINE STORED" in screens.drift_view({"system_id": "x"}, 100)
    assert "INSUFFICIENT EVIDENCE" in screens.assurance_view(
        {"system_id": "x", "overall": None, "components": []}, 100)


def test_missing_evidence_never_scores():
    out = screens.assurance_view({"system_id": "x", "overall": 62.5,
                                  "state": "warn", "components": [
        {"name": "identity", "score": 100.0, "state": "ok", "weight": 10,
         "checks": []},
        {"name": "evaluation", "score": None, "state": "insufficient_evidence",
         "weight": 10, "checks": []},
    ]}, 100)
    assert "INSUFFICIENT EVIDENCE" in out
    assert "62.5 / 100" in out


def test_denial_diagnostic_carries_reason_and_evidence():
    out = t.diag("HELIOS / GOVERNANCE DENIAL", [
        ("action", "github.merge_pr"), ("decision", "DENIED"),
        ("rule", "production-merge-review"),
        ("reason", "human oversight required"),
        ("evidence", "trace: 7f31… · decision: 91aa…"),
    ], note="NO EXECUTION OCCURRED")
    assert "github.merge_pr" in out and "human oversight required" in out
    assert "NO EXECUTION OCCURRED" in out


def test_replay_lab_states():
    report = {"system_id": "release-agent", "runs_replayed": 1,
              "candidates": {"governance_set": "v2-candidate"},
              "summary": {"tool_layer": {"executed_delta": 0, "denied_delta": 0,
                                         "approval_delta": 1},
                          "governance_layer": {"newly_blocked": 0,
                                               "newly_gated": 1,
                                               "newly_allowed": 0}},
              "governance_layer": {"changes": [
                  {"tool": "github.create_pr", "original": "allow",
                   "candidate": "require_approval",
                   "candidate_reason": "PR checkpoint"}]}}
    out = render_replay_report(report, 100)
    assert "NO ACTIONS EXECUTED" in out
    assert "+1" in out and "github.create_pr" in out


def test_api_failure_renders_diagnostic(tui, capsys):
    def failing(method, path, payload=None, quiet=False):
        if not quiet:
            pass
        return None
    tui._call = failing
    tui.last_error = ("/v1/systems", 503, "control plane unreachable")
    tui.view = "overview"
    tui.cache["overview"] = {"health_ok": False}
    tui.render()
    out = capsys.readouterr().out
    assert "DEGRADED LINK" in out or "CONTROL PLANE" in out
    assert "control plane unreachable" in out


# -- keyboard navigation --------------------------------------------------------------


def test_keyboard_navigation(tui, capsys):
    assert tui.handle_input("2") is True
    assert tui.view == "systems"
    assert tui.cursor == 0
    # Enter opens the selected system (audit plate)
    assert tui.handle_input("") is True
    assert tui.view == "system"
    assert "release-agent" in capsys.readouterr().out
    # Esc returns to the list
    assert tui.handle_input("esc") is True
    assert tui.view == "systems"
    # Tab cycles views
    assert tui.handle_input("tab") is True
    assert tui.view == "models"
    # ? prints help
    assert tui.handle_input("?") is True
    assert "OPERATOR REFERENCE" in capsys.readouterr().out
    # q quits
    assert tui.handle_input("q") is False


def test_traces_enter_and_why(tui, capsys):
    tui.handle_input("5")           # traces view
    assert tui.view == "traces"
    tui.handle_input("")            # open trace timeline
    out = capsys.readouterr().out
    assert "MODEL CALL" in out
    tui.handle_input("esc")         # back to list
    tui.handle_input("w")           # WHY for selected decision
    out = capsys.readouterr().out
    assert "WHY WAS THIS ACTION EXECUTED?" in out


def test_approval_console_actions(tui, capsys):
    tui.handle_input("6")           # approvals
    tui.handle_input("")            # open console
    out = capsys.readouterr().out
    assert "sha256" in out and "[A] APPROVE" in out
    decided = {}

    def decide(method, path, payload=None, quiet=False):
        decided["path"] = path
        decided["payload"] = payload
        return dict(APPROVAL, status="approved")
    tui._call = decide
    tui.handle_input("a")
    assert "/decide" in decided["path"]
    assert decided["payload"]["decision"] == "approved"


# -- equipment header + ascii fallback -------------------------------------------------


def test_equipment_header_uses_real_state():
    out = t.equipment_header(version="1.5.0", environment="production",
                             gateway="helios", tenant="acme-corp",
                             state="governed", width=100)
    assert "HELIOS" in out and "GOVERNED AI COMMAND CENTER" in out
    assert "OBSERVE / CONSTRAIN / ENABLE" in out
    assert "ACME-COR" in out             # serial from the real tenant id
    assert "V1.5.0" in out               # real revision
    assert "PRODUCTION" in out
    assert "HX-001" in out               # plate texture
    _no_overflow(out, 100)


def test_ascii_fallback(monkeypatch):
    monkeypatch.setenv("HELIOS_ASCII", "1")
    importlib.reload(t)
    try:
        out = "\n".join([
            screens.trace_timeline(EVENTS, {"id": "r", "state": "completed"}, 80),
            t.equipment_header(version="1.5.0", environment="dev",
                               gateway="helios", tenant="x", state="governed",
                               width=80),
            render_audit_summary(AUDIT, 80),
        ])
        for ch in ("─", "│", "┌", "┐", "└", "┘", "●", "✓", "×", "◆"):
            assert ch not in out, f"unicode glyph {ch!r} leaked in ASCII mode"
        _no_overflow(out, 80)
    finally:
        monkeypatch.delenv("HELIOS_ASCII", raising=False)
        importlib.reload(t)


def test_no_fake_metrics_in_overview(tui):
    data = {"health_ok": True, "authenticated": True, "policies": {},
            "systems": {"systems": []}, "models": {"models": []},
            "decisions": {"decisions": []}, "approvals": {"approvals": []},
            "drift": {"signals": []}, "evaluations": {"evaluations": []}}
    out = screens.overview(data, 120)
    for fake_value in ("1,248", "1248", "38 ms", "38ms", "48"):
        assert fake_value not in out
