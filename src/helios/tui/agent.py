"""
TUI agent pane — the daily-driver chat loop on top of /v1/agent.

Design rules:
* The agent's state is ALWAYS explicit. `awaiting_approval` and `blocked`
  are first-class, loudly-rendered states — never a generic spinner.
* An approval prompt shows everything a human needs to decide: agent, tool,
  arguments, target resource, environment, risk (with reasons), the policy
  rule that fired, and the trace id — then binds the decision to the exact
  payload hash.
"""

from __future__ import annotations

import json
import os

from helios.tui import ui
from helios.tui.ui import badge, bullet, c, error, kv, panel, risk_badge, success

STATE_BADGES = {
    "thinking": ("THINKING", "sea"),
    "planning": ("PLANNING", "sea"),
    "tool_pending": ("TOOL PENDING", "sea"),
    "running": ("RUNNING", "green"),
    "awaiting_approval": ("AWAITING APPROVAL", "yellow"),
    "blocked": ("BLOCKED", "red"),
    "completed": ("COMPLETED", "green"),
    "failed": ("FAILED", "red"),
    "cancelled": ("CANCELLED", "yellow"),
}


def state_badge(state: str) -> str:
    label, color = STATE_BADGES.get(state, (state.upper(), "dim"))
    return badge(label, color)


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
            print(success(
                f"Agent session {c(session['id'][:8], 'fg', bold=True)} · "
                f"env {c(session['environment'], 'fg')} · repo {c(repo, 'fg')} · "
                f"model {c(session['model_provider'], 'fg')}"
            ))
        return self.session

    def use_session(self, session_id: str) -> None:
        session = self._call("GET", f"/v1/agent/sessions/{session_id}")
        if session:
            self.session = session
            print(success(f"Resumed session {session['id'][:8]} "
                          f"({session['message_count']} messages)"))

    def list_sessions(self) -> None:
        data = self._call("GET", "/v1/agent/sessions")
        if not data:
            return
        rows = []
        for s in data.get("sessions", []):
            marker = c("●", "green") if self.session and s["id"] == self.session["id"] \
                else c("·", "dim")
            rows.append([marker, s["id"][:8], s["name"], s["environment"],
                         s["model_provider"], str(s["message_count"]),
                         s["created_at"][:19] if s["created_at"] else ""])
        print(ui.table(["", "id", "name", "env", "model", "msgs", "created"], rows))

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
            print(error("No run to resume."))
            return
        run = self._call("POST", f"/v1/agent/runs/{run_id}/resume")
        if run:
            self._render_run(run)

    def cancel(self, run_id: str | None = None) -> None:
        run_id = run_id or self.last_run_id
        if not run_id:
            print(error("No run to cancel."))
            return
        run = self._call("POST", f"/v1/agent/runs/{run_id}/cancel")
        if run:
            print(success(f"run {run_id[:8]} → {run['state']}"))

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
            print(c(f"  run={run['id'][:8]} · {run['steps']} steps · "
                    f"${run['cost_usd']:.4f} · {run['latency_ms']}ms", "dim"))
        elif state == "awaiting_approval":
            self._approval_prompt(run)
        elif state == "blocked":
            print(panel("BLOCKED", [
                c("The pending action was denied by a human.", "fg"),
                c("The agent will not execute it. Send a new message to "
                  "continue, or /retry the run.", "dim"),
            ], color="red"))
        elif state == "failed":
            reason = (run.get("error") or {}).get("message", "unknown")
            print(error(f"Run failed: {reason}"))
        elif state == "cancelled":
            print(c("  Run cancelled.", "yellow"))

    def _render_event(self, event: dict) -> None:
        etype = event["event_type"]
        payload = event.get("payload") or {}
        if etype == "state_change":
            print(f"  {state_badge(payload.get('to', event['name']))} "
                  f"{c(payload.get('detail', ''), 'dim')}")
        elif etype == "model_call":
            print(c(f"    model {event['name']} · {event['latency_ms']}ms", "dim"))
        elif etype == "tool_proposal":
            args = json.dumps(payload.get("args", {}), default=str)
            if len(args) > 100:
                args = args[:100] + "…"
            print(f"    {c('→', 'sea')} {c(event['name'], 'fg', bold=True)} "
                  f"{c(args, 'dim')}")
        elif etype == "risk_evaluation":
            reasons = ", ".join(payload.get("reasons", [])[:3])
            print(f"      risk {risk_badge(payload.get('risk', '?'))} "
                  f"{c(reasons, 'dim')}")
        elif etype == "policy_evaluation":
            decision = payload.get("decision", event["status"])
            color = {"allow": "green", "deny": "red",
                     "require_approval": "yellow"}.get(decision, "dim")
            print(f"      policy {badge(decision.upper(), color)} "
                  f"{c(payload.get('reason', ''), 'dim')}")
        elif etype == "approval":
            mode = payload.get("mode", "")
            print(f"      approval {badge(event['status'].upper(), 'yellow' if event['status'] == 'pending' else 'green')} "
                  f"{c(mode, 'dim')}")
        elif etype == "tool_execution":
            status = event["status"]
            color = "green" if status in ("ok", "replayed") else "red"
            preview = json.dumps(payload.get("result") or payload.get("error") or {},
                                 default=str)[:100]
            print(f"      {badge(status.upper(), color)} {c(preview, 'dim')}")

    # -- the approval prompt (flagship UX) ---------------------------------

    def _approval_prompt(self, run: dict) -> None:
        pending = run.get("pending") or {}
        approval_id = pending.get("approval_id", "")
        approval = self._fetch_approval(approval_id)
        summary = (approval or {}).get("summary", {})
        risk = pending.get("risk") or summary.get("risk") or {}
        policy = pending.get("policy") or summary.get("policy") or {}

        lines = [
            kv("agent", c(summary.get("agent_id") or "helios-agent", "fg")),
            kv("tool", c(pending.get("tool", "?"), "fg", bold=True)),
            kv("action", c(summary.get("description", ""), "fg")),
        ]
        for key, value in (summary.get("resource") or {}).items():
            lines.append(kv(key, c(str(value), "fg", bold=True)))
        args = pending.get("args") or {}
        for key, value in list(args.items())[:8]:
            preview = str(value)
            if len(preview) > 80:
                preview = preview[:80] + "…"
            lines.append(kv(f"args.{key}", preview))
        lines.extend([
            kv("environment", c(summary.get("environment", "?"), "fg", bold=True)),
            kv("risk", f"{risk_badge(risk.get('risk', 'high'))} "
                       + c(f"score {risk.get('score', '?')}", "dim")),
        ])
        for reason in risk.get("reasons", []):
            lines.append(kv("", c(f"– {reason}", "yellow")))
        lines.extend([
            kv("policy", c(f"{policy.get('policy_version', '?')} · rule "
                           f"{policy.get('rule_id', '?')}", "fg")),
            kv("why", c(policy.get("reason", pending.get("reason", "")), "fg")),
            kv("approval", c(approval_id, "dim")),
            kv("trace", c(f"run {run['id']}", "dim")),
        ])
        print(panel("APPROVAL REQUIRED", lines, color="yellow"))

        try:
            import sys
            if not sys.stdin.isatty():
                print(c("  Decide with /approve|/deny " + approval_id[:8]
                        + " then /resume", "dim"))
                return
            while True:
                choice = input(
                    "  " + c("[a]", "green") + "pprove  "
                    + c("[d]", "red") + "eny  "
                    + c("[s]", "sea") + "ession-approve  "
                    + c("[i]", "dim") + "nspect  "
                    + c("[l]", "dim") + "ater > "
                ).strip().lower()
                if choice in ("a", "d", "s"):
                    decision = {"a": "approved", "d": "denied",
                                "s": "approve_session"}[choice]
                    decided = self._call(
                        "POST", f"/v1/agent/approvals/{approval_id}/decide",
                        {"decision": decision,
                         "decided_by": os.environ.get("USER", "tui")},
                    )
                    if decided:
                        print(success(f"approval {approval_id[:8]} → "
                                      f"{decided['status']}"))
                        self.resume(run["id"])
                    return
                if choice == "i":
                    print(c(json.dumps({
                        "args": args, "risk": risk, "policy": policy,
                    }, indent=2, default=str), "dim"))
                    continue
                print(c(f"  Left pending. Later: /approve {approval_id[:8]}… "
                        f"then /resume", "dim"))
                return
        except (EOFError, KeyboardInterrupt):
            print("\n" + c(f"  Left pending — /approve {approval_id[:8]}… "
                           f"then /resume", "dim"))

    def _fetch_approval(self, approval_id: str) -> dict | None:
        data = self._call("GET", "/v1/approvals?status=pending")
        for approval in (data or {}).get("approvals", []):
            if approval["id"] == approval_id:
                return approval
        return None

    # -- inspection --------------------------------------------------------

    def show_trace(self, run_id: str | None = None) -> None:
        run_id = run_id or self.last_run_id
        if not run_id:
            print(error("No run — usage: /trace <run-id>"))
            return
        data = self._call("GET", f"/v1/agent/runs/{run_id}/events")
        if not data:
            return
        run, events = data["run"], data["events"]
        print(panel("DECISION TRACE", [
            kv("run", run["id"]),
            kv("state", state_badge(run["state"])),
            kv("steps", str(run["steps"])),
            kv("cost", f"${run['cost_usd']:.4f}"),
            kv("input", (run.get("input_text") or "")[:80]),
        ]))
        children: dict[str | None, list[dict]] = {}
        for event in events:
            children.setdefault(event.get("parent_id"), []).append(event)

        def render(parent: str | None, depth: int) -> None:
            for event in children.get(parent, []):
                indent = "  " + "  " * depth
                status = event["status"]
                color = {"ok": "green", "denied": "red", "error": "red",
                         "pending": "yellow", "blocked": "red",
                         "approved": "green"}.get(status, "dim")
                risk = f" {risk_badge(event['risk'])}" if event.get("risk") else ""
                seq_str = "#{:>2}".format(event["seq"])
                latency = f"{event['latency_ms']}ms" if event["latency_ms"] else ""
                print(f"{indent}{c(seq_str, 'dim')} "
                      f"{c(event['event_type'], 'sea')} "
                      f"{c(event['name'], 'fg', bold=True)}{risk} "
                      f"{badge(status, color)} {c(latency, 'dim')}")
                render(event["id"], depth + 1)

        render(None, 0)

    def replay(self, args: list[str]) -> None:
        run_id = args[0] if args else self.last_run_id
        if not run_id:
            print(error("Usage: /replay <run-id> [policy-version]"))
            return
        payload: dict = {}
        if len(args) > 1:
            payload["policy_version"] = args[1]
        data = self._call("POST", f"/v1/agent/runs/{run_id}/replay", payload)
        if not data:
            return
        print(panel("REPLAY", [
            kv("run", data["run_id"][:12]),
            kv("policy", c(data["policy"]["version"], "fg", bold=True)),
            kv("proposals", str(data["proposals"])),
            kv("original", c(json.dumps(data["original"]), "fg")),
            kv("candidate", c(json.dumps(data["candidate"]), "fg")),
            kv("changes", c(str(len(data["changes"])), "yellow" if data["changes"] else "green")),
        ]))
        for change in data["changes"]:
            print(bullet(
                f"{c(change['tool'], 'fg', bold=True)} "
                f"{c(change['original'], 'dim')} → "
                f"{c(change['candidate'], 'yellow', bold=True)} "
                f"{c('(' + (change.get('candidate_reason') or '') + ')', 'dim')}"
            ))

    def list_tools(self) -> None:
        data = self._call("GET", "/v1/tools")
        if not data:
            return
        rows = []
        for tool in data.get("tools", []):
            rows.append([
                c(tool["name"], "green"), tool["capability"],
                risk_badge(tool["risk_class"]),
                ",".join(tool.get("scopes", [])),
                c(tool["description"][:44], "dim"),
            ])
        print(ui.table(["tool", "capability", "base risk", "scopes", ""], rows))
