"""
Safe shell tool.

- No `shell=True`: commands are tokenized, no pipes/redirection/expansion.
- Runs inside the workspace root with a hard timeout.
- The subprocess environment is stripped of anything secret-looking so a
  prompt-injected command like `env` cannot exfiltrate provider keys.
- Dangerous patterns raise the contextual risk score (see broker/risk.py),
  which routes the call into approval instead of silent execution.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess

from helios.broker.manifest import ToolManifest
from helios.config import settings
from helios.tools.filesystem import workspace_root

_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)


def _clean_env() -> dict:
    return {
        k: v
        for k, v in os.environ.items()
        if not _SECRET_ENV.search(k)
    }


def _run(args: dict, context) -> dict:
    command = str(args["command"])
    cwd = workspace_root()
    os.makedirs(cwd, exist_ok=True)
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"unparseable command: {exc}")
    if not tokens:
        raise ValueError("empty command")

    timeout = min(float(args.get("timeout_s") or settings.shell_timeout_s),
                  settings.shell_timeout_s)
    try:
        proc = subprocess.run(
            tokens,
            cwd=cwd,
            env=_clean_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"command": command, "timed_out": True, "timeout_s": timeout,
                "exit_code": None, "stdout": "", "stderr": ""}
    except FileNotFoundError:
        return {"command": command, "exit_code": 127, "stdout": "",
                "stderr": f"command not found: {tokens[0]}", "timed_out": False}

    cap = settings.tool_output_max_bytes // 2
    return {
        "command": command,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[:cap],
        "stderr": proc.stderr[:cap],
        "timed_out": False,
    }


def _resource(args: dict) -> dict:
    return {"shell.cwd": workspace_root(),
            "shell.command": str(args.get("command", ""))}


def install(registry) -> None:
    registry.register(
        ToolManifest(
            name="shell.run",
            description="Run a command in the agent workspace (no shell "
                        "expansion, secret-stripped env, hard timeout)",
            capability="execute",
            risk_class="low",
            scopes=["shell.execute"],
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_s": {"type": "number"},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        ),
        _run,
        _resource,
    )
