"""
Governance view renderers — pure functions over API payloads.

Every renderer consumes ONLY what the HELIOS APIs returned; nothing on
screen is invented (§ no fake data). Visual language comes from
`tui.theme`: equipment plates, 1px rules, instrument glyphs, dense
monospace metadata. Each renderer degrades to plain ASCII and never
overflows the requested width.

These renderers are the data→text layer shared by the command-center
views (`tui/screens.py`) and the slash-command console (`tui/app.py`).
"""

from __future__ import annotations

import json

from helios.tui import theme as t

STATE_COLOR = {
    "ok": "accent", "active": "accent", "approved": "accent",
    "deployed": "accent", "completed": "accent", "clean": "accent",
    "EXECUTED": "accent", "COMPLETED": "accent", "APPROVED": "sea",
    "warn": "warn", "pending": "warn", "candidate": "warn",
    "evaluation": "warn", "replay": "warn", "risk_comparison": "warn",
    "human_review": "warn", "APPROVAL_REQUIRED": "warn",
    "suspended": "warn", "draft": "dim",
    "fail": "crit", "denied": "crit", "blocked": "crit", "expired": "crit",
    "rejected": "crit", "rolled_back": "crit", "DENIED": "crit",
    "BLOCKED": "crit", "FAILED": "crit", "archived": "dim",
    "insufficient_evidence": "dim", "not_registered": "crit",
    "deprecated": "dim", "not_approved": "crit",
}


def _render_state(text: str) -> str:
    return t.c(text, STATE_COLOR.get(text, "text"))


def state(text) -> str:
    return _render_state(str(text))


def _none() -> str:
    return t.c("  (none)", "faint")


# --- registry views ----------------------------------------------------------


def render_systems_table(data: dict, width: int | None = None) -> str:
    systems = (data or {}).get("systems") or []
    if not systems:
        return t.section("IDENTITY / AI SYSTEM REGISTRY", width) + "\n" + _none()
    rows = [
        [s["system_id"], s.get("owner") or "—", s.get("environment", ""),
         f"L{s.get('autonomy_level', '?')}",
         str(s.get("risk_class", "")).upper(),
         str(s.get("lifecycle", "")), f"v{s.get('version', 1)}"]
        for s in systems
    ]
    return (t.section(f"IDENTITY / AI SYSTEM REGISTRY — {len(systems)} REGISTERED",
                      width)
            + "\n" + t.table(["system", "owner", "env", "autonomy", "risk",
                              "lifecycle", "rev"], rows, width,
                              drop=["rev", "env", "risk"]))


