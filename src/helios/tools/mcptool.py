"""
MCP tool — brokered access to registered MCP servers.

Reuses the existing MCP governance (trust gating, tool allowlists,
budgets, sanitized results). The Tool Broker adds the permission / risk /
policy / approval layers on top; the MCP broker keeps its own hard limits.
"""

from __future__ import annotations

from helios.broker.manifest import ToolManifest
from helios.db import SessionLocal
from helios.models import McpServer
from helios.web.mcp import McpBroker


def _resource(args: dict) -> dict:
    return {"mcp.server": str(args.get("server_id", "")),
            "mcp.tool": str(args.get("tool", ""))}


def _call(args: dict, context) -> dict:
    db = SessionLocal()
    try:
        server = (
            db.query(McpServer)
            .filter(McpServer.id == args["server_id"],
                    McpServer.tenant_id == context.tenant_id)
            .first()
        )
        if server is None:
            raise ValueError("unknown MCP server for this tenant")
        broker = McpBroker(server)
        document = broker.call(args["tool"], dict(args.get("arguments") or {}))
        return {"server": server.name, "tool": args["tool"],
                "content": document.content, "warnings": document.warnings,
                "trust": document.trust}
    finally:
        db.close()


def install(registry) -> None:
    registry.register(
        ToolManifest(
            name="mcp.call",
            description="Call a tool on a trusted, allowlisted MCP server",
            capability="network",
            risk_class="medium",
            scopes=["mcp.call"],
            input_schema={
                "type": "object",
                "properties": {
                    "server_id": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["server_id", "tool"],
                "additionalProperties": False,
            },
            network=["registered MCP endpoints"],
            provenance="mcp",
        ),
        _call,
        _resource,
    )
