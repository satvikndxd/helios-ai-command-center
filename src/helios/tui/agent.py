"""
TUI agent pane — the governed HELIOS shell on top of /v1/agent.

Design rules (unchanged from V1, restyled for the equipment console):
* The agent's state is ALWAYS explicit. `awaiting_approval` and `blocked`
  are first-class, loudly-rendered states — never a generic spinner. The
  run state machine is drawn as an instrument column.
* An approval prompt is a SECURITY AUTHORIZATION CONSOLE: action, system,
  risk with reasons, target resource, the policy + governance rules that
  fired, data classification, and the sha256 payload binding rendered in
  full — because mutation invalidates the approval.
"""

from __future__ import annotations

import json
import os

from helios.tui import screens
from helios.tui import theme as t

STATE_WORDS = {
    "thinking": ("THINKING", "sea"),
    "planning": ("PLANNING", "sea"),
    "tool_pending": ("TOOL PENDING", "sea"),
    "running": ("RUNNING", "accent"),
    "awaiting_approval": ("AWAITING APPROVAL", "warn"),
    "blocked": ("BLOCKED", "crit"),
    "completed": ("COMPLETED", "accent"),
    "failed": ("FAILED", "crit"),
    "cancelled": ("CANCELLED", "warn"),
}


def state_badge(state: str) -> str:
    label, color = STATE_WORDS.get(state, (state.upper(), "dim"))
    glyph = t.glyph_for(state)
    return t.c(f"{glyph} {label}", color, bold=True)


