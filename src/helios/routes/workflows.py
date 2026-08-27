"""
Workflow layer routes.

Reuses the existing auth, tenant isolation, DB session, error handling, and
(through the engine) the existing governance stack.  Approvals/actions for
consequential follow-ups go through the EXISTING /v1/actions endpoints —
no duplicate approval surface here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.models import (
    ApiKey,
    ApprovalRequest,
    DecisionTrace,
    ReviewItem,
    WorkflowExecution,
    WorkspaceSource,
)
from helios.security import get_api_key
from helios.web.actions import propose_action
from helios.workflows.engine import WorkflowEngine
from helios.workflows.registry import all_packs, get_pack
from helios.workflows.seeding import seed_workspace

router = APIRouter(tags=["workflows"])

FEEDBACK_RATINGS = {"useful", "incorrect", "incomplete", "unsafe", "irrelevant"}


class WorkflowRunIn(BaseModel):
    workspace_id: str
    workflow_id: str
    input: dict = Field(default_factory=dict)


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: str = Field(min_length=1, max_length=50)
    record: dict = Field(default_factory=dict)
    content: str | None = None
    trust: str = "internal"
    provenance: dict = Field(default_factory=dict)


class FeedbackIn(BaseModel):
    rating: str
    comment: str | None = None


class ProposeActionIn(BaseModel):
    summary: str | None = None


def _pack_or_404(workspace_id: str):
    try:
        return get_pack(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _serialize_execution(e: WorkflowExecution, full: bool = False) -> dict:
    data = {
        "id": e.id,
        "workspace_id": e.workspace_id,
        "workflow_id": e.workflow_id,
        "trace_id": e.trace_id,
        "status": e.status,
        "risk": e.risk,
        "requires_approval": e.requires_approval,
        "confidence": e.confidence,
        "evidence_count": len(e.evidence or []),
        "fact_count": len(e.facts or []),
        "latency_ms": e.latency_ms,
        "cost_usd": e.cost_usd,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }
    if full:
        data.update(
            {
                "input": e.input,
                "facts": e.facts,
                "evidence": e.evidence,
                "claims": e.claims,
                "interpretation": e.interpretation,
                "recommendation": e.recommendation,
                "evaluation": e.evaluation,
                "feedback": e.feedback,
            }
        )
    return data


# -- workspaces ------------------------------------------------------------


@router.get("/v1/workspaces")
async def list_workspaces(api_key: ApiKey = Depends(get_api_key)):
    return {
        "workspaces": [
            {
                "id": p.config.id,
                "name": p.config.name,
                "domain": p.config.domain,
                "description": p.config.description,
                "capabilities": p.config.capabilities,
                "workflows": [w.id for w in p.config.workflows],
                "synthetic": p.config.metadata.get("synthetic", False),
            }
            for p in all_packs().values()
        ]
    }


@router.get("/v1/workspaces/{workspace_id}")
async def get_workspace(workspace_id: str, api_key: ApiKey = Depends(get_api_key)):
    pack = _pack_or_404(workspace_id)
    config = pack.config
    return {
        "id": config.id,
        "name": config.name,
        "domain": config.domain,
        "description": config.description,
        "terminology": config.terminology,
        "system_instructions": config.system_instructions,
        "source_types": config.source_types,
        "capabilities": config.capabilities,
        "actions": [a.model_dump() for a in config.actions],
        "policies": config.policies,
        "risk_config": config.risk_config,
        "metadata": config.metadata,
        "workflows": [
            {
                "id": w.id,
                "name": w.name,
                "description": w.description,
                "input_schema": w.input_schema,
                "source_types": w.source_types,
                "base_risk": w.base_risk,
                "approval": w.approval.model_dump(),
            }
            for w in config.workflows
        ],
    }


@router.get("/v1/workspaces/{workspace_id}/workflows")
async def list_workflows(workspace_id: str, api_key: ApiKey = Depends(get_api_key)):
    pack = _pack_or_404(workspace_id)
    return {
        "workflows": [
            {"id": w.id, "name": w.name, "description": w.description,
             "input_schema": w.input_schema, "base_risk": w.base_risk}
            for w in pack.config.workflows
        ]
    }


@router.post("/v1/workspaces/{workspace_id}/seed")
async def seed(
    workspace_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Load the pack's synthetic demo data for this tenant (idempotent)."""
    pack = _pack_or_404(workspace_id)
    created = await seed_workspace(db, api_key.tenant_id, pack)
    return {"workspace_id": workspace_id, "created": created}


