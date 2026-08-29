"""
The interactive loop for `python -m helios.tui`.

Design constraints:

* Zero extra dependencies — ANSI + readline from the standard library, httpx
  (already a Helios dependency) for transport.
* Works over SSH, in narrow terminals, and honors NO_COLOR/TERM=dumb.
* The governed path is the default and is always visibly marked GOVERNED;
  direct gateways are marked DIRECT so users know when governance is
  bypassed.
"""

from __future__ import annotations

import argparse
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
from helios.tui import ui
from helios.tui.ui import Spinner, badge, bullet, c, error, kv, panel, risk_badge, success, table

try:  # pragma: no cover - readline is absent on some platforms
    import readline  # noqa: F401  (enables history + Ctrl+L clear-screen)
except ImportError:  # pragma: no cover
    pass

HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Session", [
        ("/help", "Show this help"),
        ("/status", "Gateway, mode, model, endpoint"),
        ("/clear", "Clear the conversation history"),
        ("/quit", "Exit"),
    ]),
    ("Models & gateways", [
        ("/gateway [name]", "Show or switch the active gateway"),
        ("/connect <name>", "Alias for /gateway <name>"),
        ("/model [name]", "Show or set the model"),
        ("/models", "List models cached from the last /refresh"),
        ("/refresh", "Discover models via GET /models"),
    ]),
    ("Workspaces & workflows (governed)", [
        ("/workspace list|use <id>|status", "Domain workspaces"),
        ("/workflow list|run <id> [k=v ...]|history", "Governed workflows"),
        ("/brief", "Run the active workspace's daily brief"),
        ("/evidence <execution-id>", "Evidence + claims for an execution"),
    ]),
    ("Web research (governed, read-only)", [
        ("/web sources|status", "Source registry / audit trail"),
        ("/web search <query>", "Multi-source search with per-source status"),
        ("/web read <url>", "Read an allowlisted public page"),
        ("/web transcript <url>", "Public YouTube transcript"),
    ]),
    ("Approvals & self-evolution (governed)", [
        ("/approvals", "Pending approval requests"),
        ("/approve <id> · /deny <id>", "Decide a pending action"),
        ("/evolve [list|apply <id>]", "Failure mining → improvement proposals"),
    ]),
]

KEYS_HINT = "Ctrl+K focus prompt · Ctrl+L clear screen · Ctrl+C exit"


