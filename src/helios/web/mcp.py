"""
MCP broker (Phase W2) — governed access to Model Context Protocol servers.

Arbitrary MCP servers never run inside the API process; the broker talks to
externally-running servers over HTTP and wraps every call in the controls
the architecture doc mandates:

* trust lifecycle    — servers register as `untrusted` and are unusable
                       until explicitly approved; approval can be revoked.
* tool filtering     — only allowlisted tools are callable or even shown.
* schema validation  — arguments must be a flat-serializable dict within
                       size limits, validated before dispatch.
* per-call budgets   — max calls per dispatch, max response bytes, timeout.
* version pinning    — the version recorded at registration is pinned; a
                       different version at health time marks the server
                       degraded (`version_drift`) instead of silently
                       changing tool behavior.
* result sanitation  — every response flows through the same sanitizer as
                       web content; MCP tool descriptions are untrusted and
                       are never forwarded to the model.
"""

from __future__ import annotations

import json
from typing import Any

from helios.models import McpServer
from helios.web.sanitize import sanitize_document
from helios.web.types import SourceDocument

MAX_ARG_BYTES = 16_384
DEFAULT_BUDGETS = {"max_calls": 10, "max_bytes": 262_144, "timeout_s": 20.0}


class McpCallDenied(Exception):
    """The broker refused an MCP call (trust, allowlist, budget, schema)."""


class McpBudget:
    """Per-dispatch budget tracker."""

    def __init__(self, budgets: dict | None = None) -> None:
        merged = {**DEFAULT_BUDGETS, **(budgets or {})}
        self.max_calls = int(merged["max_calls"])
        self.max_bytes = int(merged["max_bytes"])
        self.timeout_s = float(merged["timeout_s"])
        self.calls = 0
        self.bytes = 0

    def charge_call(self) -> None:
        self.calls += 1
        if self.calls > self.max_calls:
            raise McpCallDenied(
                f"budget exceeded: {self.calls} calls > max_calls={self.max_calls}"
            )

    def charge_bytes(self, n: int) -> None:
        self.bytes += n
        if self.bytes > self.max_bytes:
            raise McpCallDenied(
                f"budget exceeded: {self.bytes} bytes > max_bytes={self.max_bytes}"
            )


def validate_arguments(arguments: Any) -> dict:
    """Arguments must be a JSON-serializable dict within the size limit."""
    if not isinstance(arguments, dict):
        raise McpCallDenied("MCP arguments must be an object")
    try:
        encoded = json.dumps(arguments)
    except (TypeError, ValueError) as exc:
        raise McpCallDenied(f"MCP arguments are not JSON-serializable: {exc}") from exc
    if len(encoded.encode()) > MAX_ARG_BYTES:
        raise McpCallDenied(f"MCP arguments exceed {MAX_ARG_BYTES} bytes")
    return arguments


class McpBroker:
    """Executes calls against ONE registered server under full policy."""

    def __init__(self, server: McpServer, client: Any = None) -> None:
        self.server = server
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=McpBudget(self.server.budgets).timeout_s)
        return self._client

    # -- controls ---------------------------------------------------------

    def _require_trust(self) -> None:
        if self.server.trust_status != "approved":
            raise McpCallDenied(
                f"MCP server '{self.server.name}' is {self.server.trust_status}; "
                "only approved servers may be called"
            )

    def _require_tool(self, tool: str) -> None:
        if tool not in (self.server.tool_allowlist or []):
            raise McpCallDenied(
                f"MCP tool '{tool}' is not on the allowlist for "
                f"server '{self.server.name}'"
            )

    def _headers(self) -> dict[str, str]:
        import os

        headers = {"Content-Type": "application/json"}
        if self.server.token_env:
            token = os.environ.get(self.server.token_env)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    # -- operations -------------------------------------------------------

    def health(self) -> dict:
        """Reachability + version-drift check against the pinned version."""
        try:
            response = self.client.get(
                self.server.endpoint.rstrip("/") + "/health",
                headers=self._headers(),
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            data = response.json() if hasattr(response, "json") else {}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unavailable", "detail": str(exc)[:200]}

        reported = str(data.get("version") or "")
        if self.server.pinned_version and reported and reported != self.server.pinned_version:
            return {
                "status": "degraded",
                "detail": (
                    f"version_drift: pinned={self.server.pinned_version} "
                    f"reported={reported}"
                ),
            }
        return {"status": "healthy", "version": reported or self.server.pinned_version}

    def call(
        self,
        tool: str,
        arguments: dict,
        budget: McpBudget | None = None,
    ) -> SourceDocument:
        """One governed tool call -> one sanitized, untrusted SourceDocument."""
        self._require_trust()
        self._require_tool(tool)
        arguments = validate_arguments(arguments)

        budget = budget or McpBudget(self.server.budgets)
        budget.charge_call()

        response = self.client.post(
            self.server.endpoint.rstrip("/") + "/tools/call",
            json={"name": tool, "arguments": arguments},
            headers=self._headers(),
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()

        raw = response.text if hasattr(response, "text") else json.dumps(response.json())
        budget.charge_bytes(len(raw.encode()))
        data = response.json()

        content = data.get("content")
        if isinstance(content, list):  # MCP content blocks
            content = "\n".join(
                str(block.get("text", "")) for block in content if isinstance(block, dict)
            )
        doc = SourceDocument(
            source=self.server.name,
            operation=f"mcp:{tool}",
            content=str(content or "")[:16_000],
            content_type="mcp_result",
            source_adapter=f"mcp:{self.server.name}",
            adapter_version=self.server.pinned_version or "unpinned",
            warnings=[f"origin=mcp_server:{self.server.endpoint}"],
        )
        return sanitize_document(doc)
