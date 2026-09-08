"""
Governance views for the TUI (Phase 11).

The TUI evolves from an agent interface into a governance control center:

    /systems [q]            the AI system registry
    /system <id>            system overview panel (the governance dashboard)
    /models                 model registry + approval status
    /policies               tool policies + governance policy sets
    /traces [system]        recent decision records (normalized evidence)
    /evaluations [system]   governance/benchmark evaluation runs
    /approvals              unified oversight queue
    /changes [system]       AI change records + lifecycle states
    /drift [system]         baselines + open drift signals
    /audit <system>         governance evidence report
    /replay system <id> [set-id]   system-level candidate replay

Every renderer is a PURE function over an API payload — the tests exercise
them without a network, and nothing renders that the API did not return:
all state is real repository state.
"""

from __future__ import annotations

import json

from helios.tui.ui import bullet, c, error, kv, panel, risk_badge, table

STATE_COLORS = {
    "ok": "green", "active": "green", "approved": "green", "deployed": "green",
    "completed": "green", "clean": "green", "EXECUTED": "green",
    "COMPLETED": "green", "APPROVED": "sea",
    "warn": "yellow", "pending": "yellow", "candidate": "yellow",
    "draft": "dim", "evaluation": "yellow", "replay": "yellow",
    "risk_comparison": "yellow", "human_review": "yellow",
    "APPROVAL_REQUIRED": "yellow", "suspended": "yellow",
    "fail": "red", "denied": "red", "blocked": "red", "expired": "red",
    "rejected": "red", "rolled_back": "red", "DENIED": "red",
    "BLOCKED": "red", "FAILED": "red", "archived": "dim",
    "insufficient_evidence": "dim", "not_registered": "red",
    "deprecated": "dim", "not_approved": "red",
}


def _state(text) -> str:
    text = str(text)
    return c(text, STATE_COLORS.get(text, "fg"), bold=text in STATE_COLORS)


def _mark(passed) -> str:
    if passed is None:
        return c("—", "dim")
    return c("✓", "green") if passed else c("✗", "red")


# --- pure renderers ---------------------------------------------------------


def render_systems_table(data: dict) -> str:
    systems = (data or {}).get("systems") or []
    rows = [
        [c(s["system_id"], "fg", bold=True),
         s.get("owner") or "—",
         s.get("environment", ""),
         f"L{s.get('autonomy_level', '?')}",
         risk_badge(str(s.get("risk_class", "low")).lower()),
         _state(s.get("lifecycle")),
         f"v{s.get('version', 1)}"]
        for s in systems
    ]
    header = c(f"  AI SYSTEM REGISTRY — {len(systems)} system(s)", "dim")
    body = table(["system", "owner", "env", "autonomy", "risk", "lifecycle", "ver"],
                 rows)
    return header + "\n" + body