def render_system_overview(report: dict, width: int | None = None) -> str:
    """The operator plate for one AI system (audit-report backed)."""
    width = width or t.W()
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
        if status == "approved":
            mark = t.c(f"{t.GL['allow']} APPROVED", "accent")
        else:
            mark = t.c(f"{t.GL['deny']} {status.upper()}", "crit")
        models_lines.append(f"{label} {mark}")

    if score.get("overall") is not None:
        governance = (f"{t.c(str(score['overall']), 'text', bold=True)} / 100 "
                      + state(score.get("state")))
    else:
        governance = t.c("insufficient evidence", "dim")

    if evaluation and evaluation.get("score") is not None:
        pct = f"{evaluation['score'] * 100:.1f}%"
        verdict = "passed" if evaluation.get("passed") else "failing"
        evaluation_line = f"{t.c(pct, 'text', bold=True)} ({verdict})"
    else:
        evaluation_line = t.c("— not evaluated", "dim")

    if drift is None:
        drift_line = t.c("— no baseline", "dim")
    elif drift.get("open_count"):
        drift_line = t.c(f"{t.GL['gate']} DETECTED ({drift['open_count']} open)",
                         "warn", bold=True)
    else:
        drift_line = t.c(f"{t.GL['allow']} none open", "accent")

    data_classes = system.get("data_classes") or []
    models_block = ([f"  {m}" for m in models_lines]
                    or [t.c("  — none declared", "dim")])
    policy_block = [f"    {t.GL['bullet']} {p.get('name')}@{p.get('version')}"
                    for p in policies[:6]]
    lines = [
        t.section(f"HELIOS / SYSTEM / {system.get('system_id', '?')}", width),
        "",
        t.kv("system", t.c(str(system.get('system_id', '?')), 'text', bold=True)
             + t.c(f"  ({system.get('name', '')})", "dim")),
        t.kv("owner", f"{system.get('owner', '—')}"
             + (f" · {system.get('organization')}"
                if system.get("organization") else "")),
        t.kv("purpose", t.fit(str(system.get("purpose") or "—"), width - 20)),
        t.kv("risk", t.glyphed("deny" if system.get("risk_class") in
                               ("HIGH", "CRITICAL") else "allow",
                               str(system.get("risk_class", "")).upper())),
        t.kv("autonomy", f"L{system.get('autonomy_level', '?')}"),
        t.kv("lifecycle", state(system.get("lifecycle"))),
        t.kv("environment", str(system.get("environment", "—"))),
        "",
        t.section("MODEL", width),
        *models_block,
        t.kv("data", " / ".join(data_classes) if data_classes
             else t.c("— none declared", "dim")),
        "",
        t.section("POLICY", width),
        t.kv("policies", f"{len(policies)} active"),
        *policy_block,
        "",
        t.section("ASSURANCE", width),
        t.kv("evaluation", evaluation_line),
        t.kv("governance", governance),
        t.kv("drift", drift_line),
        t.kv("violations", f"{len(violations)}"
             + (" recent" if violations else "")),
        t.kv("approvals", f"{approvals.get('pending', 0)} active · "
                          f"{sum(approvals.values())} total"),
    ]
    return "\n".join(t.fit(line, width) for line in lines)


def render_models_table(data: dict, width: int | None = None) -> str:
    models = (data or {}).get("models") or []
    if not models:
        return t.section("IDENTITY / MODEL REGISTRY", width) + "\n" + _none()
    rows = [
        [f"{m['provider']}/{m['model_id']}", m.get("version", ""),
         str(m.get("approval_status", "")),
         str(m.get("risk_class", "")).upper(),
         ",".join(m.get("allowed_data_classes") or []) or "—",
         ",".join(m.get("allowed_environments") or []) or "any",
         m.get("approved_by") or "—"]
        for m in models
    ]
    return (t.section(f"IDENTITY / MODEL REGISTRY — {len(models)} REGISTERED",
                      width)
            + "\n" + t.table(["model", "rev", "approval", "risk",
                              "data ceiling", "environments", "approved by"],
                             rows, width,
                             drop=["approved by", "environments", "risk"]))


def render_policies(data: dict, width: int | None = None) -> str:
    width = width or t.W()
    tool_policies = (data or {}).get("policies") or []
    governance = (data or {}).get("governance_policies") or []
    lines = [t.section("POLICY / TOOL POLICIES (BROKER)", width)]
    lines.extend(t.table(["version", "rules"],
                         [[p.get("version", ""), str(len(p.get("rules") or []))]
                          for p in tool_policies], width).split("\n"))
    lines.append("")
    lines.append(t.section("POLICY / GOVERNANCE SETS", width))
    rows = []
    for p in governance:
        origin = ("builtin" if p.get("name") == "helios-governance-default"
                  else "tenant")
        rows.append([p.get("name", ""), p.get("version", ""), origin,
                     str(len(p.get("rules") or [])),
                     p.get("default_effect", "allow"),
                     str(p.get("status", ""))])
    lines.extend(t.table(["name", "rev", "origin", "rules", "default", "status"],
                         rows, width, drop=["status", "default"]).split("\n"))
    return "\n".join(lines)


