"""
The interactive loop for `python -m helios.tui`.

Design constraints:

* Zero extra dependencies — ANSI + readline from the standard library, httpx
  (already a Helios dependency) for transport.
* Works over SSH and in narrow/limited-color terminals.
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

try:  # pragma: no cover - readline is absent on some platforms
    import readline  # noqa: F401  (enables history + Ctrl+L clear-screen)
except ImportError:  # pragma: no cover
    pass

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
CYAN = "\x1b[36m"
YELLOW = "\x1b[33m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"

HELP = """\
Commands:
  /help              Show this help
  /gateway [name]    Show or switch the active gateway
  /connect <name>    Alias for /gateway <name>
  /model [name]      Show or set the model for the active gateway
  /models            List models cached from the last /refresh
  /refresh           Discover models via GET /models on the active gateway
  /status            Show gateway, mode, model, and endpoint
  /clear             Clear the conversation history
  /quit              Exit

Keys: Ctrl+K focus prompt, Ctrl+L clear screen, Ctrl+C exit.
"""


class HeliosTUI:
    def __init__(self, gateway: str = "helios") -> None:
        self.profile: GatewayProfile = get_gateway(gateway)
        self.model: str | None = self.profile.default_model
        self.history: list[dict[str, str]] = []
        self.models: list[str] = []

    # -- presentation -----------------------------------------------------

    def mode_badge(self) -> str:
        if self.profile.mode == "helios":
            return f"{GREEN}{BOLD}GOVERNED{RESET}"
        return f"{YELLOW}{BOLD}DIRECT{RESET}"

    def prompt_str(self) -> str:
        model = self.model or "auto"
        return f"{CYAN}{self.profile.name}{RESET}{DIM}:{model}{RESET} ❯ "

    def print_status(self) -> None:
        print(f"  gateway : {self.profile.name} [{self.mode_badge()}]")
        print(f"  endpoint: {completion_endpoint(self.profile)}")
        print(f"  model   : {self.model or 'auto (router decides)'}")
        key_env = self.profile.api_key_env or "-"
        key_set = "set" if self.profile.resolve_api_key() else "not set"
        print(f"  key env : {key_env} ({key_set})")

    # -- commands ---------------------------------------------------------

    def handle_command(self, line: str) -> bool:
        """Returns False when the loop should exit."""
        parts = line.split()
        command, args = parts[0], parts[1:]

        if command in ("/quit", "/exit"):
            return False
        if command == "/help":
            print(HELP)
        elif command in ("/gateway", "/connect"):
            if not args:
                names = sorted(all_gateways())
                print(f"Active: {self.profile.name} [{self.mode_badge()}]")
                print("Available: " + ", ".join(names))
            else:
                try:
                    self.profile = get_gateway(args[0])
                    self.model = self.profile.default_model
                    self.models = []
                    print(f"Switched to {self.profile.name} [{self.mode_badge()}]")
                except KeyError as exc:
                    print(f"{RED}{exc.args[0]}{RESET}")
        elif command == "/model":
            if args:
                self.model = args[0]
                print(f"Model set to {self.model}")
            else:
                print(f"Model: {self.model or 'auto (router decides)'}")
        elif command == "/models":
            if not self.models:
                print("No cached models — run /refresh first.")
            for model_id in self.models:
                print(f"  {model_id}")
        elif command == "/refresh":
            try:
                self.models = discover_models(self.profile)
                print(f"Discovered {len(self.models)} models from {self.profile.base_url}")
            except Exception as exc:  # noqa: BLE001 - show the user, keep looping
                print(f"{RED}Model discovery failed: {exc}{RESET}")
        elif command == "/status":
            self.print_status()
        elif command == "/clear":
            self.history = []
            print("Conversation cleared.")
        else:
            print(f"Unknown command {command} — try /help")
        return True

    # -- inference --------------------------------------------------------

    def send(self, prompt: str) -> None:
        url = completion_endpoint(self.profile)
        headers = request_headers(self.profile)

        if self.profile.mode == "helios":
            payload = build_governed_payload(prompt, self.model)
        else:
            if not self.model:
                print(f"{RED}No model selected — use /model <name> or /refresh.{RESET}")
                return
            payload = build_direct_payload(prompt, self.model, self.history)

        try:
            response = httpx.post(
                url, json=payload, headers=headers, timeout=self.profile.timeout_s
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            print(f"{RED}HTTP {exc.response.status_code}: {exc.response.text[:500]}{RESET}")
            return
        except httpx.HTTPError as exc:
            print(f"{RED}Request failed: {exc}{RESET}")
            return

        if self.profile.mode == "helios":
            result = extract_governed_output(data)
            print(result["output"])
            meta = (
                f"trace={result['trace_id']} model={result['model']} "
                f"cost=${result['cost_usd']} latency={result['latency_ms']}ms"
            )
            if result["citations"]:
                meta += f" citations={len(result['citations'])}"
            print(f"{DIM}{meta}{RESET}")
        else:
            output = extract_direct_output(data)
            print(output)
            self.history.append({"role": "user", "content": prompt})
            self.history.append({"role": "assistant", "content": output})

    # -- main loop --------------------------------------------------------

    def run(self) -> None:
        print(f"{BOLD}Helios{RESET} — terminal agent interface")
        print(f"Gateway {CYAN}{self.profile.name}{RESET} [{self.mode_badge()}] — /help for commands\n")

        while True:
            try:
                line = input(self.prompt_str()).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
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
