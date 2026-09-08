"""
GovernanceEngine — the POLICY plane facade.

Composes the governance decision for a subject from:

    1. the built-in default policy set (canonical autonomy ceiling)
    2. every ACTIVE persisted policy set of the tenant (deterministic order:
       name, then version), scoped by system/environment

and exposes the three enforcement entry points used by the rest of HELIOS:

    check_deployment(system)            -> system registration / activation gate
    check_model_call(...)               -> agent runtime model preflight
    check_tool_action(...)              -> broker overlay (escalation only)

Every decision is a `GovernanceDecision` with a full explanation generated
from actual policy + subject state, serializable straight onto evidence.
Nothing here executes anything or touches the network — pure, deterministic,
replayable evaluation over DB state.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.governance.policy import (
    ALLOW,
    BLOCK_DEPLOYMENT,
    DEFAULT_GOVERNANCE_POLICY,
    DENY,
    REQUIRE_APPROVAL,
    REQUIRE_HUMAN_REVIEW,
    GovernanceDecision,
    GovernancePolicySet,
    GovernanceSubject,
    merge_decisions,
    validate_rule,
)
from helios.models import AiSystem
from helios.models import GovernancePolicySet as PolicySetRow


class PolicySetError(ValueError):
    """Invalid policy set operation."""


# --- persisted policy set management -----------------------------------------


def create_policy_set(
    db: Session,
    tenant_id: str,
    data: dict,
    *,
    status: str = "candidate",
) -> PolicySetRow:
    """Create a policy set (candidate by default — nothing enforces until active)."""
    name = str(data.get("name") or "").strip()
    version = str(data.get("version") or "").strip()
    if not name or not version:
        raise PolicySetError("policy set requires name and version")
    if status not in ("candidate", "active"):
        raise PolicySetError("status must be 'candidate' or 'active'")

    rules = data.get("rules") or []
    if not isinstance(rules, list):
        raise PolicySetError("rules must be a list")
    errors: list[str] = []
    seen_ids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            errors.append(f"rule must be an object, got {type(rule).__name__}")
            continue
        errors.extend(validate_rule(rule))
        rid = rule.get("id")
        if rid in seen_ids:
            errors.append(f"duplicate rule id '{rid}'")
        seen_ids.add(rid)
    if errors:
        raise PolicySetError("; ".join(errors[:8]))

    default_effect = data.get("default_effect", ALLOW)
    try:
        policy = GovernancePolicySet.from_dict({
            "name": name, "version": version, "rules": rules,
            "description": data.get("description", ""),
            "default_effect": default_effect,
            "scope": data.get("scope") or {},
        })
    except ValueError as exc:
        raise PolicySetError(str(exc))

    existing = (
        db.query(PolicySetRow)
        .filter(PolicySetRow.tenant_id == tenant_id,
                PolicySetRow.name == name, PolicySetRow.version == version)
        .first()
    )
    if existing is not None:
        raise PolicySetError(f"policy set '{name}@{version}' already exists")

    row = PolicySetRow(
        tenant_id=tenant_id,
        name=name,
        version=version,
        description=policy.description,
        rules=policy.rules,
        default_effect=policy.default_effect,
        scope=policy.scope,
        status=status,
        author=data.get("author"),
    )
    db.add(row)
    db.flush()
    if status == "active":
        _archive_same_name(db, tenant_id, name, keep_id=row.id)
        from datetime import datetime, timezone
        row.activated_at = datetime.now(timezone.utc)
    db.commit()
    return row


def _archive_same_name(db: Session, tenant_id: str, name: str, *, keep_id: str) -> None:
    rows = (
        db.query(PolicySetRow)
        .filter(PolicySetRow.tenant_id == tenant_id,
                PolicySetRow.name == name,
                PolicySetRow.status == "active",
                PolicySetRow.id != keep_id)
        .all()
    )
    for row in rows:
        row.status = "archived"


def activate_policy_set(db: Session, row: PolicySetRow) -> PolicySetRow:
    """candidate/archived -> active; archives the previously active same-name set."""
    from datetime import datetime, timezone

    if row.status == "active":
        return row
    _archive_same_name(db, row.tenant_id, row.name, keep_id=row.id)
    row.status = "active"
    row.activated_at = datetime.now(timezone.utc)
    db.commit()
    return row


def archive_policy_set(db: Session, row: PolicySetRow) -> PolicySetRow:
    if row.status == "archived":
        return row
    row.status = "archived"
    db.commit()
    return row


def active_policy_sets(db: Session, tenant_id: str) -> list[GovernancePolicySet]:
    """Built-in default first, then tenant actives in deterministic order."""
    rows = (
        db.query(PolicySetRow)
        .filter(PolicySetRow.tenant_id == tenant_id, PolicySetRow.status == "active")
        .order_by(PolicySetRow.name, PolicySetRow.version)
        .all()
    )
    sets = [DEFAULT_GOVERNANCE_POLICY]
    sets.extend(
        GovernancePolicySet.from_dict({
            "name": r.name, "version": r.version, "rules": r.rules,
            "description": r.description, "default_effect": r.default_effect,
            "scope": r.scope or {},
        })
        for r in rows
    )
    return sets


def policy_set_to_dict(row: PolicySetRow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "version": row.version,
        "description": row.description,
        "rules": row.rules,
        "default_effect": row.default_effect,
        "scope": row.scope,
        "status": row.status,
        "author": row.author,
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# --- evaluation ------------------------------------------------------------------


def evaluate_subject(
    db: Session,
    tenant_id: str,
    subject: GovernanceSubject,
    *,
    extra_sets: list[GovernancePolicySet] | None = None,
) -> GovernanceDecision:
    """
    Evaluate a subject against the default set + all active tenant sets
    (+ optional extra candidate sets — used by dry-run and replay).

    Deterministic: same subject + same stored state => same decision.
    """
    subject.tenant_id = tenant_id
    sets = list(extra_sets or [])
    sets.extend(active_policy_sets(db, tenant_id))
    decisions = [s.evaluate(subject) for s in sets if s.applies_to(subject)]
    return merge_decisions(decisions)


def subject_from_system(system: AiSystem, kind: str = "deployment") -> GovernanceSubject:
    return GovernanceSubject(
        kind=kind,
        tenant_id=system.tenant_id,
        system_id=system.system_id,
        environment=system.environment,
        autonomy_level=system.autonomy_level,
        risk_class=system.risk_class,
        lifecycle=system.lifecycle,
        owner=system.owner,
    )


def check_deployment(db: Session, system: AiSystem,
                     *, extra_sets: list[GovernancePolicySet] | None = None,
                     candidate_state: str = "active") -> GovernanceDecision:
    """
    Deployment/activation gate for a registered system.

    Evaluated against the posture the system WOULD have once deployed
    (lifecycle=candidate_state), so "may this system go/stay active?" is
    answerable before the transition happens.
    """
    subject = subject_from_system(system, kind="deployment")
    subject.lifecycle = candidate_state
    decision = evaluate_subject(db, system.tenant_id, subject, extra_sets=extra_sets)
    if decision.decision == BLOCK_DEPLOYMENT:
        decision.reason = f"deployment blocked: {decision.reason}"
    return decision


def check_model_call(
    db: Session,
    tenant_id: str,
    *,
    system: AiSystem | None,
    model: dict,
    data_class: str | None,
    environment: str,
    extra_sets: list[GovernancePolicySet] | None = None,
) -> GovernanceDecision:
    subject = GovernanceSubject(
        kind="model_call",
        tenant_id=tenant_id,
        system_id=system.system_id if system else None,
        environment=environment,
        autonomy_level=system.autonomy_level if system else None,
        risk_class=system.risk_class if system else None,
        lifecycle=system.lifecycle if system else None,
        data_class=data_class,
        model=model,
        owner=system.owner if system else None,
    )
    return evaluate_subject(db, tenant_id, subject, extra_sets=extra_sets)


def check_tool_action(
    db: Session,
    tenant_id: str,
    *,
    system: AiSystem | None,
    tool: str,
    capability: str,
    action_risk: str,
    environment: str,
    autonomy_level: int | None = None,
    data_class: str | None = None,
    extra_sets: list[GovernancePolicySet] | None = None,
) -> GovernanceDecision:
    subject = GovernanceSubject(
        kind="tool_action",
        tenant_id=tenant_id,
        system_id=system.system_id if system else None,
        environment=environment,
        autonomy_level=autonomy_level if autonomy_level is not None
        else (system.autonomy_level if system else None),
        risk_class=system.risk_class if system else None,
        lifecycle=system.lifecycle if system else None,
        data_class=data_class,
        tool=tool,
        capability=capability,
        action_risk=action_risk,
        owner=system.owner if system else None,
    )
    return evaluate_subject(db, tenant_id, subject, extra_sets=extra_sets)


# --- decision helpers for enforcement points ---------------------------------------


def decision_rank(decision: str) -> int:
    from helios.governance.policy import SEVERITY_OF_EFFECT
    return SEVERITY_OF_EFFECT.get(decision, 0)


__all__ = [
    "ALLOW", "DENY", "REQUIRE_APPROVAL", "REQUIRE_HUMAN_REVIEW", "BLOCK_DEPLOYMENT",
    "GovernanceDecision", "GovernanceSubject", "GovernancePolicySet",
    "PolicySetError",
    "create_policy_set", "activate_policy_set", "archive_policy_set",
    "active_policy_sets", "policy_set_to_dict",
    "evaluate_subject", "subject_from_system",
    "check_deployment", "check_model_call", "check_tool_action",
]
