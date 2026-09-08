"""
EVIDENCE PLANE — normalized AI Decision Records.

Every governed decision — a tool proposal, a model-use gate, a change
approval — is written as a stable, versioned DecisionRecord so audits can
query "what decisions did this system make?" without walking raw traces.

Records are derived from real recorded state; they never invent evidence.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.models import AgentRun, AgentSession, DecisionRecord

SCHEMA_VERSION = "1.0"


def record_tool_decision(
    db: Session,
    context,
    *,
    tool: str,
    decision: str,
    reason: str,
    risk: dict | None,
    policy: dict | None,
    resource: dict | None,
    data_classes: list[str] | None,
    human_oversight: str,
    reviewer: str | None,
    approval_id: str | None,
    trace_event_id: str | None,
) -> DecisionRecord:
    record = DecisionRecord(
        tenant_id=context.tenant_id,
        schema_version=SCHEMA_VERSION,
        system_id=context.system_id,
        run_id=context.run_id,
        session_id=context.session_id,
        actor=context.agent_id or "helios-agent",
        kind="tool_call",
        action=tool,
        data_classes=list(data_classes or []),
        resource=dict(resource or {}),
        risk=(risk or {}).get("risk"),
        risk_score=(risk or {}).get("score"),
        policy_version=(policy or {}).get("policy_version"),
        policy_rule=(policy or {}).get("rule_id"),
        decision=decision,
        reason=reason,
        human_oversight=human_oversight,
        reviewer=reviewer,
        approval_id=approval_id,
        trace_event_id=trace_event_id,
        evidence={"risk": risk, "policy": policy},
    )
    db.add(record)
    db.commit()
    return record


def record_model_use_decision(
    db: Session,
    session: AgentSession,
    run: AgentRun,
    decision,
    classification,
) -> DecisionRecord:
    record = DecisionRecord(
        tenant_id=session.tenant_id,
        schema_version=SCHEMA_VERSION,
        system_id=session.system_id,
        run_id=run.id,
        session_id=session.id,
        actor=session.agent_id or "helios-agent",
        kind="model_use",
        action=f"model_call:{decision.provider}/{decision.model_id}",
        model_provider=decision.provider,
        model_id=decision.model_id,
        model_version=decision.model_version,
        data_classes=list(classification.classes),
        risk=decision.risk_class,
        decision="allow" if decision.allowed else "deny",
        reason="; ".join(decision.reasons),
        human_oversight="not_required",
        evidence={"model_use": decision.to_dict(),
                  "classification": classification.to_dict()},
    )
    db.add(record)
    db.commit()
    return record


def record_to_dict(record: DecisionRecord) -> dict:
    return {
        "id": record.id,
        "schema_version": record.schema_version,
        "system_id": record.system_id,
        "run_id": record.run_id,
        "session_id": record.session_id,
        "actor": record.actor,
        "kind": record.kind,
        "action": record.action,
        "model": {
            "provider": record.model_provider,
            "model_id": record.model_id,
            "version": record.model_version,
        } if record.model_provider else None,
        "data_classes": record.data_classes,
        "resource": record.resource,
        "risk": record.risk,
        "risk_score": record.risk_score,
        "policy": {"version": record.policy_version, "rule": record.policy_rule},
        "decision": record.decision,
        "reason": record.reason,
        "human_oversight": record.human_oversight,
        "reviewer": record.reviewer,
        "approval_id": record.approval_id,
        "trace_event_id": record.trace_event_id,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
