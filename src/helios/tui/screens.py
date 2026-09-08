"""
Command-center screens — pure renderers over real HELIOS API payloads.

Layout doctrine: rectangular panels, 1px rules, hard alignment, dense
monospace metadata. Two columns when the terminal is wide (>=110), a single
instrument column when narrow. Every value is derived from the payload —
no invented metrics (§21). Empty and error states are first-class.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from helios.tui import governance as gov
from helios.tui import theme as t

STATUS_WORDS = [
    ("CONTROL PLANE", "ONLINE"),
    ("POLICY ENGINE", "ENFORCING"),
    ("TENANT ISOLATION", "ACTIVE"),
    ("CONTEXT GUARDRAILS", "ACTIVE"),
    ("AUDIT LOG", "RECORDING"),
]


# -- layout helpers -----------------------------------------------------------


def columns(blocks: list[tuple[str, list[str]]], width: int) -> str:
    """Side-by-side panels when wide, stacked instrument column when narrow."""
    if width < 110 or len(blocks) < 2:
        out = []
        for title, lines in blocks:
            out.append(t.section(title, width))
            out.extend(lines or [t.c("  (none)", "faint")])
            out.append("")
        return "\n".join(out).rstrip()

    halves = [blocks[i:i + 2] for i in range(0, len(blocks), 2)]
    col_w = (width - 3) // 2
    out = []
    for pair in halves:
        rendered = []
        for title, lines in pair:
            body = [t.section(title, col_w)]
            body.extend(lines or [t.c("  (none)", "faint")])
            rendered.append(body)
        height = max(len(b) for b in rendered)
        for i in range(height):
            left = rendered[0][i] if i < len(rendered[0]) else ""
            right = rendered[1][i] if len(rendered) > 1 and i < len(rendered[1]) \
                else ""
            out.append(t.pad(t.fit(left, col_w), col_w + 3)
                       + t.fit(right, col_w))
        out.append("")
    return "\n".join(out).rstrip()


def status_line(label: str, word: str, ok: bool) -> str:
    dot = t.c(t.GL["dot"], "accent") if ok else t.c(t.GL["dot_off"], "warn")
    return f"  {dot} {t.pad(label, 20)}{t.c(word if ok else word + ' (DEGRADED)', 'accent' if ok else 'warn')}"


# -- OVERVIEW -------------------------------------------------------------------


def overview(data: dict, width: int | None = None) -> str:
    width = width or t.W()
    decisions = (data.get("decisions") or {}).get("decisions") or []
    approvals = (data.get("approvals") or {}).get("approvals") or []
    systems = (data.get("systems") or {}).get("systems") or []
    models = (data.get("models") or {}).get("models") or []
    policies = data.get("policies") or {}
    drift = (data.get("drift") or {}).get("signals") or []
    evaluations = (data.get("evaluations") or {}).get("evaluations") or []

    tool_sets = len(policies.get("policies") or [])
    gov_sets = [p for p in policies.get("governance_policies") or []
                if p.get("status") == "active"]

    try:
        from helios.sentinel import INJECTION_PATTERNS, PII_PATTERNS
        guardrails = bool(PII_PATTERNS) and bool(INJECTION_PATTERNS)
    except Exception:  # noqa: BLE001
        guardrails = False

    status_block = [
        status_line("CONTROL PLANE", "ONLINE", bool(data.get("health_ok"))),
        status_line("POLICY ENGINE", "ENFORCING", bool(tool_sets or gov_sets)),
        status_line("TENANT ISOLATION", "ACTIVE", bool(data.get("authenticated"))),
        status_line("CONTEXT GUARDRAILS", "ACTIVE", guardrails),
        status_line("AUDIT LOG", "RECORDING", data.get("decisions") is not None),
    ]

    metrics_block = [
        t.kv("decisions", str(len(decisions))),
        t.kv("pending approvals", str(sum(1 for a in approvals
                                           if a.get("status") == "pending"))),
        t.kv("open drift", str(len(drift))),
        t.kv("systems", str(len(systems))),
        t.kv("models", str(len(models))),
        t.kv("policy sets", f"{len(gov_sets)} active / {tool_sets} tool"),
    ]
    if evaluations:
        latest = evaluations[0]
        metrics_block.append(
            t.kv("last evaluation",
                 f"{latest['score'] * 100:.1f}%"
                 if latest.get("score") is not None else "—"))

    # usage histogram over the last 24h from real decision timestamps
    now = datetime.now(timezone.utc)
    buckets = [0] * 24
    for d in decisions:
        stamp = (d.get("created_at") or "")[:19]
        try:
            when = datetime.fromisoformat(stamp)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            age = (now - when).total_seconds() / 3600
            if 0 <= age < 24:
                buckets[23 - int(age)] += 1
        except ValueError:
            continue
    usage_block = [f"  {t.spark(buckets, width=min(48, width - 6))}",
                   t.c("  00                          24  (UTC, decision records)",
                       "faint")]

    events_block = [
        f"  {t.ts_of(d.get('created_at'))}  "
        f"{t.glyphed(d.get('decision'), str(d.get('decision', '')).upper()):<12} "
        f"{t.c(t.fit(d.get('action', ''), 30), 'text')}"
        for d in decisions[:7]
    ] or [t.c("  (no recorded decisions)", "faint")]

    mix: dict[str, int] = {}
    for d in decisions:
        if d.get("model"):
            mix[d["model"]] = mix.get(d["model"], 0) + 1
    total = sum(mix.values()) or 1
    model_block = [
        f"  {t.pad(t.fit(name, 26), 28)}{t.bar(count / total, 12)} "
        f"{t.c(f'{count * 100 // total}%', 'dim')}"
        for name, count in sorted(mix.items(), key=lambda kv: -kv[1])[:5]
    ] or [t.c("  (no model attribution yet)", "faint")]

    return columns([
        ("SYSTEM STATUS", status_block),
        ("LIVE METRICS", metrics_block),
        ("TENANT USAGE (LAST 24H)", usage_block),
        ("POLICY EVENTS", events_block),
        ("MODEL USAGE", model_block),
    ], width)


# -- SYSTEM (operator plate) ------------------------------------------------------


def system_view(audit: dict, width: int | None = None) -> str:
    return gov.render_system_overview(audit, width)


# -- POLICY SPEC -------------------------------------------------------------------


def policy_spec(policy: dict, width: int | None = None) -> str:
    width = width or t.W()
    lines = [
        t.section(f"POLICY / {policy.get('name', '?')}@{policy.get('version', '?')}",
                  width),
        t.kv("status", gov.state(policy.get("status", "?"))),
        t.kv("description", t.fit(policy.get("description") or "—", width - 20)),
        t.kv("scope", t.fit(str(policy.get("scope") or "all systems"), width - 20)),
        "",
    ]
    for i, rule in enumerate(policy.get("rules") or [], 1):
        match = rule.get("match") or rule.get("when") or {}
        lines.append(t.section(f"RULE {i:02d} — {rule.get('id', '?')}", width))
        subject = match.get("subject")
        if subject:
            lines.append(t.kv("subject", str(subject)))
        for key, value in match.items():
            if key == "subject":
                continue
            lines.append(t.kv(f"when.{key}", t.fit(str(value), width - 24)))
        effect = str(rule.get("effect", "")).upper()
        lines.append(t.kv("effect", t.glyphed(rule.get("effect"), effect)))
        lines.append(t.kv("reason", t.fit(rule.get("reason") or "—", width - 20)))
        lines.append("")
    lines.append(t.section("DEFAULT", width))
    lines.append(t.kv("unmatched", t.glyphed(
        policy.get("default_effect", "deny"),
        str(policy.get("default_effect", "deny")).upper())))
    lines.append("")
    lines.append(t.c(f"  {t.GL['review']} REPLAY AVAILABLE — "
                     f"test this set as a candidate before activation", "sea"))
    return "\n".join(t.fit(line, width) for line in lines)


# -- APPROVAL CONSOLE ----------------------------------------------------------------


def approval_console(approval: dict, width: int | None = None) -> str:
    width = width or t.W()
    summary = approval.get("summary") or {}
    risk = summary.get("risk") or {}
    policy = summary.get("policy") or {}
    governance = summary.get("governance") or {}
    classification = summary.get("data_classification") or {}
    args_hash = approval.get("args_hash") or ""

    lines = [
        t.section(f"OVERSIGHT / APPROVAL / {approval.get('id', '?')[:8].upper()}",
                  width),
        t.kv("action", t.c(str(approval.get("action", "?")), "text", bold=True)),
        t.kv("system", str(summary.get("system_id") or "—")),
        t.kv("risk", t.glyphed("deny" if risk.get("risk") in ("high", "critical")
                               else "gate", str(risk.get("risk", "?")).upper())),
        t.kv("target", t.fit(str(summary.get("resource") or "—"), width - 20)),
        t.kv("status", gov.state(approval.get("status"))),
        "",
        t.section("PAYLOAD BINDING — MUTATION INVALIDATES THIS APPROVAL", width),
    ]
    for i in range(0, len(args_hash), 32):
        lines.append(f"  {t.c('sha256:' if i == 0 else '      ', 'dim')} "
                     f"{t.c(args_hash[i:i + 32], 'text')}")
    lines += [
        "",
        t.section("POLICY", width),
        t.kv("rule", f"{policy.get('policy_version', '?')} / "
                     f"{policy.get('rule_id', '?')}"),
        t.kv("reason", t.fit(str(policy.get('reason') or '—'), width - 20)),
        t.kv("governance", t.fit(
            "; ".join(f"{m.get('set')}:{m.get('rule_id')}→{m.get('effect')}"
                      for m in governance.get("matched_rules") or [])
            or "—", width - 20)),
        t.kv("data", str(classification.get("effective") or "—")),
        "",
        t.c("  [A] APPROVE   [D] DENY   [E] ESCALATE   [C] COMMENT", "accent"),
    ]
    return "\n".join(t.fit(line, width) for line in lines)


# -- TRACE TIMELINE ---------------------------------------------------------------------


_EVENT_LABEL = {
    "model_call": "MODEL CALL", "retrieval": "RETRIEVAL",
    "data_access": "DATA ACCESS", "tool_proposal": "TOOL PROPOSAL",
    "permission_evaluation": "PERMISSION CHECK", "risk_evaluation": "RISK ENGINE",
    "policy_evaluation": "POLICY", "governance_evaluation": "GOVERNANCE",
    "approval": "APPROVAL", "human_review": "HUMAN REVIEW",
    "tool_execution": "EXECUTION", "state_change": "STATE",
    "outcome": "OUTCOME", "model_governance": "MODEL GOVERNANCE",
    "governance_change": "CHANGE", "external_span": "EXTERNAL SPAN",
}


def trace_timeline(events: list[dict], run: dict | None = None,
                   width: int | None = None) -> str:
    width = width or t.W()
    run = run or {}
    lines = [t.section(f"EVIDENCE / TRACE / RUN {run.get('id', '?')[:8]}", width)]
    if run.get("state"):
        lines.append(shell_state(run.get("state")))
        lines.append("")
    if not events:
        lines.append(t.c("  (no events recorded)", "faint"))
        return "\n".join(lines)
    by_id = {e["id"]: e for e in events}
    for event in events:
        depth = 0 if not event.get("parent_id") or \
            event["parent_id"] not in by_id else 1
        label = _EVENT_LABEL.get(event["event_type"],
                                 event["event_type"].upper())
        payload = event.get("payload") or {}
        detail = ""
        etype = event["event_type"]
        if etype == "model_call":
            detail = event.get("name", "")
        elif etype in ("tool_proposal", "tool_execution"):
            detail = event.get("name", "")
        elif etype in ("policy_evaluation", "governance_evaluation"):
            detail = str(payload.get("decision", event.get("status", "")))
        elif etype == "risk_evaluation":
            detail = str(payload.get("risk", ""))
        elif etype == "approval":
            detail = f"{payload.get('mode', '')} {event.get('status', '')}"
        elif etype == "human_review":
            detail = str(payload.get("decided_by", ""))
        elif etype == "data_access":
            detail = f"{payload.get('direction', '')} " \
                     f"{payload.get('data_class') or ''}"
        elif etype == "outcome":
            detail = event.get("name", "")
        glyph = t.glyph_for(payload.get("decision") or event.get("status"))
        lead = "  " if depth == 0 else "      "
        lines.append(f"{lead}{t.c(t.ts_of(event.get('created_at')), 'dim')}  "
                     f"{glyph} {t.c(label, 'text')}")
        if detail:
            lines.append(f"{lead}{' ' * 12}{t.c(t.fit(detail, width - 22), 'dim')}")
    return "\n".join(t.fit(line, width) for line in lines)


# -- WHY VIEW ------------------------------------------------------------------------------


def why_view(explanation: dict, width: int | None = None) -> str:
    width = width or t.W()
    decision = explanation.get("decision", "")
    lines = [t.section(f"WHY WAS THIS ACTION {decision}?", width), ""]
    for check in explanation.get("why") or []:
        passed = check.get("passed")
        mark = t.c(t.GL["allow"], "accent") if passed else \
            (t.c(t.GL["deny"], "crit") if passed is False
             else t.c(t.GL["pending"], "dim"))
        lines.append(f"  {mark} {t.c(check.get('check', '').upper(), 'text')}")
        for sub in t.fit(str(check.get("reason") or ""), width - 8).split("\n"):
            lines.append(f"      {t.c(sub, 'dim')}")
        lines.append("")
    evidence = explanation.get("evidence") or {}
    lines.append(t.section("EVIDENCE REFERENCES", width))
    lines.append(f"  trace events : {len(evidence.get('trace_event_ids') or [])}")
    if evidence.get("approval_id"):
        lines.append(f"  approval     : {evidence['approval_id']}")
    lines.append(f"  record       : {explanation.get('decision_record_id', '?')}")
    lines.append("")
    lines.append(t.c("  Generated from recorded policy/evidence state — "
                     "not narrative text.", "faint"))
    return "\n".join(t.fit(line, width) for line in lines)


# -- ASSURANCE (score instrument) -------------------------------------------------------------


def assurance_view(score: dict, width: int | None = None) -> str:
    width = width or t.W()
    lines = [t.section(f"ASSURANCE / {score.get('system_id', '?')}", width), ""]
    if score.get("overall") is None:
        lines.append(t.c("  INSUFFICIENT EVIDENCE", "warn", bold=True))
        lines.append(t.c("  components without backing data are excluded, "
                         "never scored 100", "dim"))
    else:
        lines.append(f"  {t.c(str(score['overall']), 'text', bold=True)} / 100"
                     f"   {gov.state(score.get('state'))}")
    lines.append("")
    for component in score.get("components") or []:
        name = t.pad(component["name"].upper(), 18)
        if component.get("score") is None:
            value = t.c("INSUFFICIENT EVIDENCE", "dim")
            meter = t.c(t.GL["bar_off"] * 10, "faint")
        else:
            meter = t.bar(component["score"] / 100, 10)
            value = f"{component['score']:.0f}/{component['weight']}"
        lines.append(f"  {name}{meter}  {value}")
        failed = [ch for ch in component.get("checks") or []
                  if ch.get("passed") is False]
        for check in failed[:3]:
            lines.append(f"      {t.c(t.GL['deny'], 'crit')} "
                         f"{t.c(t.fit(check.get('reason', ''), width - 10), 'dim')}")
    lines.append("")
    lines.append(t.c(f"  formula: {score.get('formula', '')}", "faint"))
    return "\n".join(t.fit(line, width) for line in lines)


# -- DRIFT INSTRUMENT ----------------------------------------------------------------------------


def drift_view(state: dict, width: int | None = None) -> str:
    width = width or t.W()
    lines = [t.section(f"ASSURANCE / DRIFT / {state.get('system_id', '?')}", width)]
    baseline = state.get("baseline")
    if not baseline:
        lines.append(t.c("  NO BASELINE STORED — create one to enable drift "
                         "detection", "warn"))
        return "\n".join(lines)
    metrics = baseline.get("metrics") or {}
    lines += [
        "",
        t.kv("baseline", f"{baseline.get('label', '?')} · "
                         f"{(baseline.get('created_at') or '')[:19]}"),
        t.kv("write approval", f"{(metrics.get('approval_rate') or 0) * 100:.1f}%"),
        t.kv("denial rate", f"{(metrics.get('denial_rate') or 0) * 100:.1f}%"),
        t.kv("decisions", str(metrics.get("decisions", 0))),
        "",
    ]
    findings = state.get("findings") or state.get("signals") or []
    if isinstance(findings, dict):
        findings = findings.get("open") or []
    if not findings:
        lines.append(t.c(f"  {t.GL['allow']} WITHIN BASELINE — no drift "
                         "signals", "accent"))
        return "\n".join(t.fit(line, width) for line in lines)
    for f in findings:
        detail = f.get("detail") or {}
        lines.append(t.section(f"{f.get('signal', '?').upper()} — "
                               f"{f.get('severity', '').upper()}", width))
        if detail.get("baseline_value") is not None:
            lines.append(t.kv("baseline", str(detail["baseline_value"])))
        if detail.get("observed_value") is not None:
            lines.append(t.kv("observed", str(detail["observed_value"])))
        lines.append(t.kv("rule", t.fit(str(detail.get("rule", "")), width - 20)))
        lines.append("")
    return "\n".join(t.fit(line, width) for line in lines)


# -- AGENT SHELL STATE MACHINE --------------------------------------------------------------------


STATE_ORDER = ["thinking", "tool_pending", "running", "awaiting_approval",
               "executed"]


def shell_state(current: str | None, width: int | None = None) -> str:
    """The run state machine, rendered as an instrument column."""
    current = (current or "").lower()
    display = {"completed": "executed", "blocked": "blocked"}.get(current, current)
    chain = STATE_ORDER if display != "blocked" else STATE_ORDER[:4] + ["blocked"]
    lines = []
    for i, st in enumerate(chain):
        active = st == display
        mark = t.c(t.GL["dot"], "accent") if active else \
            t.c(t.GL["dot_off"], "faint")
        label = t.c(st.upper(), "accent" if active else "dim", bold=active)
        lines.append(f"  {mark} {label}")
        if i < len(chain) - 1:
            lines.append(f"    {t.c(t.GL['down'], 'faint')}")
    return "\n".join(lines)


# -- NAV PANEL ------------------------------------------------------------------------------


NAV = [
    ("1", "overview", "OVERVIEW"),
    ("2", "systems", "SYSTEMS · IDENTITY"),
    ("3", "models", "MODELS · IDENTITY"),
    ("4", "policies", "POLICIES · POLICY"),
    ("5", "traces", "TRACES · EVIDENCE"),
    ("6", "approvals", "APPROVALS · OVERSIGHT"),
    ("7", "changes", "CHANGES · ASSURANCE"),
    ("8", "replay", "REPLAY · ASSURANCE"),
    ("9", "drift", "DRIFT · ASSURANCE"),
    ("a", "audit", "AUDIT · ASSURANCE"),
]


def nav_panel(current: str, cursor: int, width: int | None = None) -> str:
    width = width or t.W()
    lines = [t.section("NAVIGATION", min(34, width))]
    for i, (key, view, label) in enumerate(NAV):
        mark = t.c(t.GL["dot"], "accent") if view == current else " "
        sel = t.c(">", "accent") if i == cursor and view == current else " "
        lines.append(f"  {sel}{mark} {t.c(f'[{key}]', 'dim')} "
                     f"{t.c(label, 'text' if view == current else 'dim')}")
    return "\n".join(lines)