class HeliosTUI:
    def __init__(self, gateway: str = "helios") -> None:
        self.profile: GatewayProfile = get_gateway(gateway)
        self.model: str | None = self.profile.default_model
        self.history: list[dict[str, str]] = []
        self.models: list[str] = []
        self.workspace: str | None = None  # active domain workspace

    # -- presentation -----------------------------------------------------

    @property
    def governed(self) -> bool:
        return self.profile.mode == "helios"

    def mode_badge(self) -> str:
        return badge("GOVERNED", "green") if self.governed else badge("DIRECT", "yellow")

    def prompt_str(self) -> str:
        return ui.build_prompt(
            self.profile.name, self.governed, self.workspace, self.model or "auto"
        )

    def print_help(self) -> None:
        for title, entries in HELP_SECTIONS:
            print("  " + c(title, "sea", bold=True))
            for cmd, desc in entries:
                print(f"    {c(cmd.ljust(42), 'green')}{c(desc, 'dim')}")
        print("\n  " + c(KEYS_HINT, "dim"))

    def print_status(self) -> None:
        key_env = self.profile.api_key_env or "-"
        key_state = (
            c("set", "green") if self.profile.resolve_api_key() else c("not set", "yellow")
        )
        print(panel("STATUS", [
            kv("gateway", f"{c(self.profile.name, 'fg', bold=True)}  {self.mode_badge()}"),
            kv("endpoint", c(completion_endpoint(self.profile), "fg")),
            kv("model", c(self.model or "auto (router decides)", "fg")),
            kv("workspace", c(self.workspace or "none", "fg")),
            kv("key env", f"{c(key_env, 'fg')} ({key_state})"),
        ]))

    # -- commands ---------------------------------------------------------

    def handle_command(self, line: str) -> bool:
        """Returns False when the loop should exit."""
        parts = line.split()
        command, args = parts[0], parts[1:]

        if command in ("/quit", "/exit"):
            return False
        if command == "/help":
            self.print_help()
        elif command in ("/gateway", "/connect"):
            if not args:
                profiles = all_gateways()
                rows = []
                for name in sorted(profiles):
                    p = profiles[name]
                    marker = c("●", "green") if name == self.profile.name else c("·", "dim")
                    mode = "governed" if p.mode == "helios" else "direct"
                    rows.append([marker, name, mode, p.source, p.base_url])
                print(table(["", "gateway", "mode", "source", "base url"], rows))
            else:
                try:
                    self.profile = get_gateway(args[0])
                    self.model = self.profile.default_model
                    self.models = []
                    print(success(f"Switched to {c(self.profile.name, 'fg', bold=True)} "
                                  f"{self.mode_badge()}"))
                except KeyError as exc:
                    print(error(exc.args[0]))
        elif command == "/model":
            if args:
                self.model = args[0]
                print(success(f"Model set to {c(self.model, 'fg', bold=True)}"))
            else:
                print(kv("model", self.model or "auto (router decides)"))
        elif command == "/models":
            if not self.models:
                print(c("  No cached models — run /refresh first.", "dim"))
            for model_id in self.models:
                print(bullet(model_id, mark="·", color="dim"))
        elif command == "/refresh":
            try:
                with Spinner("discovering models"):
                    self.models = discover_models(self.profile)
                print(success(f"Discovered {c(str(len(self.models)), 'fg', bold=True)} "
                              f"models from {self.profile.base_url}"))
            except Exception as exc:  # noqa: BLE001 - show the user, keep looping
                print(error(f"Model discovery failed: {exc}"))
        elif command == "/status":
            self.print_status()
        elif command == "/clear":
            self.history = []
            print(success("Conversation cleared."))
        elif command == "/web":
            self.handle_web(args)
        elif command == "/approvals":
            data = self._web_call("GET", "/v1/approvals?status=pending")
            if data:
                approvals = data.get("approvals", [])
                if not approvals:
                    print(c("  No pending approvals.", "dim"))
                else:
                    print(table(
                        ["id", "action", "risk"],
                        [[a["id"][:8], a["action"], risk_badge(a["risk"])]
                         for a in approvals],
                    ))
        elif command in ("/approve", "/deny"):
            if not args:
                print(error(f"Usage: {command} <approval-id>"))
            else:
                decision = "approved" if command == "/approve" else "denied"
                data = self._web_call(
                    "POST", f"/v1/approvals/{args[0]}/decide",
                    {"decision": decision, "decided_by": os.environ.get("USER", "tui")},
                )
                if data:
                    print(success(f"{data['id'][:8]} → {data['status']}"))
        elif command == "/evolve":
            self.handle_evolve(args)
        elif command == "/workspace":
            self.handle_workspace(args)
        elif command == "/workflow":
            self.handle_workflow(args)
        elif command == "/brief":
            self.run_workflow(
                "daily_brief" if self.workspace != "finance" else "operations_brief", {}
            )
        elif command == "/evidence":
            if not args:
                print(error("Usage: /evidence <execution-id>"))
            else:
                data = self._web_call("GET", f"/v1/workflows/executions/{args[0]}")
                if data:
                    self._print_evidence(data)
        else:
            print(error(f"Unknown command {command} — try /help"))
        return True

    # -- web research (governed read path) --------------------------------

    def _web_call(self, method: str, path: str, payload: dict | None = None):
        if not self.governed:
            print(error("This command needs the governed helios gateway (/gateway helios)."))
            return None
        url = self.profile.base_url.rstrip("/") + path
        headers = request_headers(self.profile)
        try:
            with Spinner("working"):
                if method == "GET":
                    response = httpx.get(url, headers=headers, timeout=self.profile.timeout_s)
                else:
                    response = httpx.post(
                        url, json=payload, headers=headers, timeout=self.profile.timeout_s
                    )
            if response.status_code == 403:
                detail = response.json().get("detail", {})
                lines = [c("Blocked by policy", "red", bold=True)]
                for reason in detail.get("reasons", []) or [detail.get("reason", "")]:
                    if reason:
                        lines.append(c(f"– {reason}", "fg"))
                if detail.get("requires_approval") or detail.get("approval_required"):
                    lines.append(c("approval required", "yellow"))
                print(panel("POLICY", lines, color="red"))
                return None
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            print(error(f"Request failed: {exc}"))
            return None

    def _print_web_result(self, data: dict) -> None:
        rows = []
        for status in data.get("source_status", []):
            detail = ""
            if status.get("results"):
                detail = f"{status['results']} results"
            elif status.get("detail") and status["status"] != "ok":
                detail = status["detail"][:60]
            rows.append([ui.status_dot(status["status"]), status["source"],
                         status["status"], c(detail, "dim")])
        print(c("  Sources", "sea", bold=True))
        print(table(["", "source", "status", "detail"], rows))

        documents = data.get("documents", [])
        if documents:
            print(c("  Evidence", "sea", bold=True))
        for i, doc in enumerate(documents, 1):
            title = doc.get("title") or doc.get("url") or "(untitled)"
            print(bullet(f"{c(f'[{i}]', 'green')} {c(title, 'fg', bold=True)}"))
            meta = (f"{doc.get('source')} · {doc.get('trust')} · "
                    f"retrieved {doc.get('retrieved_at', '')[:19]}")
            if doc.get("warnings"):
                meta += " · " + ",".join(doc["warnings"])
            print(f"      {c(meta, 'dim')}")
            snippet = (doc.get("content") or "").strip().replace("\n", " ")[:180]
            if snippet:
                print(f"      {snippet}")
        print(c(f"  job={data.get('job_id')} · {len(documents)} documents", "dim"))

    def handle_web(self, args: list[str]) -> None:
        if not args:
            print(error("Usage: /web sources|status|search <query>|read <url>|transcript <url>"))
            return
        sub, rest = args[0], args[1:]

        if sub == "sources":
            data = self._web_call("GET", "/v1/web/sources")
            if data:
                rows = []
                for src in data.get("sources", []):
                    health = src["health"]
                    caps = ",".join(k for k, v in src["capabilities"].items() if v) or "-"
                    detail = health.get("detail") or ""
                    rows.append([
                        ui.status_dot(health["status"]), src["name"],
                        f"v{src['version']}", src["trust_level"], caps,
                        c(health["status"] + (f" · {detail[:40]}" if detail else ""), "dim"),
                    ])
                print(table(["", "adapter", "ver", "trust", "capabilities", "health"], rows))
        elif sub == "status":
            data = self._web_call("GET", "/v1/web/jobs")
            if data:
                print(table(
                    ["when", "operation", "status", "docs"],
                    [[j["created_at"][:19], j["operation"], j["status"],
                      str(j["documents"])] for j in data.get("jobs", [])],
                ))
        elif sub == "search":
            if not rest:
                print(error("Usage: /web search <query>"))
                return
            data = self._web_call("POST", "/v1/web/search", {"query": " ".join(rest)})
            if data:
                self._print_web_result(data)
        elif sub == "read":
            if not rest:
                print(error("Usage: /web read <url>"))
                return
            data = self._web_call("POST", "/v1/web/read", {"url": rest[0]})
            if data:
                self._print_web_result(data)
        elif sub == "transcript":
            if not rest:
                print(error("Usage: /web transcript <url>"))
                return
            data = self._web_call("POST", "/v1/web/transcript", {"url": rest[0]})
            if data:
                self._print_web_result(data)
        else:
            print(error(f"Unknown /web subcommand '{sub}' — try /help"))

    # -- workspaces & workflows (governed) ---------------------------------

    def _governance_panel(self, execution: dict) -> str:
        status = execution.get("status", "?")
        status_color = "green" if status == "completed" else "yellow"
        approval = (
            badge("REQUIRED", "yellow")
            if execution.get("requires_approval")
            else c("not required", "dim")
        )
        confidence = execution.get("confidence")
        conf_str = "—" if confidence is None else f"{confidence:.2f}"
        if isinstance(confidence, (int, float)):
            blocks = int(round(confidence * 10))
            conf_str += "  " + c("▰" * blocks, "green") + c("▱" * (10 - blocks), "dim")
        return panel("GOVERNED", [
            kv("workspace", c(str(execution.get("workspace_id", "?")).upper(), "fg", bold=True)),
            kv("workflow", c(str(execution.get("workflow_id", "?")).upper(), "fg", bold=True)),
            kv("status", badge(status.upper(), status_color)),
            kv("risk", risk_badge(execution.get("risk", "informational"))),
            kv("evidence", c(f"{execution.get('evidence_count', 0)} sources", "fg")),
            kv("confidence", conf_str),
            kv("approval", approval),
            kv("trace", c(str(execution.get("trace_id")), "dim")),
        ])

    def _print_execution(self, execution: dict) -> None:
        print(self._governance_panel(execution))
        facts = execution.get("facts", [])
        if facts:
            print(c("  Computed facts", "sea", bold=True) + c("  (deterministic)", "dim"))
            for fact in facts[:12]:
                print(bullet(fact.get("detail") or f"{fact.get('name')} = {fact.get('value')}"))
        if execution.get("interpretation"):
            print(c("  Interpretation", "sea", bold=True) + c("  (model, evidence-grounded)", "dim"))
            text = execution["interpretation"][:700]
            for line in text.splitlines()[:12]:
                print("  " + line)
        if execution.get("recommendation"):
            print(c("  Recommendation", "sea", bold=True))
            print("  " + execution["recommendation"][:300])
        print(c(
            f"  execution={execution.get('id')} · {execution.get('latency_ms')}ms · "
            f"${execution.get('cost_usd')}", "dim",
        ))

    def _print_evidence(self, execution: dict) -> None:
        evidence = execution.get("evidence", [])
        print(c(f"  Evidence ({len(evidence)})", "sea", bold=True))
        rows = []
        for i, ev in enumerate(evidence):
            rows.append([
                c(f"[{i}]", "green"), ev.get("kind", "?"),
                str(ev.get("source"))[:34],
                c(str(ev.get("trust")), "dim"),
                c((ev.get("excerpt") or "")[:56], "dim"),
            ])
        print(table(["", "kind", "source", "trust", "excerpt"], rows))
        claims = execution.get("claims", [])
        print(c(f"  Claims ({len(claims)})", "sea", bold=True))
        category_colors = {"computation": "green", "fact": "green",
                           "interpretation": "yellow", "recommendation": "sea"}
        for claim in claims:
            cat = claim.get("category", "?")
            conf = c(f"conf={claim.get('confidence')}", "dim")
            print(bullet(
                f"{badge(cat, category_colors.get(cat, 'dim'))} {conf} "
                f"{str(claim.get('claim'))[:100]}",
                mark="",
            ))

    def handle_workspace(self, args: list[str]) -> None:
        if not args or args[0] == "list":
            data = self._web_call("GET", "/v1/workspaces")
            if data:
                rows = []
                for ws in data.get("workspaces", []):
                    marker = c("●", "green") if ws["id"] == self.workspace else c("·", "dim")
                    rows.append([
                        marker, ws["id"], ws["name"], ws["domain"],
                        c("synthetic demo", "dim") if ws.get("synthetic") else "",
                    ])
                print(table(["", "id", "name", "domain", ""], rows))
        elif args[0] == "use" and len(args) > 1:
            data = self._web_call("GET", f"/v1/workspaces/{args[1]}")
            if data:
                self.workspace = args[1]
                print(success(
                    f"Workspace {c(data['name'], 'fg', bold=True)} "
                    f"{c('[' + data['domain'] + ']', 'dim')}"
                ))
                print(kv("workflows", c(", ".join(w["id"] for w in data["workflows"]), "fg")))
        elif args[0] == "status":
            if not self.workspace:
                print(error("No active workspace — /workspace use <id> first."))
                return
            data = self._web_call("GET", f"/v1/workspaces/{self.workspace}/overview")
            if data:
                print(panel(f"{self.workspace.upper()} · COMMAND CENTER", [
                    kv("sources", str(data["sources"])),
                    kv("approvals", f"{data['pending_approvals']} pending"),
                    kv("reviews", f"{data['open_reviews']} open"),
                    kv("blocked", f"{data['blocked_workflow_traces']} workflow traces"),
                    kv("health", c(data["health"], "green")),
                ]))
                executions = data.get("recent_executions", [])[:5]
                if executions:
                    print(table(
                        ["when", "workflow", "status", "risk"],
                        [[e["created_at"][:19], e["workflow_id"], e["status"],
                          risk_badge(e["risk"])] for e in executions],
                    ))
        else:
            print(error("Usage: /workspace list|use <id>|status"))

    def run_workflow(self, workflow_id: str, input_data: dict) -> None:
        if not self.workspace:
            print(error("No active workspace — /workspace use <id> first."))
            return
        data = self._web_call(
            "POST", "/v1/workflows/run",
            {"workspace_id": self.workspace, "workflow_id": workflow_id,
             "input": input_data},
        )
        if data:
            self._print_execution(data)

    def handle_workflow(self, args: list[str]) -> None:
        if not args or args[0] == "list":
            if not self.workspace:
                print(error("No active workspace — /workspace use <id> first."))
                return
            data = self._web_call("GET", f"/v1/workspaces/{self.workspace}/workflows")
            if data:
                rows = []
                for w in data.get("workflows", []):
                    required = ",".join(w.get("input_schema", {}).get("required", []))
                    rows.append([
                        c(w["id"], "green"), w["name"],
                        c(f"input: {required}" if required else "", "dim"),
                    ])
                print(table(["workflow", "name", ""], rows))
        elif args[0] == "run" and len(args) > 1:
            input_data: dict = {}
            for pair in args[2:]:
                if "=" in pair:
                    key, _, value = pair.partition("=")
                    input_data[key] = int(value) if value.isdigit() else value
            self.run_workflow(args[1], input_data)
        elif args[0] == "history":
            path = "/v1/workflows/executions"
            if self.workspace:
                path += f"?workspace_id={self.workspace}"
            data = self._web_call("GET", path)
            if data:
                print(table(
                    ["id", "when", "workflow", "status", "risk"],
                    [[e["id"][:8], e["created_at"][:19],
                      f"{e['workspace_id']}/{e['workflow_id']}", e["status"],
                      risk_badge(e["risk"])] for e in data.get("executions", [])],
                ))
        else:
            print(error("Usage: /workflow list|run <id> [k=v ...]|history"))

    def handle_evolve(self, args: list[str]) -> None:
        if not args:  # run an analysis
            data = self._web_call("POST", "/v1/evolution/analyze", {})
            if data is not None:
                created = data.get("created", [])
                if not created:
                    print(c("  No new proposals — recent traffic shows no recurring failures.", "dim"))
                for p in created:
                    print(bullet(
                        f"{c(p['id'][:8], 'green')} {badge(p['kind'], 'sea')} "
                        f"{c(p['title'], 'fg')}"))
                    occurrences = p["evidence"]["occurrences"]
                    print("      " + c(f"evidence: {occurrences} traces", "dim"))
        elif args[0] == "list":
            data = self._web_call("GET", "/v1/evolution/proposals")
            if data:
                print(table(
                    ["id", "status", "kind", "title"],
                    [[p["id"][:8], p["status"], p["kind"], p["title"][:60]]
                     for p in data.get("proposals", [])],
                ))
        elif args[0] == "apply" and len(args) > 1:
            data = self._web_call(
                "POST", f"/v1/evolution/proposals/{args[1]}/approve",
                {"decided_by": os.environ.get("USER", "tui")},
            )
            if data:
                print(success(f"{data['id'][:8]} → {data['status']} (v{data['version']})"))
        else:
            print(error("Usage: /evolve [list|apply <id>]"))

    # -- inference --------------------------------------------------------

    def send(self, prompt: str) -> None:
        url = completion_endpoint(self.profile)
        headers = request_headers(self.profile)

        if self.governed:
            payload = build_governed_payload(prompt, self.model)
        else:
            if not self.model:
                print(error("No model selected — use /model <name> or /refresh."))
                return
            payload = build_direct_payload(prompt, self.model, self.history)

        try:
            with Spinner("thinking"):
                response = httpx.post(
                    url, json=payload, headers=headers, timeout=self.profile.timeout_s
                )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            print(error(f"HTTP {exc.response.status_code}: {exc.response.text[:400]}"))
            return
        except httpx.HTTPError as exc:
            print(error(f"Request failed: {exc}"))
            return

        if self.governed:
            result = extract_governed_output(data)
            print(result["output"])
            meta = (
                f"trace={result['trace_id']} · "
                f"{result['model'].get('provider')}:{result['model'].get('model_id')} · "
                f"${result['cost_usd']} · {result['latency_ms']}ms"
            )
            if result["citations"]:
                meta += f" · {len(result['citations'])} citations"
            print(c("  " + meta, "dim"))
        else:
            output = extract_direct_output(data)
            print(output)
            self.history.append({"role": "user", "content": prompt})
            self.history.append({"role": "assistant", "content": output})

    # -- main loop --------------------------------------------------------

    def run(self) -> None:
        mode = "GOVERNED" if self.governed else "DIRECT"
        print(ui.banner(
            "Governed AI Command Center",
            f"gateway {self.profile.name} · {mode} · /help for commands · {KEYS_HINT}",
        ))

        while True:
            try:
                line = input(self.prompt_str()).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n" + c("  Bye.", "dim"))
                return

            if not line:
                continue
            if line.startswith("/"):
                if not self.handle_command(line):
                    return
                continue
            self.send(line)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="helios.tui", description="Helios terminal UI")
    parser.add_argument(
        "--gateway",
        default=os.environ.get("HELIOS_TUI_GATEWAY", "helios"),
        help="Gateway profile to connect to (default: helios, the governed path)",
    )
    args = parser.parse_args(argv)

    try:
        app = HeliosTUI(gateway=args.gateway)
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        raise SystemExit(1) from exc
    app.run()