def render_system_overview(report: dict) -> str:
    """The governance dashboard for one system, from its audit report."""
    system = report.get("system") or {}
    score = report.get("governance_score") or {}
    evaluation = report.get("evaluation")
    drift = (report.get("drift") or {}).get("signals")
    violations = report.get("violations") or []
    activity = report.get("activity") or {}
    approvals = (activity.get("approvals") or {}).get("by_status") or {}
    policies = [p for p in (report.get("policies") or [])
                if p.get("applies_to_system")]

    models_lines = []
    for model in report.get("models") or []:
        declared = model.get("declared") or {}
        label = f"{declared.get('provider', '?')}/{declared.get('model_id', '?')}"
        if declared.get("version"):
            label += f" v{declared['version']}"
        status = model.get("approval_status", "not_registered")
        mark = c("✓ APPROVED", "green") if status == "approved" \
            else c(f"✗ {status.upper()}", "red")
        models_lines.append(f"{label} {mark}")

    overall = score.get("overall")
    governance = (f"{c(str(overall), 'fg', bold=True)} / 100 "
                  f"{_state(score.get('state'))}" if overall is not None
                  else c("insufficient evidence", "dim"))
    if drift is None:
        drift_line = c("— no baseline", "dim")
    elif drift.get("open_count"):
        drift_line = c(f"⚠ DETECTED ({drift['open_count']} open)", "yellow", bold=True)
    else:
        drift_line = c("✓ none open", "green")

    if evaluation and evaluation.get("score") is not None:
        pct = f"{evaluation['score'] * 100:.1f}%"
        verdict = "passed" if evaluation.get("passed") else "failing"
        evaluation_line = f"{c(pct, 'fg', bold=True)} ({verdict})"
    else:
        evaluation_line = c("— not evaluated", "dim")

    lines = [
        kv("SYSTEM", c(system.get("system_id", "?"), "fg", bold=True)
           + c(f"  ({system.get('name', '')})", "dim")),
        kv("OWNER", f"{system.get('owner', '—')}"
           + (f" · {system.get('organization')}" if system.get("organization") else "")),
        kv("PURPOSE", (system.get("purpose") or "—")[:70]),
        kv("RISK", risk_badge(str(system.get("risk_class", "low")).lower())),
        kv("AUTONOMY", f"L{system.get('autonomy_level', '?')}"),
        kv("LIFECYCLE", _state(system.get("lifecycle"))),
        kv("ENVIRONMENT", system.get("environment", "—")),
        kv("MODEL", "; ".join(models_lines) or c("— none declared", "dim")),
        kv("DATA", " / ".join(system.get("data_classes") or []) or c("— none declared", "dim")),
        kv("POLICIES", f"{len(policies)} active"),
        kv("EVALUATION", evaluation_line),
        kv("GOVERNANCE", governance),
        kv("DRIFT", drift_line),
        kv("VIOLATIONS", str(len(violations)) + (" recent" if violations else "")),
        kv("APPROVALS", f"{approvals.get('pending', 0)} active · "
                        f"{sum(approvals.values())} total"),
    ]
    return panel("HELIOS · AI GOVERNANCE CONTROL PLANE", lines)


def render_models_table(data: dict) -> str:
    models = (data or {}).get("models") or []
    rows = [
        [f"{m['provider']}/{m['model_id']}", m.get("version", ""),
         _state(m.get("approval_status")),
         risk_badge(str(m.get("risk_class", "low")).lower()),
         ",".join(m.get("allowed_data_classes") or []) or "—",
         ",".join(m.get("allowed_environments") or []) or "any",
         m.get("approved_by") or "—"]
        for m in models
    ]
    return (c(f"  MODEL REGISTRY — {len(models)} model(s)", "dim") + "\n"
            + table(["model", "ver", "approval", "risk", "data ceiling",
                     "environments", "approved by"], rows))


def render_policies(data: dict) -> str:
    tool_policies = (data or {}).get("policies") or []
    governance = (data or {}).get("governance_policies") or []
    lines = [c("  TOOL POLICIES (broker)", "dim")]
    lines.append(table(
        ["version", "rules"],
        [[p.get("version", ""), str(len(p.get("rules") or []))]
         for p in tool_policies]))
    lines.append(c("  GOVERNANCE POLICY SETS", "dim"))
    rows = []
    for p in governance:
        origin = "builtin" if p.get("name") == "helios-governance-default" else "tenant"
        rows.append([p.get("name", ""), p.get("version", ""), origin,
                     str(len(p.get("rules") or [])),
                     p.get("default_effect", "allow")])
    lines.append(table(["name", "version", "origin", "rules", "default"], rows))
    return "\n".join(lines)


def render_decisions(data: dict) -> str:
    decisions = (data or {}).get("decisions") or []
    rows = [
        [(d.get("created_at") or "")[:19].replace("T", " "),
         d.get("system_id") or "—",
         d.get("action", "")[:28],
         _state(d.get("decision")),
         risk_badge(d.get("risk") or "low"),
         (d.get("oversight") or {}).get("actual", "none"),
         (d.get("run_id") or "")[:8]]
        for d in decisions
    ]
    return (c(f"  DECISION RECORDS — {len(decisions)} shown", "dim") + "\n"
            + table(["time", "system", "action", "decision", "risk",
                     "oversight", "run"], rows))


def render_evaluations(data: dict) -> str:
    evaluations = (data or {}).get("evaluations") or []
    rows = [
        [(e.get("created_at") or "")[:19].replace("T", " "),
         e.get("system_id", ""), e.get("kind", ""),
         _state(e.get("status")),
         f"{e['score'] * 100:.1f}%" if e.get("score") is not None else "—",
         _mark(e.get("passed"))]
        for e in evaluations
    ]
    return (c(f"  EVALUATIONS — {len(evaluations)} shown", "dim") + "\n"
            + table(["time", "system", "kind", "status", "score", "pass"], rows))


