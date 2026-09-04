"""
Git tools — typed subcommands only (no arbitrary `git <anything>`),
executed in the workspace root with timeouts and a secret-stripped env.
"""

from __future__ import annotations

import os
import subprocess

from helios.broker.manifest import ToolManifest
from helios.config import settings
from helios.tools.filesystem import workspace_root
from helios.tools.shell import _clean_env


def _git(argv: list[str]) -> dict:
    cwd = workspace_root()
    os.makedirs(cwd, exist_ok=True)
    try:
        proc = subprocess.run(
            ["git", *argv],
            cwd=cwd,
            env=_clean_env(),
            capture_output=True,
            text=True,
            timeout=settings.shell_timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"argv": argv, "timed_out": True, "exit_code": None,
                "stdout": "", "stderr": ""}
    cap = settings.tool_output_max_bytes // 2
    return {
        "argv": argv,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[:cap],
        "stderr": proc.stderr[:cap],
        "timed_out": False,
    }


def _status(args: dict, context) -> dict:
    return _git(["status", "--porcelain=v1", "--branch"])


def _diff(args: dict, context) -> dict:
    argv = ["diff"]
    if args.get("staged"):
        argv.append("--cached")
    if args.get("path"):
        argv.extend(["--", str(args["path"])])
    return _git(argv)


def _log(args: dict, context) -> dict:
    limit = int(args.get("limit") or 10)
    return _git(["log", f"-{min(limit, 50)}", "--oneline"])


def _branch(args: dict, context) -> dict:
    return _git(["checkout", "-b", str(args["name"])])


def _commit(args: dict, context) -> dict:
    if args.get("add_all"):
        added = _git(["add", "-A"])
        if added["exit_code"] not in (0,):
            return added
    return _git(["commit", "-m", str(args["message"])])


_OBJ = {"type": "object", "additionalProperties": False}


def install(registry) -> None:
    registry.register(
        ToolManifest(
            name="git.status", description="Show git working tree status",
            capability="read", risk_class="low", scopes=["git.read"],
            input_schema={**_OBJ, "properties": {}}, idempotent=True,
        ), _status)
    registry.register(
        ToolManifest(
            name="git.diff", description="Show git diff (optionally staged / one path)",
            capability="read", risk_class="low", scopes=["git.read"],
            input_schema={**_OBJ, "properties": {
                "staged": {"type": "boolean"}, "path": {"type": "string"}}},
            idempotent=True,
        ), _diff)
    registry.register(
        ToolManifest(
            name="git.log", description="Show recent commits",
            capability="read", risk_class="low", scopes=["git.read"],
            input_schema={**_OBJ, "properties": {"limit": {"type": "integer"}}},
            idempotent=True,
        ), _log)
    registry.register(
        ToolManifest(
            name="git.branch", description="Create and switch to a new branch",
            capability="write", risk_class="low", scopes=["git.write"],
            input_schema={**_OBJ, "properties": {"name": {"type": "string"}},
                          "required": ["name"]},
            resource_fields={"name": "git.branch"},
        ), _branch)
    registry.register(
        ToolManifest(
            name="git.commit", description="Commit staged (or all) changes",
            capability="write", risk_class="low", scopes=["git.write"],
            input_schema={**_OBJ, "properties": {
                "message": {"type": "string"}, "add_all": {"type": "boolean"}},
                "required": ["message"]},
        ), _commit)