def render_decisions(data: dict, width: int | None = None) -> str:
    decisions = (data or {}).get("decisions") or []
    if not decisions:
        return t.section("EVIDENCE / DECISION RECORDS", width) + "\n" + _none()
    rows = [
        [t.ts_of(d.get("created_at")), d.get("system_id") or "—",
         t.fit(d.get("action", ""), 28),
         t.glyphed(d.get("decision"), str(d.get("decision", "")).upper()),
         str(d.get("risk") or "—"),
         (d.get("oversight") or {}).get("actual", "none"),
         (d.get("run_id") or "")[:8]]
        for d in decisions
    ]
    return (t.section(f"EVIDENCE / DECISION RECORDS — {len(decisions)} SHOWN",
                      width)
            + "\n" + t.table(["utc", "system", "action", "decision", "risk",
                              "oversight", "run"], rows, width,
                              drop=["run", "oversight", "system"]))


def render_evaluations(data: dict, width: int | None = None) -> str:
    evaluations = (data or {}).get("evaluations") or []
    if not evaluations:
        return t.section("ASSURANCE / EVALUATIONS", width) + "\n" + _none()
    rows = [
        [t.ts_of(e.get("created_at")), e.get("system_id", ""),
         e.get("kind", ""), str(e.get("status", "")),
         f"{e['score'] * 100:.1f}%" if e.get("score") is not None else "—",
         t.glyph_for("allow" if e.get("passed") else "deny")]
        for e in evaluations
    ]
    return (t.section(f"ASSURANCE / EVALUATIONS — {len(evaluations)} SHOWN",
                      width)
            + "\n" + t.table(["utc", "system", "kind", "status", "score",
                              "pass"], rows, width,
                              drop=["kind", "status", "utc"]))


def render_approvals_queue(data: dict, width: int | None = None) -> str:
    approvals = (data or {}).get("approvals") or []
    if not approvals:
        return t.section("OVERSIGHT / QUEUE", width) + "\n" + _none()
    rows = [
        [a["id"][:8], t.fit(a.get("action", ""), 30),
         str(a.get("risk") or "").upper(), str(a.get("status", "")),
         a.get("system_id") or "—", a.get("decision_kind") or "—",
         a.get("delegated_to") or "—",
         (a.get("expires_at") or "—")[:19].replace("T", " ")]
        for a in approvals
    ]
    return (t.section(f"OVERSIGHT / QUEUE — {len(approvals)} REQUEST(S)", width)
            + "\n" + t.table(["id", "action", "risk", "status", "system",
                              "kind", "delegate", "expires"], rows, width,
                              drop=["delegate", "expires", "kind", "risk"]))


def render_changes(data: dict, width: int | None = None) -> str:
    changes = (data or {}).get("changes") or []
    if not changes:
        return t.section("ASSURANCE / CHANGE RECORDS", width) + "\n" + _none()
    rows = [
        [ch["id"][:8], ch.get("change_type", ""),
         t.fit(ch.get("title") or "", 28), ch.get("author", ""),
         str(ch.get("state", "")), t.ts_of(ch.get("created_at"))]
        for ch in changes
    ]
    return (t.section(f"ASSURANCE / CHANGE RECORDS — {len(changes)} SHOWN", width)
            + "\n" + t.table(["id", "type", "title", "author", "state", "utc"],
                             rows, width, drop=["utc", "author", "type"]))


def render_drift(data: dict, width: int | None = None) -> str:
    signals = (data or {}).get("signals") or []
    if not signals:
        return (t.section("ASSURANCE / DRIFT", width) + "\n"
                + t.c("  (none) — within stored baselines", "faint"))
    rows = [
        [t.ts_of(s.get("last_seen")), s.get("system_id", ""),
         s.get("signal", ""), str(s.get("severity", "")),
         str(s.get("occurrences", 1)), str(s.get("status", "")),
         t.fit(str((s.get("detail") or {}).get("rule", "")), 38)]
        for s in signals
    ]
    return (t.section(f"ASSURANCE / DRIFT — {len(signals)} SIGNAL(S)", width)
            + "\n" + t.table(["utc", "system", "signal", "severity", "x",
                              "status", "rule"], rows, width,
                              drop=["rule", "status", "x", "utc"]))


