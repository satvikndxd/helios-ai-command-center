"""
ASSURANCE PLANE — governance audit report.

A machine-readable governance record for one AI system, assembled entirely
from recorded state. This is *governance evidence*, not a legal/regulatory
compliance certification — the language is deliberately "control status" and
"audit record".
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.governance.drift import detect_drift
from helios.governance.evaluation import evaluate_system
from helios.governance.oversight import oversight_report
from helios.governance.score import governance_score
from helios.governance.systems import system_to_dict, get_system
from helios.models import AIModel, ChangeRecord, DecisionRecord


def audit_report(db: Session, tenant_id: str, system_id: str) -> dict:
    system = get_system(db, tenant_id, system_id)
    if system is None:
        raise KeyError(f"system '{system_id}' not found")

    recent_decisions = (
        db.query(DecisionRecord)
        .filter(DecisionRecord.tenant_id == tenant_id,
                DecisionRecord.system_id == system_id)
        .order_by(DecisionRecord.created_at.desc())
        .limit(20)
        .all()
    )
    violations = [
        {"id": r.id, "action": r.action, "decision": r.decision,
         "reason": r.reason, "risk": r.risk,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in recent_decisions
        if r.decision == "deny" or r.human_oversight == "bypassed"
    ]

    changes = (
        db.query(ChangeRecord)
        .filter(ChangeRecord.tenant_id == tenant_id,
                ChangeRecord.system_id == system_id)
        .order_by(ChangeRecord.created_at.desc())
        .limit(10)
        .all()
    )

    models = (
        db.query(AIModel)
        .filter(AIModel.tenant_id == tenant_id)
        .all()
    )
    declared_ids = {
        (m.get("model_id") if isinstance(m, dict) else str(m))
        for m in (system.models or [])
    }
    model_status = [
        {"provider": m.provider, "model_id": m.model_id, "status": m.status,
         "allowed_data_classes": m.allowed_data_classes,
         "allowed_environments": m.allowed_environments}
        for m in models if m.model_id in declared_ids
    ]

    score = governance_score(db, tenant_id, system_id)
    oversight = oversight_report(db, tenant_id, system_id)
    drift = detect_drift(db, tenant_id, system_id)
    evaluation = evaluate_system(db, tenant_id, system_id)

    return {
        "record_type": "helios_governance_audit_record",
        "schema_version": "1.0",
        "disclaimer": "Governance evidence and control status only — not a "
                      "legal or regulatory compliance certification.",
        "system": system_to_dict(system, include_revisions=True),
        "governance_status": {
            "overall": score["overall"],
            "checks": score["checks"],
        },
        "evaluation": evaluation,
        "models": model_status,
        "human_oversight": oversight["summary"],
        "bypass_detected": oversight["bypass_detected"],
        "violations": violations,
        "recent_changes": [
            {"id": c.id, "kind": c.kind, "title": c.title, "status": c.status,
             "author": c.author,
             "created_at": c.created_at.isoformat() if c.created_at else None}
            for c in changes
        ],
        "drift": {"detected": drift["drift_detected"], "signals": drift["signals"]},
    }
