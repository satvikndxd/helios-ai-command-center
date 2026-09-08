"""
EVIDENCE PLANE — the "WHY?" explainer.

Assembles a governance explanation for a decision from ACTUAL recorded
state (the DecisionRecord, its system, the model registry, the approval),
never hard-coded UI text. Each line is a check with a ✓/✗ and the concrete
fact behind it.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.governance.systems import get_system
from helios.models import ApprovalRequest, DecisionRecord


def _check(ok: bool, label: str, detail: str) -> dict:
    return {"ok": ok, "label": label, "detail": detail}


def explain_decision(db: Session, record: DecisionRecord) -> dict:
    checks: list[dict] = []

    # identity
    system = get_system(db, record.tenant_id, record.system_id) if record.system_id else None
    if system is not None:
        checks.append(_check(True, "AI system registered",
                             f"{system.system_id} owned by {system.owner}"))
        checks.append(_check(system.lifecycle == "active", "System lifecycle",
                             f"lifecycle = {system.lifecycle}"))
    else:
        checks.append(_check(record.system_id is None, "AI system",
                             "no system bound (ad-hoc invocation)"
                             if record.system_id is None
                             else f"system '{record.system_id}' not found"))

    checks.append(_check(bool(record.actor), "Actor identified",
                         f"actor = {record.actor}"))

    # model
    if record.model_provider:
        allowed = record.decision == "allow" if record.kind == "model_use" else True
        checks.append(_check(allowed, "Model governance",
                             f"{record.model_provider}/{record.model_id}"
                             f"{' v' + record.model_version if record.model_version else ''}"
                             f" — {record.reason if record.kind == 'model_use' else 'approved'}"))

    # data classification
    if record.data_classes:
        checks.append(_check(True, "Data classification",
                             f"classes = {record.data_classes}"))
    else:
        checks.append(_check(True, "Data classification", "PUBLIC (no sensitive signals)"))

    # risk
    if record.risk:
        checks.append(_check(record.risk in ("low", "medium"), "Risk assessment",
                             f"risk = {record.risk.upper()}"
                             + (f" (score {record.risk_score})" if record.risk_score else "")))

    # policy
    if record.policy_version:
        checks.append(_check(record.decision != "deny", "Policy matched",
                             f"{record.policy_version} · rule {record.policy_rule} "
                             f"→ {record.decision.upper()}"))

    # human oversight
    oversight = record.human_oversight
    if oversight == "not_required":
        checks.append(_check(True, "Human oversight", "not required by policy"))
    elif oversight == "approved":
        checks.append(_check(True, "Human oversight",
                             f"approved by {record.reviewer or 'reviewer'}"))
    elif oversight == "denied":
        checks.append(_check(False, "Human oversight",
                             f"denied by {record.reviewer or 'reviewer'}"))
    elif oversight == "required":
        checks.append(_check(False, "Human oversight",
                             "required but not yet granted (pending approval)"))
    elif oversight == "bypassed":
        checks.append(_check(False, "Human oversight",
                             "REQUIRED but bypassed — governance violation"))

    # approval evidence
    if record.approval_id:
        approval = db.get(ApprovalRequest, record.approval_id)
        if approval is not None:
            checks.append(_check(approval.status == "approved", "Approval record",
                                 f"{approval.id[:8]} · status {approval.status} "
                                 f"· bound to args_hash {approval.args_hash[:12]}…"))

    verdict = {
        "allow": "ALLOWED",
        "deny": "BLOCKED",
        "require_approval": "APPROVAL REQUIRED",
        "require_human_review": "HUMAN REVIEW REQUIRED",
        "block_deployment": "DEPLOYMENT BLOCKED",
    }.get(record.decision, record.decision.upper())

    return {
        "decision_id": record.id,
        "question": _question(record),
        "verdict": verdict,
        "reason": record.reason,
        "checks": checks,
        "passed": sum(1 for c in checks if c["ok"]),
        "total": len(checks),
    }


def _question(record: DecisionRecord) -> str:
    if record.decision == "deny":
        return f"Why was '{record.action}' blocked?"
    if record.decision in ("require_approval", "require_human_review"):
        return f"Why did '{record.action}' require human oversight?"
    return f"Why was '{record.action}' allowed?"
