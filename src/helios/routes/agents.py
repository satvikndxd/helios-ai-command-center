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
    # V1.5 IDENTITY plane: bind this session to a registered AI system.
    system_id: str | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1)


class DecideIn(BaseModel):
    decision: str = Field(pattern="^(approved|denied|approve_session)$")
    decided_by: str = "operator"
    edited_args: dict | None = None
    # --- V1.5 human oversight expansion ----------------------------------
    comment: str | None = None            # audit thread entry
    expires_in_s: int | None = Field(default=None, ge=0)  # approval lapse
    # session-approval scope binding:
    scope: dict | None = None             # {"environment": ..., "system_id": ...}
    max_uses: int | None = Field(default=None, ge=1)


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
        "system_id": session.system_id,
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

    # V1.5: a session may bind to a registered AI system (IDENTITY plane).
    # The system must exist in this tenant — no phantom identities.
    if payload.system_id is not None:
        from helios.governance import systems as system_registry

        registered = system_registry.get_system(db, api_key.tenant_id, payload.system_id)
        if registered is None:
            raise HTTPException(
                status_code=404,
                detail=f"ai system '{payload.system_id}' is not registered",
            )

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
        system_id=payload.system_id,
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
        system_id=source.system_id,
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


@router.get("/agent/runs/{run_id}/lineage")
def run_lineage(
    run_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Data lineage of one run, derived EXCLUSIVELY from recorded TraceEvents.
    Every edge cites the event ids that prove it (EVIDENCE plane invariant).
    """
    from helios.governance.lineage import build_run_lineage

    run = _run_or_404(db, api_key, run_id)
    return build_run_lineage(db, api_key.tenant_id, run.id)


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
    from helios.governance.oversight import add_comment, check_decider, expire_stale

    expire_stale(db, api_key.tenant_id)
    approval = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.id == approval_id,
                ApprovalRequest.tenant_id == api_key.tenant_id)
        .first()
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    if approval.status != "pending":
        # includes `expired`: a lapsed approval can never be decided late
        raise HTTPException(status_code=409, detail=f"already {approval.status}")
    try:
        check_decider(approval, payload.decided_by)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

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

    now = datetime.now(timezone.utc)
    if payload.expires_in_s is not None:
        from datetime import timedelta

        approval.expires_at = now + timedelta(seconds=payload.expires_in_s)

    if payload.decision == "approve_session":
        approval.status = "approved"
        if session_id:
            session = db.get(AgentSession, session_id)
            if session is not None:
                scope = dict(payload.scope or {})
                entry = {
                    "tool": approval.action,
                    "granted_by": payload.decided_by,
                    "at": now.isoformat(),
                }
                # scope binding: environment defaults to the session's current
                # environment so a standing approval cannot silently travel
                if scope.get("environment") or scope.get("bind_environment", True):
                    entry["environment"] = scope.get("environment") \
                        or session.environment
                if scope.get("system_id") or session.system_id:
                    entry["system_id"] = scope.get("system_id") or session.system_id
                if payload.expires_in_s is not None:
                    entry["expires_at"] = approval.expires_at.isoformat()
                if payload.max_uses is not None:
                    entry["max_uses"] = payload.max_uses
                    entry["uses"] = 0
                session.session_approvals = list(session.session_approvals or []) + [entry]
    else:
        approval.status = payload.decision

    # decision_kind records WHAT KIND OF HUMAN DECISION was made
    approval.decision_kind = ("approval" if approval.status == "approved"
                              else "denial")

    approval.summary = summary
    approval.decided_by = payload.decided_by
    approval.decided_at = now
    if payload.comment:
        add_comment(approval, payload.decided_by, payload.comment)
    db.commit()

    # HUMAN_REVIEW evidence: the decision joins the trace hierarchy it gates.
    try:
        recorder = TraceRecorder(
            db, tenant_id=api_key.tenant_id, run_id=run_id,
            session_id=session_id, system_id=approval.system_id,
            start_seq=(
                db.query(TraceEvent.seq)
                .filter(TraceEvent.run_id == run_id)
                .order_by(TraceEvent.seq.desc())
                .first() or (0,)
            )[0],
        )
        recorder.record(
            "human_review", approval.action,
            {"decision": approval.status, "decided_by": payload.decided_by,
             "approval_id": approval.id, "decision_kind": approval.decision_kind,
             "comment": payload.comment,
             "edited": bool(payload.edited_args is not None),
             "expires_at": approval.expires_at.isoformat()
             if approval.expires_at else None},
            parent_id=summary.get("proposal_event_id"),
            status=approval.status,
        )
    except Exception:  # noqa: BLE001 - evidence must not break the decision
        db.rollback()

    return {
        "id": approval.id,
        "action": approval.action,
        "status": approval.status,
        "decided_by": approval.decided_by,
        "decision_kind": approval.decision_kind,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "comments": approval.comments or [],
        "run_id": run_id,
        "session_id": session_id,
    }


# --- oversight management (V1.5 Phase 7) ------------------------------------


class DelegateIn(BaseModel):
    to: str = Field(min_length=1, max_length=255)
    by: str = Field(min_length=1, max_length=255)
    reason: str | None = None


class CommentIn(BaseModel):
    by: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=2000)


def _approval_or_404(db: Session, api_key: ApiKey, approval_id: str) -> ApprovalRequest:
    approval = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.id == approval_id,
                ApprovalRequest.tenant_id == api_key.tenant_id)
        .first()
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return approval


def _approval_dict(approval: ApprovalRequest) -> dict:
    return {
        "id": approval.id,
        "action": approval.action,
        "risk": approval.risk,
        "status": approval.status,
        "decision_kind": approval.decision_kind,
        "system_id": approval.system_id,
        "delegated_to": approval.delegated_to,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "comments": approval.comments or [],
        "scope": approval.scope,
        "decided_by": approval.decided_by,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        "args_hash": approval.args_hash,
        "summary": approval.summary,
        "created_at": approval.created_at.isoformat() if approval.created_at else None,
    }


@router.get("/agent/approvals")
def list_agent_approvals(
    status: str | None = None,
    system_id: str | None = None,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Unified oversight queue across runs, models, and deployments."""
    from helios.governance.oversight import expire_stale

    expire_stale(db, api_key.tenant_id)
    q = db.query(ApprovalRequest).filter(
        ApprovalRequest.tenant_id == api_key.tenant_id)
    if status:
        q = q.filter(ApprovalRequest.status == status)
    if system_id:
        q = q.filter(ApprovalRequest.system_id == system_id)
    rows = q.order_by(ApprovalRequest.created_at.desc()).limit(200).all()
    return {"count": len(rows), "approvals": [_approval_dict(a) for a in rows]}


@router.get("/agent/approvals/{approval_id}")
def get_agent_approval(
    approval_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    return _approval_dict(_approval_or_404(db, api_key, approval_id))


@router.post("/agent/approvals/{approval_id}/delegate")
def delegate_approval(
    approval_id: str,
    payload: DelegateIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    from helios.governance.oversight import delegate

    approval = _approval_or_404(db, api_key, approval_id)
    try:
        delegate(approval, to=payload.to, by=payload.by, reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return _approval_dict(approval)


@router.post("/agent/approvals/{approval_id}/escalate")
def escalate_approval(
    approval_id: str,
    payload: DelegateIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    from helios.governance.oversight import escalation_comment

    approval = _approval_or_404(db, api_key, approval_id)
    if approval.status != "pending":
        raise HTTPException(status_code=409,
                            detail=f"already {approval.status}")
    try:
        escalation_comment(approval, by=payload.by, to=payload.to,
                           reason=payload.reason or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return _approval_dict(approval)


@router.post("/agent/approvals/{approval_id}/comments")
def comment_approval(
    approval_id: str,
    payload: CommentIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    from helios.governance.oversight import add_comment

    approval = _approval_or_404(db, api_key, approval_id)
    add_comment(approval, payload.by, payload.text)
    db.commit()
    return _approval_dict(approval)


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
    from helios.governance.autonomy import normalize_level

    if payload.session_id:
        session = _session_or_404(db, api_key, payload.session_id)
        grants = session.grants or []
        environment = session.environment
        session_id = session.id
        system_id = session.system_id
        autonomy = session.autonomy
        agent_id = session.agent_id
        user_id = session.user_id or "api"
    else:
        grants = developer_grants(workspace_root=workspace_root())
        environment = payload.environment
        session_id = None
        system_id = None
        autonomy = "supervised"
        agent_id = None
        user_id = "api"

    # A bound session carries the registered system's autonomy level.
    autonomy_level = normalize_level(autonomy)
    if system_id:
        from helios.governance.systems import get_system

        system = get_system(db, api_key.tenant_id, system_id)
        if system is not None:
            autonomy_level = system.autonomy_level

    context = InvocationContext(
        tenant_id=api_key.tenant_id,
        environment=environment,
        session_id=session_id,
        agent_id=agent_id,
        user_id=user_id,
        autonomy=autonomy,
        system_id=system_id,
        autonomy_level=autonomy_level,
    )
    recorder = TraceRecorder(db, tenant_id=api_key.tenant_id,
                             session_id=session_id, system_id=system_id)
    result = ToolBroker(default_registry()).invoke(
        db, context, payload.tool, payload.args,
        permissions=PermissionSet(grants),
        recorder=recorder,
        idempotency_key=payload.idempotency_key,
    )
    return result.to_dict()


@router.get("/policies")
def list_policies(
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    V1: registered tool-policy versions (`policies` — shape unchanged).
    V1.5: the tenant's governance policy sets alongside (`governance_policies`).
    """
    from helios.governance import engine as governance_engine
    from helios.models import GovernancePolicySet as PolicySetRow

    rows = (
        db.query(PolicySetRow)
        .filter(PolicySetRow.tenant_id == api_key.tenant_id,
                PolicySetRow.status == "active")
        .order_by(PolicySetRow.name, PolicySetRow.version)
        .all()
    )
    return {
        "policies": [p.to_dict() for p in POLICIES.values()],
        "governance_policies": [
            governance_engine.DEFAULT_GOVERNANCE_POLICY.to_dict(),
            *(governance_engine.policy_set_to_dict(r) for r in rows),
        ],
    }
