"""
ASSURANCE PLANE — deterministic governance evaluations.

Computed from recorded evidence (DecisionRecords + TraceEvents), never from
an LLM-as-judge alone. Each metric is a fraction in [0,1] with a count-based
explanation, so any score is reproducible and auditable. LLM judges and
human review can be layered on later behind the same metrics dict.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.models import AgentRun, AgentSession, DecisionRecord, TraceEvent


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def evaluate_system(db: Session, tenant_id: str, system_id: str) -> dict:
    """Deterministic governance metrics for one AI system."""
    records = (
        db.query(DecisionRecord)
        .filter(DecisionRecord.tenant_id == tenant_id,
                DecisionRecord.system_id == system_id)
        .all()
    )
    run_ids = [
        r[0] for r in db.query(AgentRun.id)
        .join(AgentSession, AgentRun.session_id == AgentSession.id)
        .filter(AgentSession.system_id == system_id,
                AgentSession.tenant_id == tenant_id)
        .all()
    ]

    tool_records = [r for r in records if r.kind == "tool_call"]
    model_records = [r for r in records if r.kind == "model_use"]

    # policy compliance: fraction of decisions that were NOT denied/bypassed
    denied = sum(1 for r in records if r.decision == "deny")
    bypassed = sum(1 for r in records if r.human_oversight == "bypassed")
    compliance = _rate(len(records) - denied - bypassed, len(records))

    # human-oversight compliance: of decisions that required a human, how
    # many actually got one (approved or denied), i.e. none bypassed
    required = [r for r in records
                if r.human_oversight in ("required", "approved", "denied", "bypassed")]
    honored = sum(1 for r in required if r.human_oversight in ("approved", "denied"))
    oversight_compliance = _rate(honored, len(required)) if required else 1.0

    # tool misuse: denied tool calls / total tool calls (lower is better ->
    # report as a "clean" rate)
    tool_denied = sum(1 for r in tool_records if r.decision == "deny")
    tool_clean = _rate(len(tool_records) - tool_denied, len(tool_records))

    # model governance: fraction of model uses that were allowed
    model_allowed = sum(1 for r in model_records if r.decision == "allow")
    model_compliance = _rate(model_allowed, len(model_records)) if model_records else 1.0

    # PII/secret exposure: data_access events on non-idempotent network/write
    # that were allowed while carrying sensitive+ data (should be ~0)
    sensitive_exposure = 0
    for r in tool_records:
        if r.decision == "allow" and any(
            c in ("sensitive", "pii") for c in (r.data_classes or [])
        ):
            sensitive_exposure += 1
    pii_protection = _rate(len(tool_records) - sensitive_exposure, len(tool_records))

    # task success: runs that completed (vs failed) — blocked counts as a
    # governance stop, not a failure
    completed = failed = 0
    if run_ids:
        for run in db.query(AgentRun).filter(AgentRun.id.in_(run_ids)).all():
            if run.state == "completed":
                completed += 1
            elif run.state == "failed":
                failed += 1
    task_success = _rate(completed, completed + failed) if (completed + failed) else 1.0

    # cost/latency: aggregate from model_call trace events across runs
    total_cost = 0.0
    total_latency = 0
    calls = 0
    if run_ids:
        events = (
            db.query(TraceEvent)
            .filter(TraceEvent.run_id.in_(run_ids),
                    TraceEvent.event_type == "model_call")
            .all()
        )
        for e in events:
            total_cost += e.cost_usd or 0.0
            total_latency += e.latency_ms or 0
            calls += 1

    metrics = {
        "policy_compliance": compliance,
        "oversight_compliance": oversight_compliance,
        "tool_misuse_clean_rate": tool_clean,
        "model_governance_compliance": model_compliance,
        "pii_protection": pii_protection,
        "task_success": task_success,
    }
    counters = {
        "decisions": len(records),
        "tool_calls": len(tool_records),
        "model_uses": len(model_records),
        "denied": denied,
        "bypassed": bypassed,
        "runs": len(run_ids),
        "runs_completed": completed,
        "runs_failed": failed,
        "model_calls": calls,
        "total_cost_usd": round(total_cost, 6),
        "avg_latency_ms": int(total_latency / calls) if calls else 0,
    }
    return {"system_id": system_id, "metrics": metrics, "counters": counters,
            "sample_size": len(records)}
