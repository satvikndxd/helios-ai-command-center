"""
Governance audit report (ASSURANCE plane).

One call assembles the complete governance evidence for an AI system:
identity, owner, purpose, models, tools, data classes, autonomy, active
policies, evaluations, violations, approvals, human oversight, recent
changes, drift, and the overall governance status — every section derived
from stored state, exportable as JSON (this dict IS the export).

Language discipline: this is a GOVERNANCE EVIDENCE report — policy status,
audit record, control status. It is NOT a legal or regulatory compliance
certification, and it never claims to be one.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from helios.governance import models_registry
from helios.governance.drift import latest_baseline, open_signals
from helios.governance.lineage import build_system_lineage
from helios.governance.oversight import analyze_system
from helios.governance.score import compute_governance_score
from helios.governance.systems import get_system_or_raise, list_versions
from helios.models import (
    AgentRun,
    AgentSession,
    ApprovalRequest,
    ChangeRecord,
    DecisionRecord,
    EvaluationRun,
)

REPORT_SCHEMA = "helios-audit-report-v1"


def build_audit_report(db: Session, tenant_id: str, system_id: str,
                       *, window: int = 50) -> dict:
    system = get_system_or_raise(db, tenant_id, system_id)

    # --- identity + composition -------------------------------------------
    models = []
    for ref in (system.models or []):
        provider = ref.get("provider") if isinstance(ref, dict) else None
        model_id = ref.get("model_id") if isinstance(ref, dict) else str(ref)
        asset = models_registry.find_model(
            db, tenant_id, provider or "", model_id,
            version=ref.get("version") if isinstance(ref, dict) else None) \
            if provider else None
        models.append({
            "declared": ref,
            "registered": asset is not None,
            "approval_status": asset.approval_status if asset else "not_registered",
            "approved_by": asset.approved_by if asset else None,
            "allowed_data_classes": list(asset.allowed_data_classes or []) if asset else [],
        })

    versions = list_versions(db, system)

    # --- evidence: decisions, runs, violations ------------------------------
    decisions = (
        db.query(DecisionRecord)
        .filter(DecisionRecord.tenant_id == tenant_id,
                DecisionRecord.system_id == system_id)
        .order_by(DecisionRecord.created_at.desc())
        .limit(window)
        .all()
    )
    decision_counts: dict[str, int] = {}
    for record in decisions:
        decision_counts[record.decision] = decision_counts.get(record.decision, 0) + 1
    violations = [
        {"record_id": r.id, "run_id": r.run_id, "action": r.action,
         "decision": r.decision, "risk": r.risk,
         "reason": (r.outcome or {}).get("reason"),
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in decisions if r.decision == "DENIED"
    ]

    runs = (
        db.query(AgentRun)
        .join(AgentSession, AgentRun.session_id == AgentSession.id)
        .filter(AgentSession.tenant_id == tenant_id,
                AgentSession.system_id == system_id)
        .order_by(AgentRun.created_at.desc())
        .limit(window)
        .all()
    )
    run_counts: dict[str, int] = {}
    for run in runs:
        run_counts[run.state] = run_counts.get(run.state, 0) + 1

    # --- oversight + approvals ------------------------------------------------
    oversight = analyze_system(db, tenant_id, system_id, system=system)
    approvals = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.tenant_id == tenant_id,
                ApprovalRequest.system_id == system_id)
        .order_by(ApprovalRequest.created_at.desc())
        .limit(window)
        .all()
    )
    approval_counts: dict[str, int] = {}
    for approval in approvals:
        approval_counts[approval.status] = approval_counts.get(approval.status, 0) + 1

    # --- policies ---------------------------------------------------------------
    from helios.governance import engine as governance_engine

    active_sets = governance_engine.active_policy_sets(db, tenant_id)
    applied_policies = []
    for policy_set in active_sets:
        scope = policy_set.scope or {}
        applies = (not scope.get("system_ids")
                   or system_id in scope["system_ids"])
        applied_policies.append({
            "name": policy_set.name, "version": policy_set.version,
            "applies_to_system": applies,
            "rules": len(policy_set.rules),
            "scope": scope,
        })

    # --- assurance: evaluation + score + drift -----------------------------------
    evaluation = (
        db.query(EvaluationRun)
        .filter(EvaluationRun.tenant_id == tenant_id,
                EvaluationRun.system_id == system_id,
                EvaluationRun.kind == "governance")
        .order_by(EvaluationRun.created_at.desc())
        .first()
    )
    score = compute_governance_score(db, system)
    drift = open_signals(db, tenant_id, system_id)
    baseline = latest_baseline(db, tenant_id, system_id)

    # --- changes ------------------------------------------------------------------
    changes = (
        db.query(ChangeRecord)
        .filter(ChangeRecord.tenant_id == tenant_id,
                ChangeRecord.system_id == system_id)
        .order_by(ChangeRecord.created_at.desc())
        .limit(window)
        .all()
    )

    # --- data flows -----------------------------------------------------------------
    lineage = build_system_lineage(db, tenant_id, system_id, limit_runs=window)

    return {
        "report": REPORT_SCHEMA,
        "disclaimer": (
            "Governance evidence report: policy status, audit record, and "
            "control status derived from stored HELIOS evidence. This is not "
            "a legal or regulatory compliance certification."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "system": {
            "system_id": system.system_id,
            "name": system.name,
            "owner": system.owner,
            "organization": system.organization,
            "purpose": system.purpose,
            "description": system.description,
            "environment": system.environment,
            "lifecycle": system.lifecycle,
            "autonomy_level": system.autonomy_level,
            "risk_class": system.risk_class,
            "version": system.version,
            "tools": list(system.tools or []),
            "data_classes": list(system.data_classes or []),
            "declared_policies": list(system.policies or []),
            "oversight_requirements": dict(system.oversight or {}),
            "deployment": dict(system.deployment or {}),
            "blocked_reason": system.blocked_reason,
            "created_at": system.created_at.isoformat() if system.created_at else None,
            "version_count": len(versions),
        },
        "models": models,
        "policies": applied_policies,
        "activity": {
            "window": window,
            "runs": {"total": len(runs), "by_state": run_counts},
            "decisions": {"total": len(decisions), "by_decision": decision_counts},
            "approvals": {"total": len(approvals), "by_status": approval_counts},
        },
        "violations": violations,
        "human_oversight": oversight,
        "data_flows": {
            "sources": lineage["summary"]["sources"],
            "destinations": lineage["summary"]["destinations"],
            "data_classes_seen": lineage["summary"]["data_classes_seen"],
            "violation_count": lineage["summary"]["violation_count"],
            "violations": lineage["violations"][:10],
        },
        "evaluation": ({
            "id": evaluation.id, "score": evaluation.score,
            "passed": evaluation.passed, "status": evaluation.status,
            "metrics": {name: {"score": r.get("score"), "passed": r.get("passed")}
                        for name, r in (evaluation.results or {}).items()},
            "created_at": evaluation.created_at.isoformat()
            if evaluation.created_at else None,
        } if evaluation else None),
        "governance_score": score,
        "drift": {
            "baseline": ({"id": baseline.id, "label": baseline.label,
                          "created_at": baseline.created_at.isoformat()
                          if baseline.created_at else None} if baseline else None),
            "signals": drift,
        },
        "changes": [{
            "id": c.id, "change_type": c.change_type, "title": c.title,
            "author": c.author, "state": c.state,
            "deployed_at": c.deployed_at.isoformat() if c.deployed_at else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        } for c in changes],
        "recent_decisions": [{
            "id": r.id, "action": r.action, "decision": r.decision,
            "risk": r.risk, "model": r.model,
            "oversight": r.oversight,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in decisions[:10]],
    }