def render_replay_report(report: dict, width: int | None = None) -> str:
    width = width or t.W()
    summary = report.get("summary") or {}
    tool = summary.get("tool_layer") or {}
    gov = summary.get("governance_layer") or {}
    candidates = report.get("candidates") or {}
    lines = [
        t.section(f"ASSURANCE / REPLAY / {report.get('system_id', '?')}", width),
        t.kv("source", str(report.get("system_id", "?"))),
        t.kv("runs", str(report.get("runs_replayed", 0))),
        t.kv("candidate", t.fit(json.dumps(
            {k: v for k, v in candidates.items() if v}, default=str), width - 20)
            or t.c("—", "dim")),
        "",
        t.section("DELTA", width),
        t.kv("tool", f"executed {tool.get('executed_delta', 0):+} · "
                     f"denied {tool.get('denied_delta', 0):+} · "
                     f"approvals {tool.get('approval_delta', 0):+}"),
        t.kv("governance", f"blocked +{gov.get('newly_blocked', 0)} · "
                           f"gated +{gov.get('newly_gated', 0)} · "
                           f"allowed +{gov.get('newly_allowed', 0)}"),
        "",
        t.c(f"  {t.GL['review']} NO ACTIONS EXECUTED — REPLAY IS SIMULATION ONLY",
            "sea"),
    ]
    changes = (report.get("governance_layer") or {}).get("changes") or []
    if changes:
        lines.append("")
        lines.extend(t.table(
            ["action", "original", "candidate", "reason"],
            [[t.fit(ch.get("tool", ""), 26), str(ch.get("original", "")),
              str(ch.get("candidate", "")),
              t.fit(str(ch.get("candidate_reason", "")), 40)]
             for ch in changes[:12]], width, drop=["reason"]).split("\n"))
    return "\n".join(lines)


def render_audit_summary(report: dict, width: int | None = None) -> str:
    width = width or t.W()
    lines = [*render_system_overview(report, width).split("\n"), ""]
    score = report.get("governance_score") or {}
    lines.append(t.section("GOVERNANCE STATUS COMPONENTS", width))
    rows = []
    for component in score.get("components") or []:
        mark = {"ok": t.GL["allow"], "warn": t.GL["gate"],
                "fail": t.GL["deny"],
                "insufficient_evidence": t.GL["pending"]}[component["state"]]
        rows.append([component["name"],
                     f"{component['score']:.0f}"
                     if component.get("score") is not None else "—",
                     mark, str(component.get("weight"))])
    lines.append(t.table(["component", "score", "", "weight"], rows, width))
    insufficient = [c for c in score.get("components") or []
                    if c.get("score") is None]
    for component in insufficient:
        lines.append(f"  {t.c(t.GL['pending'], 'dim')} "
                     f"{t.c(component['name'].upper(), 'dim')} — "
                     f"{t.c('INSUFFICIENT EVIDENCE', 'dim')}")

    oversight = report.get("human_oversight") or {}
    lines.append("")
    lines.append(t.section("HUMAN OVERSIGHT", width))
    lines.append(t.fit(
        f"  {t.GL['bullet']} required: {oversight.get('required_count', 0)} · "
        f"involved: {len(oversight.get('involved') or [])} · bypassed: "
        f"{len(oversight.get('bypassed') or [])} · gaps: "
        f"{len(oversight.get('requirement_gaps') or [])}", width))

    flows = report.get("data_flows") or {}
    lines.append(t.section("DATA FLOWS", width))
    lines.append(t.fit(
        f"  {t.GL['bullet']} sources: {flows.get('sources', 0)} · "
        f"destinations: {flows.get('destinations', 0)} · classes seen: "
        f"{', '.join(flows.get('data_classes_seen') or []) or '—'} · "
        f"violations: {flows.get('violation_count', 0)}", width))

    changes = report.get("changes") or []
    if changes:
        lines.append(t.section("RECENT CHANGES", width))
        for change in changes[:5]:
            lines.append(t.fit(
                f"  {t.GL['bullet']} {change['change_type']} · "
                f"{change['title'] or ''} · "
                f"{state(change['state'])} · {change['author']}", width))
    lines.append("")
    lines.append(t.c(f"  {t.fit(report.get('disclaimer', ''), width - 4)}",
                     "faint"))
    return "\n".join(lines)