# -- sources ---------------------------------------------------------------


@router.post("/v1/workspaces/{workspace_id}/sources", status_code=201)
async def add_source(
    workspace_id: str,
    payload: SourceIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    _pack_or_404(workspace_id)
    source = WorkspaceSource(
        tenant_id=api_key.tenant_id,
        workspace_id=workspace_id,
        name=payload.name,
        type=payload.type,
        record=payload.record,
        content=payload.content,
        trust=payload.trust
        if payload.trust in ("internal", "untrusted_external_content")
        else "untrusted_external_content",
        provenance=payload.provenance,
    )
    db.add(source)
    db.commit()
    return {"id": source.id, "name": source.name, "type": source.type,
            "trust": source.trust, "version": source.version}


@router.get("/v1/workspaces/{workspace_id}/sources")
async def list_sources(
    workspace_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    _pack_or_404(workspace_id)
    sources = (
        db.query(WorkspaceSource)
        .filter(
            WorkspaceSource.tenant_id == api_key.tenant_id,
            WorkspaceSource.workspace_id == workspace_id,
        )
        .order_by(WorkspaceSource.created_at.desc())
        .all()
    )
    return {
        "sources": [
            {"id": s.id, "name": s.name, "type": s.type, "trust": s.trust,
             "version": s.version, "status": s.status,
             "provenance": s.provenance,
             "created_at": s.created_at.isoformat() if s.created_at else None}
            for s in sources
        ]
    }


# -- execution -------------------------------------------------------------


@router.post("/v1/workflows/run")
async def run_workflow(
    payload: WorkflowRunIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    pack = _pack_or_404(payload.workspace_id)
    if pack.config.workflow(payload.workflow_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"workflow '{payload.workflow_id}' not found in workspace "
            f"'{payload.workspace_id}'",
        )
    engine = WorkflowEngine(db, api_key)
    execution = await engine.run(pack, payload.workflow_id, payload.input)
    return _serialize_execution(execution, full=True)


@router.get("/v1/workflows/executions")
async def list_executions(
    workspace_id: str | None = None,
    limit: int = 20,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    query = db.query(WorkflowExecution).filter(
        WorkflowExecution.tenant_id == api_key.tenant_id
    )
    if workspace_id:
        query = query.filter(WorkflowExecution.workspace_id == workspace_id)
    executions = (
        query.order_by(WorkflowExecution.created_at.desc()).limit(min(limit, 100)).all()
    )
    return {"executions": [_serialize_execution(e) for e in executions]}


@router.get("/v1/workflows/executions/{execution_id}")
async def get_execution(
    execution_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    execution = (
        db.query(WorkflowExecution)
        .filter(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.tenant_id == api_key.tenant_id,
        )
        .first()
    )
    if not execution:
        raise HTTPException(status_code=404, detail="execution not found")
    return _serialize_execution(execution, full=True)


@router.post("/v1/workflows/executions/{execution_id}/propose-action", status_code=201)
async def propose_followup_action(
    execution_id: str,
    payload: ProposeActionIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Create the workflow's typed follow-up action through the EXISTING
    approval system (payload-hash-bound; execute via /v1/actions/execute).
    """
    execution = (
        db.query(WorkflowExecution)
        .filter(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.tenant_id == api_key.tenant_id,
        )
        .first()
    )
    if not execution:
        raise HTTPException(status_code=404, detail="execution not found")
    pack = _pack_or_404(execution.workspace_id)
    workflow = pack.config.workflow(execution.workflow_id)
    action_name = workflow.approval.action if workflow else None
    if not action_name:
        raise HTTPException(
            status_code=422,
            detail=f"workflow '{execution.workflow_id}' defines no follow-up action",
        )
    args = {
        "workspace_id": execution.workspace_id,
        "workflow_id": execution.workflow_id,
        "execution_id": execution.id,
        "trace_id": execution.trace_id,
        "summary": payload.summary or (execution.recommendation or "")[:500],
    }
    approval = propose_action(db, api_key.tenant_id, action_name, args)
    return {
        "approval_id": approval.id,
        "action": action_name,
        "args": args,
        "risk": approval.risk,
        "status": approval.status,
        "next": "approve via POST /v1/approvals/{id}/decide, then execute via "
                "POST /v1/actions/execute with these exact args",
    }


# -- feedback --------------------------------------------------------------


@router.post("/v1/workflows/executions/{execution_id}/feedback")
async def execution_feedback(
    execution_id: str,
    payload: FeedbackIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Human feedback: useful | incorrect | incomplete | unsafe | irrelevant.

    Negative ratings escalate to the EXISTING review queue and are recorded
    on the DecisionTrace so the EXISTING evolution engine mines them.
    """
    if payload.rating not in FEEDBACK_RATINGS:
        raise HTTPException(
            status_code=422,
            detail=f"rating must be one of {sorted(FEEDBACK_RATINGS)}",
        )
    execution = (
        db.query(WorkflowExecution)
        .filter(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.tenant_id == api_key.tenant_id,
        )
        .first()
    )
    if not execution:
        raise HTTPException(status_code=404, detail="execution not found")

    execution.feedback = {"rating": payload.rating, "comment": payload.comment}
    escalated = payload.rating in ("incorrect", "unsafe", "incomplete")
    if execution.trace_id:
        trace = (
            db.query(DecisionTrace)
            .filter(DecisionTrace.id == execution.trace_id)
            .first()
        )
        if trace:
            trace.feedback = {
                "thumbs": "down" if escalated else "up",
                "workflow_rating": payload.rating,
                "comment": payload.comment,
            }
    if escalated:
        db.add(
            ReviewItem(
                tenant_id=api_key.tenant_id,
                trace_id=execution.trace_id or execution.id,
                reason=f"workflow feedback: {payload.rating} "
                f"({execution.workspace_id}/{execution.workflow_id})"[:255],
            )
        )
    db.commit()
    return {"id": execution.id, "feedback": execution.feedback,
            "escalated_to_review": escalated}


# -- command center --------------------------------------------------------


@router.get("/v1/workspaces/{workspace_id}/overview")
async def workspace_overview(
    workspace_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Command-center view: executions, approvals, reviews, blocked traces."""
    _pack_or_404(workspace_id)
    tenant = api_key.tenant_id
    executions = (
        db.query(WorkflowExecution)
        .filter(
            WorkflowExecution.tenant_id == tenant,
            WorkflowExecution.workspace_id == workspace_id,
        )
        .order_by(WorkflowExecution.created_at.desc())
        .limit(10)
        .all()
    )
    pending_approvals = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.tenant_id == tenant, ApprovalRequest.status == "pending")
        .count()
    )
    open_reviews = (
        db.query(ReviewItem)
        .filter(ReviewItem.tenant_id == tenant, ReviewItem.status == "open")
        .count()
    )
    blocked_traces = (
        db.query(DecisionTrace)
        .filter(
            DecisionTrace.tenant_id == tenant,
            DecisionTrace.status == "blocked",
            DecisionTrace.task_type.like("workflow:%"),
        )
        .count()
    )
    sources = (
        db.query(WorkspaceSource)
        .filter(
            WorkspaceSource.tenant_id == tenant,
            WorkspaceSource.workspace_id == workspace_id,
        )
        .count()
    )
    return {
        "workspace_id": workspace_id,
        "sources": sources,
        "recent_executions": [_serialize_execution(e) for e in executions],
        "pending_approvals": pending_approvals,
        "open_reviews": open_reviews,
        "blocked_workflow_traces": blocked_traces,
        "requires_approval": sum(1 for e in executions if e.requires_approval),
        "health": "ok",
    }
