"""
MCP server routes (Phase W2): registration, trust lifecycle, health, calls.

Servers register as `untrusted` and cannot be called until a human approves
them.  Every call is tool-filtered, budgeted, and sanitized by the broker.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.models import ApiKey, McpServer
from helios.security import get_api_key
from helios.web.mcp import McpBroker, McpCallDenied

router = APIRouter(tags=["mcp"])


class McpServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(min_length=1, max_length=500)
    transport: str = "http"
    pinned_version: str | None = None
    tool_allowlist: list[str] = Field(default_factory=list)
    budgets: dict = Field(default_factory=dict)
    token_env: str | None = Field(
        default=None, description="NAME of the env var holding the token — never the token"
    )


class McpCallIn(BaseModel):
    server_id: str
    tool: str
    arguments: dict = Field(default_factory=dict)


def _serialize(server: McpServer) -> dict:
    return {
        "id": server.id,
        "name": server.name,
        "endpoint": server.endpoint,
        "transport": server.transport,
        "pinned_version": server.pinned_version,
        "trust_status": server.trust_status,
        "tool_allowlist": server.tool_allowlist,
        "budgets": server.budgets,
        "token_env": server.token_env,
    }


def _get_server(db: Session, api_key: ApiKey, server_id: str) -> McpServer:
    server = (
        db.query(McpServer)
        .filter(McpServer.id == server_id, McpServer.tenant_id == api_key.tenant_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return server


@router.post("/v1/mcp/servers", status_code=201)
async def register_server(
    payload: McpServerIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Register an MCP server. It starts UNTRUSTED and cannot be called yet."""
    if payload.token_env and payload.token_env.lower().startswith(("bearer ", "sk-")):
        raise HTTPException(
            status_code=422,
            detail="token_env must be an environment-variable NAME, never a credential",
        )
    server = McpServer(
        tenant_id=api_key.tenant_id,
        name=payload.name,
        endpoint=payload.endpoint,
        transport=payload.transport,
        pinned_version=payload.pinned_version,
        tool_allowlist=payload.tool_allowlist,
        budgets=payload.budgets,
        token_env=payload.token_env,
        trust_status="untrusted",
    )
    db.add(server)
    db.commit()
    return _serialize(server)


@router.get("/v1/mcp/servers")
async def list_servers(
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    servers = (
        db.query(McpServer).filter(McpServer.tenant_id == api_key.tenant_id).all()
    )
    return {"servers": [_serialize(s) for s in servers]}


@router.post("/v1/mcp/servers/{server_id}/trust")
async def set_trust(
    server_id: str,
    decision: dict,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Approve or revoke a server (human trust decision)."""
    status = decision.get("trust_status")
    if status not in ("approved", "revoked"):
        raise HTTPException(status_code=422, detail="trust_status must be approved|revoked")
    server = _get_server(db, api_key, server_id)
    server.trust_status = status
    db.commit()
    return _serialize(server)


@router.get("/v1/mcp/servers/{server_id}/health")
async def server_health(
    server_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    server = _get_server(db, api_key, server_id)
    return McpBroker(server).health()


@router.post("/v1/mcp/call")
async def call_tool(
    payload: McpCallIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Governed MCP tool call: trust + allowlist + budgets + sanitization."""
    server = _get_server(db, api_key, payload.server_id)
    broker = McpBroker(server)
    try:
        document = broker.call(payload.tool, payload.arguments)
    except McpCallDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"document": document.model_dump(mode="json")}
