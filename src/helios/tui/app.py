"""
HELIOS command center — the operator console.

The interactive loop for `python -m helios.tui`.

Visual doctrine: an equipment console, not a dashboard. Equipment
identification plate on boot, 1px rules, rectangular panels, instrument
glyphs, dense monospace metadata, a real operator prompt
(`helios@control:~$`). Keyboard-first (1-9/a, j/k, arrows, Enter, Esc,
Tab, ?, r, q) while the full slash-command surface from V1 is preserved
verbatim — commands and keys drive the same views.

Every value on screen comes from a live HELIOS API response. When a call
fails, the console renders a SYSTEM DIAGNOSTIC with the HTTP status and
detail — never a vague error, never invented data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

from helios.gateways import (
    GatewayProfile,
    all_gateways,
    discover_models,
    get_gateway,
)
from helios.tui import (
    build_direct_payload,
    build_governed_payload,
    completion_endpoint,
    extract_direct_output,
    extract_governed_output,
    request_headers,
)
from helios.tui import screens
from helios.tui import theme as t
from helios.tui.agent import AgentPane
from helios.tui.governance import (
    render_approvals_queue,
    render_audit_summary,
    render_changes,
    render_decisions,
    render_drift,
    render_evaluations,
    render_models_table,
    render_policies,
    render_systems_table,
)

try:  # pragma: no cover - readline is absent on some platforms
    import readline  # noqa: F401  (enables history and line editing)
except ImportError:  # pragma: no cover
    pass

VERSION = "1.5.0"

HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Control center (keyboard)", [
        ("1..9 / a", "overview · systems · models · policies · traces · "
                     "approvals · changes · replay · drift · audit"),
        ("j k ↑ ↓", "move selection"),
        ("Enter", "open selected record (system / policy / trace / approval)"),
        ("Esc", "back   ·   Tab next view   ·   r refresh   ·   ? help"),
        ("w", "in traces: WHY explanation for the selected decision"),
        ("A D E C", "in an approval: approve / deny / escalate / comment"),
        ("q · Ctrl+C", "exit"),
    ]),
    ("Governance views (commands)", [
        ("/systems [q] · /system <id>", "AI system registry / operator plate"),
        ("/models", "model registry: approval, data ceilings, environments"),
        ("/policies", "tool policies + governance sets; Enter for the spec"),
        ("/traces [system]", "decision records; Enter for the trace timeline"),
        ("/evaluations [system]", "governance/benchmark evaluation runs"),
        ("/approvals", "human oversight queue; Enter for the auth console"),
        ("/changes [system]", "AI change records + lifecycle states"),
        ("/drift [system]", "drift signals / per-system instrument"),
        ("/audit <system>", "governance evidence report"),
        ("/score <system>", "governance score instrument"),
        ("/why <record-id>", "the WHY explanation, from evidence"),
        ("/replay system <id> [set-id]", "candidate-policy simulation lab"),
        ("/replay <run-id> [policy]", "V1 run-level replay"),
    ]),
    ("Governed agent (HELIOS shell)", [
        ("<message>", "send to the agent; every tool flows through the broker"),
        ("/sessions · /session <id>", "list / resume persistent sessions"),
        ("/tools", "tool manifests: capability, base risk, scopes"),
        ("/trace [run-id]", "hierarchical decision trace"),
        ("/resume · /cancel [run-id]", "continue / cancel a run"),
        ("/approve <id> · /deny <id>", "decide a pending action"),
        ("/ask <prompt>", "one-shot governed completion"),
    ]),
    ("Session & gateways", [
        ("/help · /status · /clear", "help · gateway/mode/model · clear history"),
        ("/gateway [name] · /connect <name>", "show or switch gateway"),
        ("/model [name] · /models discovered · /refresh", "model selection"),
        ("/quit", "exit"),
    ]),
    ("Workspaces, web research, evolution (V1 surfaces)", [
        ("/workspace list|use <id>|status", "domain workspaces"),
        ("/workflow list|run <id> [k=v] · /brief", "governed workflows"),
        ("/evidence <execution-id>", "evidence + claims for an execution"),
        ("/web sources|status|search|read|transcript", "governed read path"),
        ("/evolve [list|apply <id>]", "failure mining → proposals"),
    ]),
]


class HeliosTUI:
    def __init__(self, gateway: str = "helios") -> None:
        self.profile: GatewayProfile = get_gateway(gateway)
        self.model: str | None = self.profile.default_model
        self.history: list[dict[str, str]] = []
        self.models: list[str] = []
        self.workspace: str | None = None
        self.agent = AgentPane(self._call)
        # command-center state
        self.view = "overview"
        self.cursor = 0
        self.stack: list[str] = []
        self.cache: dict = {}
        self.items: list[dict] = []
        self.detail: dict | None = None
        self.last_error: tuple | None = None

    # -- presentation ------------------------------------------------------

    @property
    def governed(self) -> bool:
        return self.profile.mode == "helios"

    def print_help(self) -> None:
        width = t.W()
        print()
        print(t.section("HELIOS / OPERATOR REFERENCE", width))
        for title, rows in HELP_SECTIONS:
            print()
            print(t.c(f"  {title}", "sea", bold=True))
            for cmd, desc in rows:
                print(f"    {t.c(t.fit(cmd, 34).ljust(34), 'text')} "
                      f"{t.c(t.fit(desc, width - 42), 'dim')}")
        print()

    def print_status(self) -> None:
        width = t.W()
        print()
        print(t.section("HELIOS / SESSION STATUS", width))
        print(t.kv("gateway", self.profile.name))
        print(t.kv("mode", "GOVERNED" if self.governed else "DIRECT"))
        print(t.kv("base url", self.profile.base_url))
        print(t.kv("model", self.model or "auto (router decides)"))
        key_set = bool(self.profile.resolve_api_key())
        print(t.kv("api key", t.c("set", "accent") if key_set
                   else t.c("not set", "warn")))
        print(t.kv("view", self.view))
        print()

    # -- transport -----------------------------------------------------------

    def _call(self, method: str, path: str, payload: dict | None = None,
              quiet: bool = False):
        if not self.governed:
            self.last_error = (path, 0, "requires the governed helios gateway")
            if not quiet:
                print(t.diag("HELIOS / TRANSPORT", [
                    ("path", path),
                    ("reason", "this command needs the governed helios gateway"),
                    ("remedy", "/gateway helios"),
                ], severity="warn"))
            return None
        url = self.profile.base_url.rstrip("/") + path
        headers = request_headers(self.profile)
        try:
            if method == "GET":
                response = httpx.get(url, headers=headers,
                                     timeout=self.profile.timeout_s)
            else:
                response = httpx.post(url, json=payload, headers=headers,
                                      timeout=self.profile.timeout_s)
        except httpx.HTTPError as exc:
            self.last_error = (path, 0, str(exc))
            if not quiet:
                print(t.diag("HELIOS / TRANSPORT FAULT", [
                    ("path", path), ("error", str(exc)[:120]),
                ], note="NO REQUEST REACHED THE CONTROL PLANE", severity="crit"))
            return None
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except Exception:  # noqa: BLE001
                detail = response.text
            self.last_error = (path, response.status_code, str(detail))
            if not quiet:
                print(t.diag("HELIOS / CONTROL PLANE RESPONSE", [
                    ("path", path),
                    ("status", str(response.status_code)),
                    ("detail", t.fit(str(detail), 100)),
                ], note="NOTHING WAS EXECUTED", severity="crit"))
            return None
        self.last_error = None
        return response.json()

    # -- views -----------------------------------------------------------------

    def _fetch(self, view: str) -> None:
        self.items = []
        if view == "overview":
            self.cache["overview"] = {
                "health_ok": self._call("GET", "/health", quiet=True) is not None,
                "authenticated": True,
                "policies": self._call("GET", "/v1/policies", quiet=True),
                "systems": self._call("GET", "/v1/systems", quiet=True),
                "models": self._call("GET", "/v1/models", quiet=True),
                "decisions": self._call("GET", "/v1/decisions?limit=200",
                                        quiet=True),
                "approvals": self._call("GET", "/v1/approvals", quiet=True),
                "drift": self._call("GET", "/v1/drift", quiet=True),
                "evaluations": self._call("GET", "/v1/evaluations?limit=5",
                                          quiet=True),
            }
        elif view == "systems":
            data = self._call("GET", "/v1/systems", quiet=True)
            self.cache[view] = data
            self.items = (data or {}).get("systems") or []
        elif view == "models":
            data = self._call("GET", "/v1/models", quiet=True)
            self.cache[view] = data
            self.items = (data or {}).get("models") or []
        elif view == "policies":
            data = self._call("GET", "/v1/policies", quiet=True)
            self.cache[view] = data
            self.items = (data or {}).get("governance_policies") or []
        elif view == "traces":
            data = self._call("GET", "/v1/decisions?limit=60", quiet=True)
            self.cache[view] = data
            self.items = (data or {}).get("decisions") or []
        elif view == "approvals":
            data = self._call("GET", "/v1/approvals", quiet=True)
            self.cache[view] = data
            self.items = (data or {}).get("approvals") or []
        elif view == "changes":
            data = self._call("GET", "/v1/changes", quiet=True)
            self.cache[view] = data
            self.items = (data or {}).get("changes") or []
        elif view == "drift":
            data = self._call("GET", "/v1/drift", quiet=True)
            self.cache[view] = data
            self.items = (data or {}).get("signals") or []

    def enter(self, view: str, *, push: bool = True) -> None:
        if push and view != self.view:
            self.stack.append(self.view)
        self.view = view
        self.cursor = 0
        self.detail = None
        self._fetch(view)
        self.render()

    def back(self) -> None:
        if self.detail is not None:
            self.detail = None
            self.render()
            return
        if self.stack:
            self.enter(self.stack.pop(), push=False)
        else:
            self.enter("overview", push=False)

    def render(self) -> None:
        width = t.W()
        print()
        print(t.equipment_header(
            version=VERSION,
            environment=os.environ.get("HELIOS_AGENT_ENV", "operator"),
            gateway=self.profile.name,
            tenant=self.profile.name,
            state="governed" if self.governed else "direct",
            width=width, governed=self.governed))
        print()
        if self.last_error and self.view in ("overview",):
            path, status, detail = self.last_error
            print(t.diag("HELIOS / DEGRADED LINK", [
                ("path", path), ("status", str(status) or "transport"),
                ("detail", t.fit(detail, 90)),
            ], note="values below reflect the last successful responses",
                severity="warn"))
            print()
        self._render_view(width)
        print()
        print(t.footer(view=self.view, gateway=self.profile.name,
                       governed=self.governed, width=width,
                       hint="1-9/a views · j/k move · enter open · esc back · "
                            "? help · q quit"))

    def _render_view(self, width: int) -> None:
        view = self.view
        if view == "overview":
            print(screens.overview(self.cache.get("overview") or {}, width))
        elif view == "systems":
            print(render_systems_table(self.cache.get("systems") or {}, width))
            self._cursor_line("system")
        elif view == "models":
            print(render_models_table(self.cache.get("models") or {}, width))
            self._cursor_line("model")
        elif view == "policies":
            if self.detail:
                print(screens.policy_spec(self.detail, width))
            else:
                print(render_policies(self.cache.get("policies") or {}, width))
                self._cursor_line("governance set (enter for spec)")
        elif view == "traces":
            if self.detail:
                print(self.detail.get("render", ""))
            else:
                print(render_decisions(self.cache.get("traces") or {}, width))
                self._cursor_line("decision (enter for trace, w for why)")
        elif view == "approvals":
            if self.detail:
                print(screens.approval_console(self.detail, width))
            else:
                print(render_approvals_queue(self.cache.get("approvals") or {},
                                             width))
                self._cursor_line("approval (enter for the auth console)")
        elif view == "changes":
            print(render_changes(self.cache.get("changes") or {}, width))
            self._cursor_line("change")
        elif view == "drift":
            if self.detail:
                print(screens.drift_view(self.detail, width))
            else:
                print(render_drift(self.cache.get("drift") or {}, width))
                self._cursor_line("signal")
        elif view == "replay":
            print(self.cache.get("replay_render")
                  or t.c("  use: /replay system <system-id> [candidate-set-id]",
                         "dim"))
        elif view == "audit":
            print(render_audit_summary(self.cache.get("audit") or {}, width))
        elif view == "system":
            print(render_audit_summary(self.cache.get("system") or {}, width))
        elif view == "assurance":
            print(screens.assurance_view(self.cache.get("score") or {}, width))

    def _cursor_line(self, kind: str) -> None:
        if not self.items:
            return
        item = self.items[min(self.cursor, len(self.items) - 1)]
        ident = item.get("system_id") or item.get("id") or item.get("name") or "?"
        print(t.c(f"  {t.GL['arrow']} {kind} {str(ident)[:48]} "
                  f"[{self.cursor + 1}/{len(self.items)}]", "faint"))

    # -- opening records ---------------------------------------------------------

    def open_selected(self) -> None:
        if not self.items:
            self.render()
            return
        item = self.items[min(self.cursor, len(self.items) - 1)]
        if self.view == "systems":
            audit = self._call("GET", f"/v1/systems/{item['system_id']}/audit")
            if audit:
                self.stack.append(self.view)
                self.view = "system"
                self.cache["system"] = audit
                self.render()
        elif self.view == "policies":
            self.detail = item
            self.render()
        elif self.view == "traces":
            run_id = item.get("run_id")
            data = self._call("GET", f"/v1/agent/runs/{run_id}/events") \
                if run_id else None
            self.detail = {"render": screens.trace_timeline(
                (data or {}).get("events", []), (data or {}).get("run") or {},
                t.W())} if data else None
            self.render()
        elif self.view == "approvals":
            self.detail = item
            self.render()
        elif self.view == "drift":
            system_id = item.get("system_id")
            if system_id:
                state = self._call("GET", f"/v1/systems/{system_id}/drift")
                if state:
                    state["findings"] = [item]
                    self.detail = state
                    self.render()
        else:
            self.render()

    def why_selected(self) -> None:
        if self.view != "traces" or not self.items:
            return
        item = self.items[min(self.cursor, len(self.items) - 1)]
        explanation = self._call("GET", f"/v1/decisions/{item['id']}/explanation")
        if explanation:
            self.stack.append(self.view)
            self.view = "traces"
            self.detail = {"render": screens.why_view(explanation, t.W())}
            self.render()

    # -- approval actions -----------------------------------------------------------

    def approval_action(self, key: str) -> None:
        if not self.detail:
            return
        approval_id = self.detail.get("id", "")
        by = os.environ.get("USER", "operator")
        if key == "a":
            data = self._call("POST", f"/v1/agent/approvals/{approval_id}/decide",
                              {"decision": "approved", "decided_by": by,
                               "comment": "approved from control center"})
        elif key == "d":
            data = self._call("POST", f"/v1/agent/approvals/{approval_id}/decide",
                              {"decision": "denied", "decided_by": by,
                               "comment": "denied from control center"})
        elif key == "e":
            print(t.c("  escalate to: ", "dim"), end="")
            target = input().strip() or "security-lead"
            data = self._call("POST", f"/v1/agent/approvals/{approval_id}/escalate",
                              {"to": target, "by": by,
                               "reason": "escalated from control center"})
        elif key == "c":
            print(t.c("  comment: ", "dim"), end="")
            text = input().strip()
            data = self._call("POST", f"/v1/agent/approvals/{approval_id}/comments",
                              {"by": by, "text": text or "reviewed"})
        else:
            return
        if data:
            self.detail = data
            self.render()

    # -- the loop ---------------------------------------------------------------------

    def run(self) -> None:
        width = t.W()
        print()
        print(t.equipment_header(
            version=VERSION,
            environment=os.environ.get("HELIOS_AGENT_ENV", "operator"),
            gateway=self.profile.name,
            tenant=self.profile.name,
            state="governed" if self.governed else "direct",
            width=width, governed=self.governed))
        print()
        print(t.c("  SYSTEMS ARE OBSERVED.   ACTIONS ARE CONSTRAINED.", "dim"))
        print(t.c("  DECISIONS ARE EVIDENCED.   GOVERNANCE IS ASSURED.", "dim"))
        print()
        print(t.c("  type /help for the operator reference · plain text talks "
                  "to the governed agent", "faint"))
        self.enter("overview", push=False)
        while True:
            try:
                line = input(t.prompt_str())
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not self.handle_input(line.strip()):
                break

    def handle_input(self, line: str) -> bool:
        if not line:
            self.open_selected()
            return True
        low = line.lower()
        if low in ("q", "/quit", "/exit"):
            return False
        if low in ("?", "/help"):
            self.print_help()
            return True
        if low == "r":
            self._fetch(self.view)
            self.render()
            return True
        if low in ("esc", "\x1b"):
            self.back()
            return True
        if low == "tab":
            order = [v for _, v, _ in screens.NAV]
            self.enter(order[(order.index(self.view) + 1) % len(order)]
                       if self.view in order else "overview")
            return True
        if low in ("j", "down", "\x1b[b"):
            self.cursor = min(self.cursor + 1, max(0, len(self.items) - 1))
            self.render()
            return True
        if low in ("k", "up", "\x1b[a"):
            self.cursor = max(0, self.cursor - 1)
            self.render()
            return True
        if low == "w":
            self.why_selected()
            return True
        if self.view == "approvals" and self.detail and low in ("a", "d", "e", "c"):
            self.approval_action(low)
            return True
        keys = {k: v for k, v, _ in screens.NAV}
        if low in keys:
            self.enter(keys[low])
            return True
        if line.startswith("/"):
            return self.handle_command(line)
        self.agent.chat(line)
        return True

    # -- command console (V1 surface preserved) -------------------------------------------

    def handle_command(self, line: str) -> bool:
        parts = line.split()
        command, args = parts[0], parts[1:]

        if command in ("/quit", "/exit"):
            return False
        if command == "/help":
            self.print_help()
        elif command in ("/gateway", "/connect"):
            if not args:
                rows = []
                for name in sorted(all_gateways()):
                    p = all_gateways()[name]
                    marker = t.c(t.GL["dot"], "accent") \
                        if name == self.profile.name else t.c(t.GL["dot_off"], "faint")
                    rows.append([marker, name,
                                 "governed" if p.mode == "helios" else "direct",
                                 p.source, p.base_url])
                print(t.table(["", "gateway", "mode", "source", "base url"], rows,
                              drop=["source"]))
            else:
                try:
                    self.profile = get_gateway(args[0])
                    self.model = self.profile.default_model
                    self.models = []
                    print(t.c(f"  {t.GL['allow']} switched to {self.profile.name} "
                              f"({'GOVERNED' if self.governed else 'DIRECT'})",
                              "accent"))
                except KeyError as exc:
                    print(t.c(f"  × {exc.args[0]}", "crit"))
        elif command == "/model":
            if args:
                self.model = args[0]
                print(t.c(f"  {t.GL['allow']} model set to {self.model}", "accent"))
            else:
                print(t.kv("model", self.model or "auto (router decides)"))
        elif command == "/models":
            if args and args[0] == "discovered":
                if not self.models:
                    print(t.c("  (none cached — run /refresh)", "faint"))
                for model_id in self.models:
                    print(f"    {t.c(t.GL['bullet'], 'faint')} {model_id}")
            else:
                self.enter("models")
        elif command == "/refresh":
            try:
                self.models = discover_models(self.profile)
                print(t.c(f"  {t.GL['allow']} discovered {len(self.models)} models "
                          f"from {self.profile.base_url}", "accent"))
            except Exception as exc:  # noqa: BLE001
                print(t.diag("HELIOS / DISCOVERY FAULT", [("error", str(exc)[:100])],
                             severity="crit"))
        elif command == "/status":
            self.print_status()
        elif command == "/clear":
            self.history = []
            print(t.c(f"  {t.GL['allow']} conversation cleared", "accent"))
        # governance views
        elif command == "/systems":
            self.enter("systems")
        elif command == "/system":
            if not args:
                self.enter("systems")
            else:
                audit = self._call("GET", f"/v1/systems/{args[0]}/audit")
                if audit:
                    self.stack.append(self.view)
                    self.view = "system"
                    self.cache["system"] = audit
                    self.render()
        elif command == "/policies":
            self.enter("policies")
        elif command == "/traces":
            self.enter("traces")
        elif command == "/evaluations":
            query = f"?system_id={args[0]}" if args else ""
            data = self._call("GET", f"/v1/evaluations{query}")
            if data is not None:
                print(render_evaluations(data, t.W()))
        elif command == "/approvals":
            self.enter("approvals")
        elif command == "/changes":
            self.enter("changes")
        elif command == "/drift":
            if args:
                state = self._call("GET", f"/v1/systems/{args[0]}/drift")
                if state:
                    print(screens.drift_view(state, t.W()))
            else:
                self.enter("drift")
        elif command == "/audit":
            if not args:
                print(t.c("  × usage: /audit <system-id>", "crit"))
            else:
                audit = self._call("GET", f"/v1/systems/{args[0]}/audit")
                if audit:
                    print(render_audit_summary(audit, t.W()))
        elif command == "/score":
            if not args:
                print(t.c("  × usage: /score <system-id>", "crit"))
            else:
                score = self._call("GET", f"/v1/systems/{args[0]}/score")
                if score:
                    print(screens.assurance_view(score, t.W()))
        elif command == "/why":
            if not args:
                print(t.c("  × usage: /why <decision-record-id>", "crit"))
            else:
                explanation = self._call(
                    "GET", f"/v1/decisions/{args[0]}/explanation")
                if explanation:
                    print(screens.why_view(explanation, t.W()))
        elif command == "/replay":
            if args and args[0] == "system":
                if len(args) < 2:
                    print(t.c("  × usage: /replay system <system-id> "
                              "[candidate-set-id]", "crit"))
                else:
                    payload = {}
                    if len(args) > 2:
                        payload["candidate_governance_set_id"] = args[2]
                    report = self._call("POST", f"/v1/replay/systems/{args[1]}",
                                        payload)
                    if report:
                        from helios.tui.governance import render_replay_report
                        self.cache["replay_render"] = render_replay_report(
                            report, t.W())
                        self.view = "replay"
                        self.render()
            else:
                self.agent.replay(args)
        # agent surface
        elif command == "/sessions":
            self.agent.list_sessions()
        elif command == "/session":
            if args:
                self.agent.use_session(args[0])
            else:
                self.agent.ensure_session()
        elif command == "/tools":
            self.agent.list_tools()
        elif command == "/trace":
            self.agent.show_trace(args[0] if args else None)
        elif command == "/resume":
            self.agent.resume(args[0] if args else None)
        elif command == "/cancel":
            self.agent.cancel(args[0] if args else None)
        elif command == "/ask":
            if not args:
                print(t.c("  × usage: /ask <prompt>", "crit"))
            else:
                self.send(" ".join(args))
        elif command in ("/approve", "/deny"):
            if not args:
                print(t.c(f"  × usage: {command} <approval-id>", "crit"))
            else:
                decision = "approved" if command == "/approve" else "denied"
                approval_id = args[0]
                if len(approval_id) < 36:
                    pending = self._call("GET", "/v1/approvals?status=pending",
                                         quiet=True)
                    matches = [a["id"] for a in
                               (pending or {}).get("approvals", [])
                               if a["id"].startswith(approval_id)]
                    if len(matches) == 1:
                        approval_id = matches[0]
                    else:
                        print(t.c(f"  × {len(matches)} pending approvals match "
                                  f"'{args[0]}' — use the full id", "crit"))
                        return True
                data = self._call(
                    "POST", f"/v1/agent/approvals/{approval_id}/decide",
                    {"decision": decision,
                     "decided_by": os.environ.get("USER", "operator")})
                if data:
                    print(t.c(f"  {t.glyph_for(data['status'])} "
                              f"{data['id'][:8]} → {data['status']}", "accent"))
                    if data.get("run_id"):
                        self.agent.resume(data["run_id"])
        elif command == "/web":
            self.handle_web(args)
        elif command == "/workspace":
            self.handle_workspace(args)
        elif command == "/workflow":
            self.handle_workflow(args)
        elif command == "/brief":
            self.run_workflow(
                "daily_brief" if self.workspace != "finance"
                else "operations_brief", {})
        elif command == "/evidence":
            if not args:
                print(t.c("  × usage: /evidence <execution-id>", "crit"))
            else:
                data = self._call("GET", f"/v1/workflows/executions/{args[0]}")
                if data:
                    self._print_evidence(data)
        elif command == "/evolve":
            self.handle_evolve(args)
        else:
            print(t.c(f"  × unknown command {command} — try /help", "crit"))
        return True

    # -- governed completion -----------------------------------------------------

    def send(self, prompt: str) -> None:
        headers = request_headers(self.profile)
        if self.governed:
            url = completion_endpoint(self.profile)
            payload = build_governed_payload(prompt, self.model)
        else:
            url = self.profile.base_url.rstrip("/") + "/chat/completions"
            payload = build_direct_payload(prompt, self.model or "", self.history)
        try:
            response = httpx.post(url, json=payload, headers=headers,
                                  timeout=self.profile.timeout_s)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            print(t.diag("HELIOS / TRANSPORT FAULT", [("error", str(exc)[:120])],
                         severity="crit"))
            return
        if self.governed:
            out = extract_governed_output(data)
            self.history.append({"role": "user", "content": prompt})
            self.history.append({"role": "assistant",
                                 "content": out.get("output", "")})
            print()
            print(out.get("output") or "")
            meta = (f"trace={out.get('trace_id')} · "
                    f"{(out.get('model') or {}).get('provider', '?')}/"
                    f"{(out.get('model') or {}).get('id', '?')} · "
                    f"${out.get('cost_usd', 0):.4f} · {out.get('latency_ms', 0)}ms")
            print(t.c(f"  {meta}", "faint"))
        else:
            text = extract_direct_output(data)
            self.history.append({"role": "user", "content": prompt})
            self.history.append({"role": "assistant", "content": text})
            print()
            print(text)

    # -- web research (governed read path) -------------------------------------------

    def _print_web_result(self, data: dict) -> None:
        rows = []
        for status in data.get("source_status", []):
            detail = ""
            if status.get("results"):
                detail = f"{status['results']} results"
            elif status.get("detail") and status["status"] != "ok":
                detail = status["detail"][:60]
            rows.append([t.glyph_for("allow" if status["status"] == "ok"
                                     else "deny"),
                         status["source"], status["status"],
                         t.c(detail, "dim")])
        print(t.section("SOURCES"))
        print(t.table(["", "source", "status", "detail"], rows))
        documents = data.get("documents", [])
        if documents:
            print(t.section("EVIDENCE"))
        for i, doc in enumerate(documents, 1):
            title = doc.get("title") or doc.get("url") or "(untitled)"
            print(f"  {t.c(f'[{i}]', 'accent')} {t.c(title, 'text', bold=True)}")
            meta = (f"{doc.get('source')} · {doc.get('trust')} · retrieved "
                    f"{(doc.get('retrieved_at') or '')[:19]}")
            if doc.get("warnings"):
                meta += " · " + ",".join(doc["warnings"])
            print(f"      {t.c(meta, 'dim')}")
            snippet = (doc.get("content") or "").strip().replace("\n", " ")[:160]
            if snippet:
                print(f"      {t.c(snippet, 'dim')}")
        print(t.c(f"  job={data.get('job_id')} · {len(documents)} documents",
                  "faint"))

    def handle_web(self, args: list[str]) -> None:
        if not args:
            print(t.c("  × usage: /web sources|status|search <query>|"
                      "read <url>|transcript <url>", "crit"))
            return
        sub, rest = args[0], args[1:]
        if sub == "sources":
            data = self._call("GET", "/v1/web/sources")
            if data:
                rows = []
                for src in data.get("sources", []):
                    health = src["health"]
                    caps = ",".join(k for k, v in src["capabilities"].items()
                                    if v) or "-"
                    rows.append([
                        t.glyph_for("allow" if health["status"] == "ok"
                                    else "deny"),
                        src["name"], f"v{src['version']}", src["trust_level"],
                        caps, t.c(health["status"], "dim"),
                    ])
                print(t.table(["", "adapter", "ver", "trust", "capabilities",
                               "health"], rows, drop=["capabilities"]))
        elif sub == "status":
            data = self._call("GET", "/v1/web/jobs")
            if data:
                print(t.table(
                    ["when", "operation", "status", "docs"],
                    [[j["created_at"][:19], j["operation"], j["status"],
                      str(j["documents"])] for j in data.get("jobs", [])],
                    drop=["when"]))
        elif sub in ("search", "read", "transcript"):
            if not rest:
                print(t.c(f"  × usage: /web {sub} <argument>", "crit"))
                return
            path = {"search": "/v1/web/search", "read": "/v1/web/read",
                    "transcript": "/v1/web/transcript"}[sub]
            payload = ({"query": " ".join(rest)} if sub == "search"
                       else {"url": rest[0]})
            data = self._call("POST", path, payload)
            if data:
                self._print_web_result(data)
        else:
            print(t.c(f"  × unknown /web subcommand '{sub}'", "crit"))

    # -- workspaces & workflows (governed, V1 surfaces) --------------------------------

    def _print_execution(self, execution: dict) -> None:
        status = execution.get("status", "?")
        lines = [
            t.section("GOVERNED EXECUTION"),
            t.kv("workspace", str(execution.get("workspace_id", "?")).upper()),
            t.kv("workflow", str(execution.get("workflow_id", "?")).upper()),
            t.kv("status", t.glyphed("allow" if status == "completed" else "gate",
                                     status.upper())),
            t.kv("risk", str(execution.get("risk", "informational")).upper()),
            t.kv("evidence", f"{execution.get('evidence_count', 0)} sources"),
            t.kv("confidence", "—" if execution.get("confidence") is None
                 else f"{execution['confidence']:.2f}"),
            t.kv("approval", "REQUIRED" if execution.get("requires_approval")
                 else "not required"),
            t.kv("trace", str(execution.get("trace_id"))),
        ]
        print("\n".join(lines))
        facts = execution.get("facts", [])
        if facts:
            print(t.section("COMPUTED FACTS (DETERMINISTIC)"))
            for fact in facts[:12]:
                fact_text = (fact.get("detail")
                             or f"{fact.get('name')} = {fact.get('value')}")
                print(f"  {t.c(t.GL['bullet'], 'faint')} {fact_text}")
        if execution.get("interpretation"):
            print(t.section("INTERPRETATION (MODEL, EVIDENCE-GROUNDED)"))
            for line in execution["interpretation"][:700].splitlines()[:12]:
                print(f"  {line}")
        if execution.get("recommendation"):
            print(t.section("RECOMMENDATION"))
            print(f"  {execution['recommendation'][:300]}")
        print(t.c(f"  execution={execution.get('id')} · "
                  f"{execution.get('latency_ms')}ms · "
                  f"${execution.get('cost_usd')}", "faint"))

    def _print_evidence(self, execution: dict) -> None:
        evidence = execution.get("evidence", [])
        print(t.section(f"EVIDENCE ({len(evidence)})"))
        rows = []
        for i, ev in enumerate(evidence):
            rows.append([f"[{i}]", ev.get("kind", "?"),
                         str(ev.get("source"))[:34],
                         t.c(str(ev.get("trust")), "dim"),
                         t.c((ev.get("excerpt") or "")[:50], "dim")])
        print(t.table(["", "kind", "source", "trust", "excerpt"], rows,
                      drop=["excerpt"]))
        claims = execution.get("claims", [])
        print(t.section(f"CLAIMS ({len(claims)})"))
        for claim in claims:
            glyph = t.glyph_for("allow" if claim.get("category")
                                in ("computation", "fact") else "gate")
            print(f"  {glyph} {t.c(claim.get('category', '?'), 'dim')} "
                  f"{t.fit(str(claim.get('text') or claim.get('detail') or ''), 70)}")

    def handle_workspace(self, args: list[str]) -> None:
        if not args or args[0] == "list":
            data = self._call("GET", "/v1/workspaces")
            if data:
                rows = []
                for ws in data.get("workspaces", []):
                    marker = t.c(t.GL["dot"], "accent") \
                        if ws["id"] == self.workspace else t.c(t.GL["dot_off"], "faint")
                    rows.append([marker, ws["id"], ws.get("domain", ""),
                                 str(len(ws.get("workflows", [])))])
                print(t.table(["", "workspace", "domain", "workflows"], rows))
        elif args[0] == "use" and len(args) > 1:
            self.workspace = args[1]
            print(t.c(f"  {t.GL['allow']} workspace {self.workspace}", "accent"))
        elif args[0] == "status":
            if not self.workspace:
                print(t.c("  (no workspace selected — /workspace use <id>)",
                          "faint"))
                return
            data = self._call("GET", f"/v1/workspaces/{self.workspace}")
            if data:
                print(t.section(f"WORKSPACE / {self.workspace}"))
                for key in ("name", "domain", "description"):
                    print(t.kv(key, t.fit(str(data.get(key, "—")), 60)))
        else:
            print(t.c("  × usage: /workspace list|use <id>|status", "crit"))

    def handle_workflow(self, args: list[str]) -> None:
        if not args or args[0] == "list":
            if not self.workspace:
                print(t.c("  (select a workspace first: /workspace use <id>)",
                          "faint"))
                return
            data = self._call("GET",
                              f"/v1/workspaces/{self.workspace}/workflows")
            if data:
                rows = [[w.get("id", w) if isinstance(w, dict) else w,
                         t.fit(str((w.get("description") if isinstance(w, dict)
                                    else "")), 50)]
                        for w in data.get("workflows", [])]
                print(t.table(["workflow", ""], rows))
        elif args[0] == "run" and len(args) > 1:
            inputs = {}
            for kv in args[2:]:
                if "=" in kv:
                    key, value = kv.split("=", 1)
                    inputs[key] = value
            self.run_workflow(args[1], inputs)
        elif args[0] == "history":
            data = self._call("GET", "/v1/workflows/executions")
            if data:
                rows = [[e["id"][:8], e.get("workflow_id", ""),
                         e.get("status", ""), (e.get("created_at") or "")[:19]]
                        for e in data.get("executions", [])]
                print(t.table(["id", "workflow", "status", "when"], rows,
                              drop=["when"]))
        else:
            print(t.c("  × usage: /workflow list|run <id> [k=v ...]|history",
                      "crit"))

    def run_workflow(self, workflow_id: str, input_data: dict) -> None:
        if not self.workspace:
            print(t.c("  × select a workspace first: /workspace use <id>",
                      "crit"))
            return
        execution = self._call("POST", "/v1/workflows/run", {
            "workspace_id": self.workspace,
            "workflow_id": workflow_id,
            "input": input_data,
        })
        if execution:
            self._print_execution(execution)

    def handle_evolve(self, args: list[str]) -> None:
        if not args or args[0] == "list":
            data = self._call("GET", "/v1/evolution/proposals")
            if data:
                rows = [[p["id"][:8], p.get("kind", ""), p.get("status", ""),
                         t.fit(p.get("rationale", ""), 40)]
                        for p in data.get("proposals", [])]
                print(t.table(["id", "kind", "status", "rationale"], rows))
        elif args[0] == "apply" and len(args) > 1:
            data = self._call("POST",
                              f"/v1/evolution/proposals/{args[1]}/approve")
            if data:
                print(t.c(f"  {t.GL['allow']} applied {data.get('id', '')[:8]}",
                          "accent"))
        else:
            print(t.c("  × usage: /evolve [list|apply <id>]", "crit"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="helios-tui")
    parser.add_argument("--gateway", default="helios")
    args = parser.parse_args(argv)
    HeliosTUI(args.gateway).run()


if __name__ == "__main__":  # pragma: no cover
    main()