def render_approvals_queue(data: dict) -> str:
    approvals = (data or {}).get("approvals") or []
    rows = [
        [a["id"][:8], a.get("action", "")[:34],
         risk_badge(a.get("risk") or "low"),
         _state(a.get("status")),
         a.get("system_id") or "—",
         a.get("decision_kind") or "—",
         a.get("delegated_to") or "—",
         (a.get("expires_at") or "—")[:19].replace("T", " ")]
        for a in approvals
    ]
    return (c(f"  HUMAN OVERSIGHT QUEUE — {len(approvals)} request(s)", "dim")
            + "\n"
            + table(["id", "action", "risk", "status", "system", "kind",
                     "delegate", "expires"], rows))


def render_changes(data: dict) -> str:
    changes = (data or {}).get("changes") or []
    rows = [
        [ch["id"][:8], ch.get("change_type", ""), (ch.get("title") or "")[:30],
         ch.get("author", ""), _state(ch.get("state")),
         (ch.get("created_at") or "")[:19].replace("T", " ")]
        for ch in changes
    ]
    return (c(f"  AI CHANGE RECORDS — {len(changes)} shown", "dim") + "\n"
            + table(["id", "type", "title", "author", "state", "created"], rows))


def render_drift(data: dict) -> str:
    signals = (data or {}).get("signals") or []
    rows = [
        [(s.get("last_seen") or "")[:19].replace("T", " "),
         s.get("system_id", ""), s.get("signal", ""),
         c(s.get("severity", ""), "red" if s.get("severity") == "critical"
           else "yellow" if s.get("severity") == "warning" else "dim"),
         str(s.get("occurrences", 1)), _state(s.get("status")),
         str((s.get("detail") or {}).get("rule", ""))[:40]]
        for s in signals
    ]
    return (c(f"  GOVERNANCE DRIFT — {len(signals)} signal(s)", "dim") + "\n"
            + table(["last seen", "system", "signal", "severity", "×",
                     "status", "rule"], rows))


def render_audit_summary(report: dict) -> str:
    lines = [render_system_overview(report)]
    score = report.get("governance_score") or {}
    lines.append("")
    lines.append(c("  GOVERNANCE STATUS COMPONENTS", "dim"))
    rows = []
    for component in score.get("components") or []:
        state = component.get("state")
        rows.append([
            component["name"],
            f"{component['score']:.0f}" if component.get("score") is not None else "—",
            {"ok": "✓", "warn": "⚠", "fail": "✗",
             "insufficient_evidence": "—"}[state],
            str(component.get("weight")),
        ])
    lines.append(table(["component", "score", "", "weight"], rows))
    oversight = report.get("human_oversight") or {}
    lines.append("")
    lines.append(c("  HUMAN OVERSIGHT", "dim"))
    lines.append(bullet(f"required: {oversight.get('required_count', 0)} · "
                        f"involved: {len(oversight.get('involved') or [])} · "
                        f"bypassed: {len(oversight.get('bypassed') or [])} · "
                        f"gaps: {len(oversight.get('requirement_gaps') or [])}"))
    flows = report.get("data_flows") or {}
    lines.append(c("  DATA FLOWS (event-backed lineage)", "dim"))
    lines.append(bullet(f"sources: {flows.get('sources', 0)} · "
                        f"destinations: {flows.get('destinations', 0)} · "
                        f"classes seen: {', '.join(flows.get('data_classes_seen') or []) or '—'} · "
                        f"violations: {flows.get('violation_count', 0)}"))
    changes = report.get("changes") or []
    if changes:
        lines.append(c("  RECENT CHANGES", "dim"))
        for change in changes[:5]:
            lines.append(bullet(f"{change['change_type']} · {change['title'][:40]} "
                                f"· {_state(change['state'])} · {change['author']}"))
    lines.append("")
    lines.append(c(f"  {report.get('disclaimer', '')[:100]}", "dim"))
    return "\n".join(lines)


