"""
Agent runtime API: sessions, runs, events, approvals, tools, replay.

    POST /v1/agent/sessions                create a session (grants, model, env)
    GET  /v1/agent/sessions                list sessions
    GET  /v1/agent/sessions/{id}           inspect (messages, grants, approvals)
    POST /v1/agent/sessions/{id}/fork      fork with full history
    POST /v1/agent/sessions/{id}/messages  send a message -> run
    GET  /v1/agent/runs/{id}               run state
    GET  /v1/agent/runs/{id}/events        hierarchical trace (poll w/ after_seq)
    POST /v1/agent/runs/{id}/cancel        request cancellation
    POST /v1/agent/runs/{id}/resume        continue after an approval decision
    POST /v1/agent/runs/{id}/retry         re-run the input as a fresh run
    POST /v1/agent/runs/{id}/replay        re-evaluate against another policy
    POST /v1/agent/approvals/{id}/decide   approve | deny | approve_session (+ edits)
    GET  /v1/tools                          tool manifests
    POST /v1/tools/invoke                   direct governed invocation
    GET  /v1/policies                       registered policy versions
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.agent.replay import replay_run
from helios.agent.runtime import AgentRuntime
from helios.broker.core import ToolBroker
from helios.broker.permissions import PermissionSet, developer_grants
from helios.broker.policy import POLICIES
from helios.broker.registry import default_registry
from helios.broker.trace import TraceRecorder, args_preview, event_to_dict
from helios.broker.types import InvocationContext
from helios.db import get_db
from helios.models import AgentRun, AgentSession, ApiKey, ApprovalRequest, TraceEvent
from helios.security import get_api_key
from helios.tools.filesystem import workspace_root
from helios.web.actions import hash_args

router = APIRouter(prefix="/v1", tags=["agent"])


def _runtime() -> AgentRuntime:
    return AgentRuntime(ToolBroker(default_registry()))


# --- schemas ---------------------------------------------------------------


class SessionIn(BaseModel):
    name: str = "session"
    environment: str = Field(default="dev", pattern="^(dev|staging|production)$")
    autonomy: str = Field(default="supervised", pattern="^(supervised|autonomous)$")
    model_provider: str | None = None
    model_id: str | None = None
    github_repo: str | None = None
    grants: list[dict] | None = None
    user_id: str | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1)


class DecideIn(BaseModel):
    decision: str = Field(pattern="^(approved|denied|approve_session)$")
    decided_by: str = "operator"
    edited_args: dict | None = None


class ReplayIn(BaseModel):
    policy_version: str | None = None
    policy: dict | None = None


class InvokeIn(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)
    session_id: str | None = None
    environment: str = Field(default="dev", pattern="^(dev|staging|production)$")
    idempotency_key: str | None = None


# --- helpers ---------------------------------------------------------------


def _session_or_404(db: Session, api_key: ApiKey, session_id: str) -> AgentSession:
    session = (
        db.query(AgentSession)
        .filter(AgentSession.id == session_id,
                AgentSession.tenant_id == api_key.tenant_id)
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


def _run_or_404(db: Session, api_key: ApiKey, run_id: str) -> AgentRun:
    run = (
        db.query(AgentRun)
        .filter(AgentRun.id == run_id, AgentRun.tenant_id == api_key.tenant_id)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


def _session_dict(session: AgentSession, include_messages: bool = False) -> dict:
    data = {
        "id": session.id,
        "name": session.name,
        "agent_id": session.agent_id,
        "environment": session.environment,
        "autonomy": session.autonomy,
        "model_provider": session.model_provider,
        "model_id": session.model_id,
        "status": session.status,
        "policy_version": session.policy_version,
        "forked_from": session.forked_from,
        "grants": session.grants,
        "session_approvals": session.session_approvals,
        "message_count": len(session.messages or []),
        "created_at": session.created_at.isoformat() if session.created_at else None,
    }
    if include_messages:
        data["messages"] = session.messages
    return data


def _run_dict(run: AgentRun) -> dict:
    return {
        "id": run.id,
        "session_id": run.session_id,
        "state": run.state,
        "input_text": run.input_text,
        "output_text": run.output_text,
        "pending": run.pending,
        "error": run.error,
        "steps": run.steps,
        "cost_usd": run.cost_usd,
        "latency_ms": run.latency_ms,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


# --- sessions --------------------------------------------------------------


@router.post("/agent/sessions", status_code=201)
def create_session(
    payload: SessionIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    from helios.config import settings

    grants = payload.grants
    if grants is None:
        grants = developer_grants(
            workspace_root=workspace_root(), github_repo=payload.github_repo
        )
    session = AgentSession(
        tenant_id=api_key.tenant_id,
        name=payload.name,
        environment=payload.environment,
        autonomy=payload.autonomy,
        model_provider=payload.model_provider or settings.default_provider,
        model_id=payload.model_id,
        grants=grants,
        user_id=payload.user_id,
    )
    db.add(session)
    db.commit()
    return _session_dict(session)


@router.get("/agent/sessions")
def list_sessions(
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    sessions = (
        db.query(AgentSession)
        .filter(AgentSession.tenant_id == api_key.tenant_id)
        .order_by(AgentSession.created_at.desc())
        .limit(50)
        .all()
    )
    return {"sessions": [_session_dict(s) for s in sessions]}


@router.get("/agent/sessions/{session_id}")
def get_session(
    session_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    session = _session_or_404(db, api_key, session_id)
    runs = (
        db.query(AgentRun)
        .filter(AgentRun.session_id == session.id)
        .order_by(AgentRun.created_at.desc())
        .limit(20)
        .all()
    )
    data = _session_dict(session, include_messages=True)
    data["runs"] = [_run_dict(r) for r in runs]
    return data


@router.post("/agent/sessions/{session_id}/fork", status_code=201)
def fork_session(
    session_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    source = _session_or_404(db, api_key, session_id)
    fork = AgentSession(
        tenant_id=source.tenant_id,
        name=f"{source.name} (fork)",
        environment=source.environment,
        autonomy=source.autonomy,
        model_provider=source.model_provider,
        model_id=source.model_id,
        grants=list(source.grants or []),
        messages=list(source.messages or []),
        user_id=source.user_id,
        forked_from=source.id,
        policy_version=source.policy_version,
    )
    db.add(fork)
    db.commit()
    return _session_dict(fork)


# --- runs ------------------------------------------------------------------


@router.post("/agent/sessions/{session_id}/messages", status_code=201)
async def send_message(
    session_id: str,
    payload: MessageIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    session = _session_or_404(db, api_key, session_id)
    session.messages = list(session.messages or []) + [
        {"role": "user", "content": payload.content}
    ]
    run = AgentRun(
        tenant_id=api_key.tenant_id,
        session_id=session.id,
        input_text=payload.content,
        state="thinking",
    )
    db.add(run)
    db.commit()

    run = await _runtime().run_message(db, session, run)
    return _run_dict(run)


@router.get("/agent/runs/{run_id}")
def get_run(
    run_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    return _run_dict(_run_or_404(db, api_key, run_id))


@router.get("/agent/runs/{run_id}/events")
def run_events(
    run_id: str,
    after_seq: int = 0,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    run = _run_or_404(db, api_key, run_id)
    events = (
        db.query(TraceEvent)
        .filter(TraceEvent.run_id == run.id, TraceEvent.seq > after_seq)
        .order_by(TraceEvent.seq)
        .limit(500)
        .all()
    )
    return {"run": _run_dict(run), "events": [event_to_dict(e) for e in events]}


@router.post("/agent/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    run = _run_or_404(db, api_key, run_id)
    if run.state in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"run already {run.state}")
    run.cancel_requested = True
    if run.state in ("awaiting_approval", "blocked"):
        # nothing is executing; cancel immediately and void the pending ask
        pending = run.pending or {}
        approval = db.get(ApprovalRequest, pending.get("approval_id", "") or "")
        if approval is not None and approval.status == "pending":
            approval.status = "expired"
        run.state = "cancelled"
        run.pending = None
    db.commit()
    return _run_dict(run)


@router.post("/agent/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    run = _run_or_404(db, api_key, run_id)
    session = _session_or_404(db, api_key, run.session_id)
    run = await _runtime().resume(db, session, run)
    return _run_dict(run)


@router.post("/agent/runs/{run_id}/retry", status_code=201)
async def retry_run(
    run_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    source = _run_or_404(db, api_key, run_id)
    if source.state not in ("failed", "cancelled", "blocked"):
        raise HTTPException(
            status_code=409, detail=f"only failed/cancelled/blocked runs can be "
            f"retried (state={source.state})")
    session = _session_or_404(db, api_key, source.session_id)
    run = AgentRun(
        tenant_id=api_key.tenant_id,
        session_id=session.id,
        input_text=source.input_text,
        state="thinking",
    )
    db.add(run)
    db.commit()
    run = await _runtime().run_message(db, session, run)
    return _run_dict(run)


@router.post("/agent/runs/{run_id}/replay")
def replay(
    run_id: str,
    payload: ReplayIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    run = _run_or_404(db, api_key, run_id)
    session = _session_or_404(db, api_key, run.session_id)
    try:
        return replay_run(
            db, run, session,
            policy_version=payload.policy_version,
            policy_doc=payload.policy,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# --- approvals -------------------------------------------------------------


@router.post("/agent/approvals/{approval_id}/decide")
def decide_approval(
    approval_id: str,
    payload: DecideIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    approval = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.id == approval_id,
                ApprovalRequest.tenant_id == api_key.tenant_id)
        .first()
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail=f"already {approval.status}")

    summary = dict(approval.summary or {})
    run_id = summary.get("run_id")
    session_id = summary.get("session_id")

    if payload.edited_args is not None:
        if not summary.get("args_editable"):
            raise HTTPException(
                status_code=422,
                detail="this tool does not allow argument editing on approval")
        # Re-bind the approval to the EDITED payload: the human approves
        # exactly what will execute, nothing else.
        new_hash = hash_args(approval.action, payload.edited_args)
        summary["original_args_hash"] = approval.args_hash
        summary["args"] = args_preview(payload.edited_args)
        summary["edited"] = True
        approval.args_hash = new_hash
        if run_id:
            run = db.get(AgentRun, run_id)
            if run is not None and run.pending:
                pending = dict(run.pending)
                pending["args"] = payload.edited_args
                pending["args_hash"] = new_hash
                run.pending = pending

    if payload.decision == "approve_session":
        approval.status = "approved"
        if session_id:
            session = db.get(AgentSession, session_id)
            if session is not None:
                session.session_approvals = list(session.session_approvals or []) + [
                    {"tool": approval.action,
                     "granted_by": payload.decided_by,
                     "at": datetime.now(timezone.utc).isoformat()}
                ]
    else:
        approval.status = payload.decision

    approval.summary = summary
    approval.decided_by = payload.decided_by
    approval.decided_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "id": approval.id,
        "action": approval.action,
        "status": approval.status,
        "decided_by": approval.decided_by,
        "run_id": run_id,
        "session_id": session_id,
    }


# --- tools + policies ------------------------------------------------------


@router.get("/tools")
def list_tools(api_key: ApiKey = Depends(get_api_key)):
    return {"tools": [m.public_dict() for m in default_registry().list()]}


@router.post("/tools/invoke")
def invoke_tool(
    payload: InvokeIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    if payload.session_id:
        session = _session_or_404(db, api_key, payload.session_id)
        grants = session.grants or []
        environment = session.environment
        session_id = session.id
    else:
        grants = developer_grants(workspace_root=workspace_root())
        environment = payload.environment
        session_id = None

    context = InvocationContext(
        tenant_id=api_key.tenant_id,
        environment=environment,
        session_id=session_id,
        user_id="api",
    )
    recorder = TraceRecorder(db, tenant_id=api_key.tenant_id, session_id=session_id)
    result = ToolBroker(default_registry()).invoke(
        db, context, payload.tool, payload.args,
        permissions=PermissionSet(grants),
        recorder=recorder,
        idempotency_key=payload.idempotency_key,
    )
    return result.to_dict()


@router.get("/policies")
def list_policies(api_key: ApiKey = Depends(get_api_key)):
    return {"policies": [p.to_dict() for p in POLICIES.values()]}