class AgentPane:
    """Owns the active agent session + run rendering. Transport-injected."""

    def __init__(self, call):
        self._call = call  # (method, path, payload=None) -> dict | None
        self.session: dict | None = None
        self.last_run_id: str | None = None
        self._seen_seq: dict[str, int] = {}

    # -- session management ------------------------------------------------

    def ensure_session(self) -> dict | None:
        if self.session is not None:
            return self.session
        payload = {
            "name": "tui",
            "environment": os.environ.get("HELIOS_AGENT_ENV", "dev"),
            "github_repo": os.environ.get("HELIOS_GITHUB_REPO") or None,
            "user_id": os.environ.get("USER", "tui"),
        }
        provider = os.environ.get("HELIOS_AGENT_PROVIDER")
        if provider:
            payload["model_provider"] = provider
        session = self._call("POST", "/v1/agent/sessions", payload)
        if session:
            self.session = session
            repo = payload["github_repo"] or "none (set HELIOS_GITHUB_REPO)"
            print(t.section("HELIOS SHELL / SESSION"))
            print(t.kv("session", t.c(session["id"][:8], "text", bold=True)))
            print(t.kv("environment", session["environment"]))
            print(t.kv("repository", repo))
            print(t.kv("model", session["model_provider"]))
            print(t.kv("governance", t.c("ENFORCED", "accent", bold=True)))
        return self.session

    def use_session(self, session_id: str) -> None:
        session = self._call("GET", f"/v1/agent/sessions/{session_id}")
        if session:
            self.session = session
            print(t.c(f"  {t.GL['allow']} resumed session {session['id'][:8]} "
                      f"({session['message_count']} messages)", "accent"))

    def list_sessions(self) -> None:
        from helios.tui import governance as gov  # noqa: F401  (state colors)

        data = self._call("GET", "/v1/agent/sessions")
        if not data:
            return
        rows = []
        for s in data.get("sessions", []):
            marker = t.c(t.GL["dot"], "accent") \
                if self.session and s["id"] == self.session["id"] \
                else t.c(t.GL["dot_off"], "faint")
            rows.append([marker, s["id"][:8], s["name"], s.get("system_id") or "—",
                         s["environment"], s["model_provider"],
                         str(s["message_count"]),
                         (s["created_at"] or "")[:19]])
        print(t.table(["", "id", "name", "system", "env", "model", "msgs",
                       "created"], rows,
                      drop=["created", "msgs", "model"]))

    # -- chat --------------------------------------------------------------

    def chat(self, text: str) -> None:
        if self.ensure_session() is None:
            return
        run = self._call(
            "POST", f"/v1/agent/sessions/{self.session['id']}/messages",
            {"content": text},
        )
        if run:
            self._render_run(run)

    def resume(self, run_id: str | None = None) -> None:
        run_id = run_id or self.last_run_id
        if not run_id:
            print(t.c("  × no run to resume", "crit"))
            return
        run = self._call("POST", f"/v1/agent/runs/{run_id}/resume")
        if run:
            self._render_run(run)

    def cancel(self, run_id: str | None = None) -> None:
        run_id = run_id or self.last_run_id
        if not run_id:
            print(t.c("  × no run to cancel", "crit"))
            return
        run = self._call("POST", f"/v1/agent/runs/{run_id}/cancel")
        if run:
            print(t.c(f"  {t.GL['gate']} run {run_id[:8]} → {run['state']}",
                      "warn"))

    # -- rendering ---------------------------------------------------------

    def _render_run(self, run: dict) -> None:
        self.last_run_id = run["id"]
        after = self._seen_seq.get(run["id"], 0)
        data = self._call("GET", f"/v1/agent/runs/{run['id']}/events?after_seq={after}")
        events = (data or {}).get("events", [])
        if events:
            self._seen_seq[run["id"]] = events[-1]["seq"]
        for event in events:
            self._render_event(event)

        state = run["state"]
        if state == "completed":
            print()
            print(run.get("output_text") or "")
            print(t.c(f"  run={run['id'][:8]} · {run['steps']} steps · "
                      f"${run['cost_usd']:.4f} · {run['latency_ms']}ms", "faint"))
        elif state == "awaiting_approval":
            self._approval_prompt(run)
        elif state == "blocked":
            print()
            print(t.diag("HELIOS / GOVERNANCE BLOCK", [
                ("state", "BLOCKED"),
                ("meaning", "the pending action was denied by a human"),
                ("effect", "nothing executed; send a new message or /resume"),
            ], severity="crit"))
        elif state == "failed":
            reason = (run.get("error") or {}).get("message", "unknown")
            print(t.diag("HELIOS / RUN FAULT", [("run", run["id"][:8]),
                                                ("reason", reason)],
                         severity="crit"))
        elif state == "cancelled":
            print(t.c(f"  {t.GL['gate']} run cancelled", "warn"))

    def _render_event(self, event: dict) -> None:
        etype = event["event_type"]
        payload = event.get("payload") or {}
        if etype == "state_change":
            print(f"  {state_badge(payload.get('to', event['name']))} "
                  f"{t.c(payload.get('detail', ''), 'dim')}")
        elif etype == "model_call":
            print(t.c(f"    {t.GL['circle']} model {event['name']} · "
                      f"{event['latency_ms']}ms", "dim"))
        elif etype == "tool_proposal":
            args = json.dumps(payload.get("args", {}), default=str)
            if len(args) > 90:
                args = args[:90] + "…"
            print(f"    {t.c(t.GL['arrow'], 'sea')} "
                  f"{t.c(event['name'], 'text', bold=True)} "
                  f"{t.c(args, 'dim')}")
        elif etype == "risk_evaluation":
            reasons = ", ".join(payload.get("reasons", [])[:3])
            print(f"      risk {t.glyphed('deny' if payload.get('risk') in ('high', 'critical') else 'allow', str(payload.get('risk', '?')).upper())} "
                  f"{t.c(reasons, 'dim')}")
        elif etype in ("policy_evaluation", "governance_evaluation"):
            decision = payload.get("decision", event["status"])
            print(f"      {t.glyphed(decision)} "
                  f"{t.c(t.fit(payload.get('reason', ''), 70), 'dim')}")
        elif etype == "approval":
            mode = payload.get("mode", "")
            print(f"      {t.glyphed(event['status'])} approval "
                  f"{t.c(mode, 'dim')} "
                  f"{t.c((payload.get('approval_id') or '')[:8], 'dim')}")
        elif etype == "human_review":
            print(f"      {t.c(t.GL['review'], 'sea')} human review "
                  f"{t.c(payload.get('decided_by', ''), 'text')} "
                  f"{t.c(payload.get('decision', ''), 'dim')}")
        elif etype == "tool_execution":
            status = event["status"]
            preview = json.dumps(payload.get("result") or payload.get("error")
                                 or {}, default=str)[:90]
            print(f"      {t.glyphed('allow' if status in ('ok', 'replayed') else 'deny', status.upper())} "
                  f"{t.c(preview, 'dim')}")

    # -- the approval console (flagship UX) ---------------------------------

    def _fetch_approval(self, approval_id: str) -> dict | None:
        data = self._call("GET", "/v1/approvals?status=pending")
        for approval in (data or {}).get("approvals", []):
            if approval["id"] == approval_id:
                return approval
        return None

    def _approval_prompt(self, run: dict) -> None:
        pending = run.get("pending") or {}
        approval_id = pending.get("approval_id", "")
        approval = self._fetch_approval(approval_id) or {}
        summary = approval.get("summary") or {}
        risk = pending.get("risk") or summary.get("risk") or {}
        policy = pending.get("policy") or summary.get("policy") or {}
        governance = summary.get("governance") or {}
        classification = summary.get("data_classification") or {}
        args_hash = approval.get("args_hash") or pending.get("args_hash") or ""

        print()
        print(screens.shell_state("awaiting_approval"))
        print()
        lines = [
            t.section(f"OVERSIGHT / APPROVAL / {approval_id[:8].upper()}"),
            t.kv("action", t.c(str(pending.get("tool", "?")), "text", bold=True)),
            t.kv("system", str(summary.get("system_id")
                               or (self.session or {}).get("system_id") or "—")),
            t.kv("risk", t.glyphed(
                "deny" if risk.get("risk") in ("high", "critical") else "gate",
                str(risk.get("risk", "?")).upper())),
        ]
        for reason in (risk.get("reasons") or [])[:4]:
            lines.append(f"      {t.c(t.GL['bullet'], 'faint')} "
                         f"{t.c(reason, 'dim')}")
        lines += [
            t.kv("target", t.fit(str(summary.get("resource") or "—"), 60)),
            t.kv("data", str(classification.get("effective") or "—")),
            "",
            t.section("POLICY"),
            t.kv("rule", f"{policy.get('policy_version', '?')} / "
                         f"{policy.get('rule_id', '?')}"),
            t.kv("reason", t.fit(str(policy.get("reason") or "—"), 60)),
        ]
        if governance:
            lines += [
                t.section("GOVERNANCE"),
                t.kv("decision", t.glyphed(governance.get("decision"),
                                           str(governance.get("decision", "")).upper())),
            ]
            for rule in governance.get("matched_rules") or []:
                lines.append(f"      {t.c(t.GL['bullet'], 'faint')} "
                             f"{rule.get('set')}@{rule.get('version')}:"
                             f"{rule.get('rule_id')}")
        lines += ["", t.section("PAYLOAD BINDING — MUTATION INVALIDATES")]
        for i in range(0, len(args_hash), 32):
            lines.append(f"  {t.c('sha256:' if i == 0 else '      ', 'dim')} "
                         f"{t.c(args_hash[i:i + 32], 'text')}")
        lines += ["",
                  t.c("  [A] approve   [D] deny   (or /approve <id> · /deny <id>)",
                      "accent")]
        print("\n".join(lines))
        self._pending_approval = approval_id

    # -- inspection --------------------------------------------------------

    def show_trace(self, run_id: str | None = None) -> None:
        run_id = run_id or self.last_run_id
        if not run_id:
            print(t.c("  × no run — usage: /trace <run-id>", "crit"))
            return
        data = self._call("GET", f"/v1/agent/runs/{run_id}/events")
        if not data:
            return
        print(screens.trace_timeline(data["events"], data["run"]))

    def replay(self, args: list[str]) -> None:
        run_id = args[0] if args else self.last_run_id
        if not run_id:
            print(t.c("  × usage: /replay <run-id> [policy-version]", "crit"))
            return
        payload: dict = {}
        if len(args) > 1:
            payload["policy_version"] = args[1]
        data = self._call("POST", f"/v1/agent/runs/{run_id}/replay", payload)
        if not data:
            return
        width = t.W()
        lines = [
            t.section(f"ASSURANCE / REPLAY / RUN {data['run_id'][:8]}"),
            t.kv("policy", t.c(data["policy"]["version"], "text", bold=True)),
            t.kv("proposals", str(data["proposals"])),
            t.kv("original", json.dumps(data["original"])),
            t.kv("candidate", json.dumps(data["candidate"])),
            "",
            t.c(f"  {t.GL['review']} NO ACTIONS EXECUTED — SIMULATION ONLY",
                "sea"),
        ]
        if data["changes"]:
            lines.append("")
            lines.extend(t.table(
                ["action", "original", "candidate", "reason"],
                [[ch["tool"], ch["original"], ch["candidate"],
                  t.fit(ch.get("candidate_reason") or "", 34)]
                 for ch in data["changes"]], drop=["reason"]).split("\n"))
        print("\n".join(t.fit(line, width) for line in lines))

    def list_tools(self) -> None:
        data = self._call("GET", "/v1/tools")
        if not data:
            return
        rows = []
        for tool in data.get("tools", []):
            rows.append([
                tool["name"], tool["capability"],
                str(tool["risk_class"]).upper(),
                ",".join(tool.get("scopes", [])),
                t.fit(tool["description"], 40),
            ])
        print(t.table(["tool", "capability", "base risk", "scopes", ""], rows,
                      drop=["scopes"]))
