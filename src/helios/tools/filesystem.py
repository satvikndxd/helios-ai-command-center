"""
Filesystem tools — jailed to the configured workspace root.

Defense in depth: the permission layer constrains `filesystem.path` with a
prefix rule on the *normalized* path, and the executor independently
refuses to touch anything outside the workspace even if a grant is
misconfigured.
"""

from __future__ import annotations

import os

from helios.broker.manifest import ToolManifest
from helios.config import settings


def workspace_root() -> str:
    return os.path.abspath(settings.workspace_root)


def resolve_path(path: str) -> str:
    """Join with the workspace root and normalize (collapses `../`)."""
    root = workspace_root()
    if os.path.isabs(path):
        candidate = os.path.normpath(path)
    else:
        candidate = os.path.normpath(os.path.join(root, path))
    return candidate


def _require_in_workspace(resolved: str) -> None:
    root = workspace_root()
    real = os.path.realpath(resolved)
    real_root = os.path.realpath(root)
    if not (real == real_root or real.startswith(real_root + os.sep)):
        raise PermissionError(f"path escapes workspace root: {resolved}")


def _resource(args: dict) -> dict:
    return {"filesystem.path": resolve_path(str(args.get("path", "")))}


def _read(args: dict, context) -> dict:
    resolved = resolve_path(args["path"])
    _require_in_workspace(resolved)
    with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read(settings.tool_output_max_bytes + 1)
    truncated = len(content) > settings.tool_output_max_bytes
    return {
        "path": resolved,
        "content": content[: settings.tool_output_max_bytes],
        "truncated": truncated,
    }


def _write(args: dict, context) -> dict:
    resolved = resolve_path(args["path"])
    _require_in_workspace(resolved)
    os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
    content = str(args["content"])
    with open(resolved, "w", encoding="utf-8") as fh:
        fh.write(content)
    return {"path": resolved, "bytes_written": len(content.encode())}


def _list(args: dict, context) -> dict:
    resolved = resolve_path(args.get("path", "."))
    _require_in_workspace(resolved)
    entries = []
    for name in sorted(os.listdir(resolved)):
        full = os.path.join(resolved, name)
        entries.append({"name": name, "dir": os.path.isdir(full),
                        "size": os.path.getsize(full) if os.path.isfile(full) else None})
    return {"path": resolved, "entries": entries[:500], "count": len(entries)}


def install(registry) -> None:
    registry.register(
        ToolManifest(
            name="fs.read",
            description="Read a file inside the agent workspace",
            capability="read",
            risk_class="low",
            scopes=["filesystem.read"],
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            idempotent=True,
        ),
        _read,
        _resource,
    )
    registry.register(
        ToolManifest(
            name="fs.write",
            description="Write a file inside the agent workspace",
            capability="write",
            risk_class="low",
            scopes=["filesystem.write"],
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            idempotent=True,
        ),
        _write,
        _resource,
    )
    registry.register(
        ToolManifest(
            name="fs.list",
            description="List a directory inside the agent workspace",
            capability="read",
            risk_class="low",
            scopes=["filesystem.read"],
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            idempotent=True,
        ),
        _list,
        _resource,
    )