def render_replay_report(report: dict) -> str:
    summary = report.get("summary") or {}
    tool = summary.get("tool_layer") or {}
    gov = summary.get("governance_layer") or {}
    lines = [
        c(f"  SYSTEM REPLAY — {report.get('system_id')} "
          f"({report.get('runs_replayed', 0)} run(s))", "dim"),
        kv("candidates", json.dumps(report.get("candidates") or {}, default=str)[:70]),
        kv("tool Δ", f"executed {tool.get('executed_delta', 0):+} · "
                     f"denied {tool.get('denied_delta', 0):+} · "
                     f"approvals {tool.get('approval_delta', 0):+}"),
        kv("governance Δ", f"blocked +{gov.get('newly_blocked', 0)} · "
                           f"gated +{gov.get('newly_gated', 0)} · "
                           f"allowed +{gov.get('newly_allowed', 0)}"),
    ]
    changes = (report.get("governance_layer") or {}).get("changes") or []
    if changes:
        rows = [[ch.get("tool", "")[:30], _state(ch.get("original")),
                 _state(ch.get("candidate")),
                 str(ch.get("candidate_reason", ""))[:44]]
                for ch in changes[:12]]
        lines.append(table(["action", "original", "candidate", "reason"], rows))
    return "\n".join(lines)



# --- the pane ------------------------------------------------------------------


class GovernancePane:
    """Governance commands over the same HTTP transport as the agent pane."""

    def __init__(self, call) -> None:
        self._call = call

    # views

    def systems(self, args: list[str]) -> None:
        query = f"?q={args[0]}" if args else ""
        data = self._call("GET", f"/v1/systems{query}")
        if data:
            print(render_systems_table(data))

    def system_overview(self, args: list[str]) -> None:
        if not args:
            print(error("Usage: /system <system-id>"))
            return
        report = self._call("GET", f"/v1/systems/{args[0]}/audit")
        if report:
            print(render_system_overview(report))

    def models(self) -> None:
        data = self._call("GET", "/v1/models")
        if data:
            print(render_models_table(data))

    def policies(self) -> None:
        data = self._call("GET", "/v1/policies")
        if data:
            print(render_policies(data))

    def traces(self, args: list[str]) -> None:
        query = f"?system_id={args[0]}&limit=30" if args else "?limit=30"
        data = self._call("GET", f"/v1/decisions{query}")
        if data:
            print(render_decisions(data))

    def evaluations(self, args: list[str]) -> None:
        query = f"?system_id={args[0]}" if args else ""
        data = self._call("GET", f"/v1/evaluations{query}")
        if data:
            print(render_evaluations(data))

    def approvals(self) -> None:
        data = self._call("GET", "/v1/approvals")
        if data:
            print(render_approvals_queue(data))

    def changes(self, args: list[str]) -> None:
        query = f"?system_id={args[0]}" if args else ""
        data = self._call("GET", f"/v1/changes{query}")
        if data:
            print(render_changes(data))

    def drift(self, args: list[str]) -> None:
        if args:
            data = self._call("POST", f"/v1/systems/{args[0]}/drift/check")
            if data:
                status = data.get("status")
                if status == "no_baseline":
                    print(error(data.get("detail", "no baseline")))
                    return
                color = "green" if status == "clean" else "yellow"
                print(c(f"  DRIFT CHECK — {status}", color, bold=True))
                for finding in data.get("findings") or []:
                    print(bullet(f"{finding['signal']} [{finding['severity']}]: "
                                 f"{finding['detail'].get('rule', '')}"))
                if not data.get("findings"):
                    print(c("  ✓ within baseline thresholds", "green"))
            listing = self._call("GET", f"/v1/drift?system_id={args[0]}")
            if listing:
                print(render_drift(listing))
        else:
            data = self._call("GET", "/v1/drift")
            if data:
                print(render_drift(data))

    def audit(self, args: list[str]) -> None:
        if not args:
            print(error("Usage: /audit <system-id>"))
            return
        report = self._call("GET", f"/v1/systems/{args[0]}/audit")
        if report:
            print(render_audit_summary(report))

    def replay_system(self, args: list[str]) -> None:
        if not args:
            print(error("Usage: /replay system <system-id> [candidate-set-id]"))
            return
        system_id = args[0]
        payload = {}
        if len(args) > 1:
            payload["candidate_governance_set_id"] = args[1]
        data = self._call("POST", f"/v1/replay/systems/{system_id}", payload)
        if data:
            print(render_replay_report(data))
