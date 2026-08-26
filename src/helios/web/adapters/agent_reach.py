"""
Agent-Reach — optional read-only MCP connector.

Not forked into Helios core: the adapter talks to an externally-run
Agent-Reach MCP endpoint identified by `HELIOS_AGENT_REACH_MCP_URL`
(token via `HELIOS_AGENT_REACH_TOKEN`).  When unconfigured, health reports
`unconfigured` and requests raise AdapterUnavailable — the broker records
that honestly instead of pretending the source was searched.

All returned resources are untrusted content and flow through the same
sanitizer as every other adapter.  Tool *descriptions* from the MCP server
are never forwarded to the model.
"""

from __future__ import annotations

import os

from helios.web.adapters.base import BaseSourceAdapter
from helios.web.types import (
    AdapterUnavailable,
    HealthStatus,
    SourceCapabilities,
    SourceDocument,
    WebAccessRequest,
)

ENDPOINT_ENV = "HELIOS_AGENT_REACH_MCP_URL"
TOKEN_ENV = "HELIOS_AGENT_REACH_TOKEN"


class AgentReachAdapter(BaseSourceAdapter):
    name = "agent-reach"
    version = "0.1.0"
    trust_level = "optional-mcp"  # community connector, not builtin trust
    capabilities = SourceCapabilities(search=True, read=True)

    # Only these MCP tools are exposed; everything else is filtered.
    TOOL_ALLOWLIST = {"search", "read"}

    @property
    def endpoint(self) -> str | None:
        return os.environ.get(ENDPOINT_ENV) or None

    def health(self) -> HealthStatus:
        if not self.endpoint:
            return HealthStatus(
                adapter=self.name,
                status="unconfigured",
                detail=f"set {ENDPOINT_ENV} to enable the Agent-Reach MCP connector",
            )
        return HealthStatus(adapter=self.name, status="healthy", detail=self.endpoint)

    def _call(self, tool: str, arguments: dict) -> dict:
        if not self.endpoint:
            raise AdapterUnavailable(
                f"agent-reach is not configured ({ENDPOINT_ENV} unset)"
            )
        if tool not in self.TOOL_ALLOWLIST:
            raise AdapterUnavailable(f"MCP tool '{tool}' is not on the allowlist")
        headers = {}
        token = os.environ.get(TOKEN_ENV)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self.client.post(
            self.endpoint.rstrip("/") + "/tools/call",
            json={"name": tool, "arguments": arguments},
            headers=headers,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return response.json()

    def _to_docs(self, payload: dict, operation: str) -> list[SourceDocument]:
        docs = []
        for item in payload.get("results", payload.get("content", [])) or []:
            if not isinstance(item, dict):
                continue
            docs.append(
                SourceDocument(
                    source=self.name,
                    operation=operation,
                    url=item.get("url"),
                    title=item.get("title"),
                    author=item.get("author"),
                    published_at=item.get("published_at"),
                    content=str(item.get("text") or item.get("content") or "")[:8000],
                    content_type=item.get("type") or "page",
                    source_adapter=self.name,
                    adapter_version=self.version,
                    citations=[{"url": item.get("url")}] if item.get("url") else [],
                    warnings=["origin=agent-reach-mcp"],
                )
            )
        return docs

    def search(self, request: WebAccessRequest) -> list[SourceDocument]:
        payload = self._call("search", {"query": request.query, "limit": request.max_results})
        return self._to_docs(payload, "search")[: request.max_results]

    def read(self, request: WebAccessRequest) -> SourceDocument:
        payload = self._call("read", {"url": request.url})
        docs = self._to_docs(payload, "read")
        if not docs:
            raise AdapterUnavailable("agent-reach returned no readable content")
        return docs[0]
