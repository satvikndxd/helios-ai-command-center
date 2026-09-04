"""
Deterministic replay.

Every tool proposal in a recorded run carries the exact context and args
that were evaluated. Replaying re-runs the pure decision pipeline
(permissions -> risk -> policy) against the same policy, a newer policy, or
a candidate policy document — and diffs the outcomes:

    ORIGINAL                 CANDIDATE (policy v2)
    5 executed, 1 approval   4 allowed, 1 denied, 1 approval

Nothing is executed during replay; it is evaluation only.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.broker.core import ToolBroker
from helios.broker.permissions import PermissionSet
from helios.broker.policy import ToolPolicy, get_policy
from helios.broker.registry import default_registry
from helios.broker.types import InvocationContext
from helios.models import AgentRun, AgentSession, TraceEvent


def _original_outcome(db: Session, proposal: TraceEvent) -> str:
    """What actually happened to this proposal, from the recorded children."""
    children = (
        db.query(TraceEvent)
        .filter(TraceEvent.parent_id == proposal.id)
        .order_by(TraceEvent.seq)
        .all()
    )
    for event in children:
        if event.event_type == "approval" and event.status == "pending":
            return "approval_required"
    for event in children:
        if event.event_type == "tool_execution":
            return "executed" if event.status in ("ok", "replayed") else "error"
    for event in children:
        if event.event_type == "outcome" and event.status == "denied":
            return "denied"
    return "denied"


def replay_run(
    db: Session,
    run: AgentRun,
    session: AgentSession,
    *,
    policy_version: str | None = None,
    policy_doc: dict | None = None,
    broker: ToolBroker | None = None,
) -> dict:
    """Re-evaluate every recorded tool proposal against a target policy."""
    if policy_doc is not None:
        policy = ToolPolicy.from_dict(policy_doc)
    else:
        policy = get_policy(policy_version)

    broker = broker or ToolBroker(default_registry())
    permissions = PermissionSet(session.grants or [])

    proposals = (
        db.query(TraceEvent)
        .filter(TraceEvent.run_id == run.id,
                TraceEvent.event_type == "tool_proposal")
        .order_by(TraceEvent.seq)
        .all()
    )

    comparisons = []
    original_counts: dict[str, int] = {}
    candidate_counts: dict[str, int] = {}

    for proposal in proposals:
        payload = proposal.payload or {}
        context = InvocationContext.from_dict(
            payload.get("context") or {"tenant_id": run.tenant_id}
        )
        args = payload.get("args") or {}
        tool = payload.get("tool") or proposal.name

        original = _original_outcome(db, proposal)
        evaluation = broker.evaluate(tool, args, context, permissions, policy)
        candidate = {
            "allow": "executed",
            "deny": "denied",
            "require_approval": "approval_required",
        }.get(evaluation["decision"], evaluation["decision"])

        original_counts[original] = original_counts.get(original, 0) + 1
        candidate_counts[candidate] = candidate_counts.get(candidate, 0) + 1
        comparisons.append({
            "seq": proposal.seq,
            "tool": tool,
            "args_hash": payload.get("args_hash"),
            "original": original,
            "candidate": candidate,
            "changed": original != candidate,
            "candidate_reason": evaluation["reason"],
            "candidate_risk": evaluation.get("risk"),
            "candidate_rule": (evaluation.get("policy") or {}).get("rule_id"),
        })

    return {
        "run_id": run.id,
        "policy": {"version": policy.version, "description": policy.description},
        "original_policy_version": session.policy_version,
        "proposals": len(proposals),
        "original": original_counts,
        "candidate": candidate_counts,
        "changes": [c for c in comparisons if c["changed"]],
        "comparisons": comparisons,
    }
