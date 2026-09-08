"""
AI System Registry API (IDENTITY plane).

    POST   /v1/systems                       register an AI system
    GET    /v1/systems                       list / search the registry
    GET    /v1/systems/{system_id}           inspect (full governed state)
    PATCH  /v1/systems/{system_id}           governed update (snapshots a version)
    POST   /v1/systems/{system_id}/lifecycle activate | suspend | archive | block | unblock
    GET    /v1/systems/{system_id}/versions  version history (newest first)
    GET    /v1/systems/{system_id}/versions/{version}  one snapshot

Every mutation is tenant-scoped and produces an immutable version snapshot —
the registry is the audit root for "what was this system, when, changed by
whom".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.governance import engine as governance_engine
from helios.governance import systems as registry
from helios.governance.policy import GovernanceSubject
from helios.models import ApiKey, ApprovalRequest
from helios.security import get_api_key
from helios.web.actions import hash_args

router = APIRouter(prefix="/v1/systems", tags=["systems"])


# --- schemas ---------------------------------------------------------------


class SystemIn(BaseModel):
    system_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=255)
    owner: str = Field(min_length=1, max_length=255)
    organization: str | None = None
    purpose: str = ""
    description: str | None = None
    environment: str = Field(default="dev", pattern="^(dev|staging|production)$")
    autonomy_level: int | str = Field(default=1)
    risk_class: str = Field(default="MEDIUM", pattern="^(?i:LOW|MEDIUM|HIGH|CRITICAL)$")
    models: list[dict] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    data_classes: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    oversight: dict = Field(default_factory=dict)
    deployment: dict = Field(default_factory=dict)
    lifecycle: str = Field(default="draft",
                           pattern="^(draft|active|suspended|archived|blocked)$")
    author: str | None = None


class SystemPatch(BaseModel):
    name: str | None = None
    owner: str | None = None
    organization: str | None = None
    purpose: str | None = None
    description: str | None = None
    environment: str | None = Field(default=None, pattern="^(dev|staging|production)$")
    autonomy_level: int | str | None = None
    risk_class: str | None = Field(default=None, pattern="^(?i:LOW|MEDIUM|HIGH|CRITICAL)$")
    models: list[dict] | None = None
    tools: list[str] | None = None
    data_classes: list[str] | None = None
    policies: list[str] | None = None
    oversight: dict | None = None
    deployment: dict | None = None
    author: str | None = None
    change_summary: str | None = None


class LifecycleIn(BaseModel):
    state: str = Field(pattern="^(draft|active|suspended|archived|blocked)$")
    reason: dict | None = None
    author: str | None = None
    # Payload-hash-bound approval satisfying a REQUIRE_APPROVAL deployment gate.
    approval_id: str | None = None


# --- deployment gate (POLICY plane enforcement) -------------------------------

POSTURE_FIELDS = ("environment", "autonomy_level", "risk_class")


def _deploy_action(system_id: str) -> str:
    return f"system.deploy:{system_id}"


def _deploy_posture(system) -> dict:
    """The exact posture an approval binds to; any change invalidates it."""
    return {
        "system_id": system.system_id,
        "version": system.version,
        "environment": system.environment,
        "autonomy_level": system.autonomy_level,
        "risk_class": system.risk_class,
    }


def _gate_activation(db: Session, api_key: ApiKey, system, approval_id: str | None):
    """
    Evaluate the deployment gate for activating `system`.

    Returns (outcome, extra):
      outcome "allow"             -> activation may proceed
      outcome "blocked"           -> governance blocked deployment (system is
                                     transitioned to blocked with the reason)
      outcome "approval_required" -> a human must approve the exact posture;
                                     extra carries the approval request
    """
    decision = governance_engine.check_deployment(db, system, candidate_state="active")

    if decision.decision in ("block_deployment", "deny"):
        rule_ids = [m["rule_id"] for m in decision.matched_rules]
        registry.set_lifecycle(
            db, system, "blocked",
            reason={
                "policy": decision.policy_versions,
                "rule_id": rule_ids[0] if rule_ids else "default",
                "detail": decision.reason,
                "decision": decision.to_dict(),
            },
            author="governance-engine",
        )
        return "blocked", {"governance": decision.to_dict()}

    if decision.decision in ("require_approval", "require_human_review"):
        action = _deploy_action(system.system_id)
        posture = _deploy_posture(system)
        posture["rules"] = [
            {"set": m["set"], "version": m["version"], "rule_id": m["rule_id"]}
            for m in decision.matched_rules
        ]
        posture_hash = hash_args(action, posture)

        if approval_id:
            approval = db.get(ApprovalRequest, approval_id)
            valid = (
                approval is not None
                and approval.tenant_id == api_key.tenant_id
                and approval.action == action
                and approval.args_hash == posture_hash
                and approval.status == "approved"
            )
            if valid:
                return "allow", {"governance": decision.to_dict(),
                                 "approval_id": approval.id}
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "approval does not authorize this deployment posture "
                             "(wrong id, not approved, or posture changed since approval)",
                    "governance": decision.to_dict(),
                },
            )

        approved = (
            db.query(ApprovalRequest)
            .filter(ApprovalRequest.tenant_id == api_key.tenant_id,
                    ApprovalRequest.action == action,
                    ApprovalRequest.args_hash == posture_hash,
                    ApprovalRequest.status == "approved")
            .first()
        )
        if approved is not None:
            return "allow", {"governance": decision.to_dict(),
                             "approval_id": approved.id}

        pending = (
            db.query(ApprovalRequest)
            .filter(ApprovalRequest.tenant_id == api_key.tenant_id,
                    ApprovalRequest.action == action,
                    ApprovalRequest.args_hash == posture_hash,
                    ApprovalRequest.status == "pending")
            .first()
        )
        if pending is None:
            pending = ApprovalRequest(
                tenant_id=api_key.tenant_id,
                action=action,
                args_hash=posture_hash,
                risk="high" if system.environment == "production" else "medium",
                decision_kind=("review" if decision.decision == "require_human_review"
                               else "approval"),
                system_id=system.system_id,
                summary={
                    "kind": "system_deployment",
                    "system_id": system.system_id,
                    "system_version": system.version,
                    "reason": decision.reason,
                    "explanation": decision.explanation,
                    "matched_rules": decision.matched_rules,
                    "posture": posture,
                },
            )
            db.add(pending)
            db.commit()
        return "approval_required", {
            "governance": decision.to_dict(),
            "approval_id": pending.id,
            "detail": "deployment requires human oversight — approve the request "
                      "and re-submit activation with approval_id",
        }

    return "allow", {"governance": decision.to_dict()}


# --- helpers ---------------------------------------------------------------


def _system_or_404(db: Session, api_key: ApiKey, system_id: str):
    try:
        return registry.get_system_or_raise(db, api_key.tenant_id, system_id)
    except registry.RegistryError:
        raise HTTPException(status_code=404, detail=f"ai system '{system_id}' not found")


def _version_dict(row) -> dict:
    return {
        "id": row.id,
        "system_id": row.system_id,
        "version": row.version,
        "snapshot": row.snapshot,
        "change_summary": row.change_summary,
        "author": row.author,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# --- routes ----------------------------------------------------------------


@router.post("", status_code=201)
def create_system(
    payload: SystemIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude_none=True)
    lifecycle = data.pop("lifecycle", "draft")
    if lifecycle == "blocked":
        raise HTTPException(status_code=400,
                            detail="systems cannot be registered as blocked")

    # Registering directly as `active` must pass the deployment gate; the
    # approval flow lives on the lifecycle endpoint, so a gated posture is
    # refused here with the full explanation (register as draft instead).
    if lifecycle == "active":
        from helios.governance.autonomy import parse_level

        subject = GovernanceSubject(
            kind="deployment",
            tenant_id=api_key.tenant_id,
            system_id=data["system_id"],
            environment=data.get("environment", "dev"),
            autonomy_level=parse_level(data.get("autonomy_level", 1)),
            risk_class=str(data.get("risk_class", "MEDIUM")).upper(),
            lifecycle="active",
            owner=data.get("owner"),
        )
        decision = governance_engine.evaluate_subject(db, api_key.tenant_id, subject)
        if decision.decision != "allow":
            raise HTTPException(status_code=400, detail={
                "error": f"deployment gate: {decision.reason}",
                "governance": decision.to_dict(),
                "hint": "register as draft and use POST /v1/systems/{id}/lifecycle "
                        "to activate through the approval flow",
            })

    try:
        system = registry.create_system(
            db, api_key.tenant_id, data,
            author=data.get("author"), lifecycle=lifecycle,
        )
    except ValueError as exc:  # RegistryError (ValueError subclass) + vocab validation
        raise HTTPException(status_code=400, detail=str(exc))
    return registry.system_to_dict(system)


@router.get("")
def list_systems(
    q: str | None = Query(default=None),
    environment: str | None = Query(default=None, pattern="^(dev|staging|production)$"),
    lifecycle: str | None = Query(default=None),
    risk_class: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    rows = registry.search_systems(
        db, api_key.tenant_id,
        query=q, environment=environment, lifecycle=lifecycle,
        risk_class=risk_class, owner=owner, limit=limit,
    )
    return {
        "count": len(rows),
        "systems": [registry.system_to_dict(s) for s in rows],
    }


@router.get("/{system_id}")
def get_system(
    system_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    return registry.system_to_dict(_system_or_404(db, api_key, system_id))


@router.patch("/{system_id}")
def update_system(
    system_id: str,
    payload: SystemPatch,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    system = _system_or_404(db, api_key, system_id)
    changes = payload.model_dump(exclude_none=True, exclude={"author", "change_summary"})

    # Posture changes on an ACTIVE (deployed) system re-run the deployment
    # gate against the merged posture: no invisible production governance
    # changes. If the new posture is gated, the change is refused with the
    # explanation — suspend, change, then re-activate through the flow.
    if system.lifecycle == "active" and any(f in changes for f in POSTURE_FIELDS):
        from helios.governance.autonomy import parse_level

        merged = GovernanceSubject(
            kind="deployment",
            tenant_id=api_key.tenant_id,
            system_id=system.system_id,
            environment=changes.get("environment", system.environment),
            autonomy_level=parse_level(changes.get("autonomy_level",
                                                  system.autonomy_level)),
            risk_class=str(changes.get("risk_class", system.risk_class)).upper(),
            lifecycle="active",
            owner=changes.get("owner", system.owner),
        )
        decision = governance_engine.evaluate_subject(db, api_key.tenant_id, merged)
        if decision.decision != "allow":
            raise HTTPException(status_code=409, detail={
                "error": f"deployment gate: {decision.reason} — change refused "
                         "while the system is active",
                "governance": decision.to_dict(),
                "hint": "suspend the system, apply the change, then activate "
                        "through the approval flow",
            })

    try:
        system = registry.update_system(
            db, system, changes,
            author=payload.author, change_summary=payload.change_summary,
        )
    except ValueError as exc:  # RegistryError (ValueError subclass) + vocab validation
        raise HTTPException(status_code=400, detail=str(exc))
    return registry.system_to_dict(system)


@router.post("/{system_id}/lifecycle")
def set_lifecycle(
    system_id: str,
    payload: LifecycleIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    system = _system_or_404(db, api_key, system_id)

    extra: dict = {}
    if payload.state == "active":
        outcome, extra = _gate_activation(db, api_key, system, payload.approval_id)
        if outcome == "blocked":
            db.refresh(system)
            data = registry.system_to_dict(system)
            data["governance"] = extra["governance"]
            return data
        if outcome == "approval_required":
            data = registry.system_to_dict(system)
            data["governance"] = extra["governance"]
            data["approval_required"] = {
                "approval_id": extra["approval_id"],
                "action": _deploy_action(system.system_id),
                "detail": extra["detail"],
            }
            return data

    try:
        system = registry.set_lifecycle(
            db, system, payload.state, reason=payload.reason, author=payload.author,
        )
    except ValueError as exc:  # RegistryError (ValueError subclass) + vocab validation
        raise HTTPException(status_code=400, detail=str(exc))
    data = registry.system_to_dict(system)
    if extra.get("governance"):
        data["governance"] = extra["governance"]
    if extra.get("approval_id"):
        data["approval_id"] = extra["approval_id"]
    return data


@router.post("/{system_id}/archive")
def archive_system(
    system_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    system = _system_or_404(db, api_key, system_id)
    try:
        system = registry.set_lifecycle(db, system, "archived", author="api")
    except ValueError as exc:  # RegistryError (ValueError subclass) + vocab validation
        raise HTTPException(status_code=400, detail=str(exc))
    return registry.system_to_dict(system)


@router.get("/{system_id}/lineage")
def system_lineage(
    system_id: str,
    limit_runs: int = Query(default=20, ge=1, le=100),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Aggregated, event-backed data lineage across the system's recent runs."""
    from helios.governance.lineage import build_system_lineage

    system = _system_or_404(db, api_key, system_id)
    return build_system_lineage(db, api_key.tenant_id, system.system_id,
                                limit_runs=limit_runs)


@router.post("/{system_id}/drift/baseline")
def create_drift_baseline(
    system_id: str,
    payload: dict | None = None,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Snapshot the system's current governance metrics as the drift baseline."""
    from helios.governance.drift import create_baseline

    system = _system_or_404(db, api_key, system_id)
    payload = payload or {}
    row = create_baseline(db, api_key.tenant_id, system.system_id,
                          by=payload.get("by"), label=payload.get("label", "baseline"))
    return {
        "id": row.id,
        "system_id": row.system_id,
        "label": row.label,
        "window": row.window,
        "metrics": row.metrics,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("/{system_id}/drift/check")
def check_drift(
    system_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Deterministic comparison of current metrics vs the latest baseline."""
    from helios.governance.drift import check_drift as _check

    system = _system_or_404(db, api_key, system_id)
    return _check(db, api_key.tenant_id, system.system_id)


@router.get("/{system_id}/drift")
def drift_state(
    system_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Baselines + signals for one system (dashboard view)."""
    from helios.governance.drift import _signal_to_dict, latest_baseline, open_signals
    from helios.models import DriftBaseline

    system = _system_or_404(db, api_key, system_id)
    baselines = (
        db.query(DriftBaseline)
        .filter(DriftBaseline.tenant_id == api_key.tenant_id,
                DriftBaseline.system_id == system.system_id)
        .order_by(DriftBaseline.created_at.desc())
        .limit(10)
        .all()
    )
    signals = open_signals(db, api_key.tenant_id, system.system_id)
    return {
        "system_id": system.system_id,
        "latest_baseline": baselines[0].metrics if baselines else None,
        "baselines": [
            {"id": b.id, "label": b.label, "window": b.window,
             "created_at": b.created_at.isoformat() if b.created_at else None}
            for b in baselines
        ],
        "signals": signals,
    }


@router.get("/{system_id}/audit")
def system_audit(
    system_id: str,
    window: int = Query(default=50, ge=1, le=500),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Full governance evidence report (alias of GET /v1/audit/{system_id})."""
    from helios.governance.audit import build_audit_report

    system = _system_or_404(db, api_key, system_id)
    return build_audit_report(db, api_key.tenant_id, system.system_id,
                              window=window)


@router.get("/{system_id}/oversight")
def system_oversight(
    system_id: str,
    limit: int = Query(default=500, ge=1, le=2000),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Human-oversight compliance for one AI system: where humans were involved,
    where policy required them, declared-requirement gaps, bypass flags, and
    approval-queue stats — all derived from DecisionRecords + approvals.
    """
    from helios.governance.oversight import analyze_system

    system = _system_or_404(db, api_key, system_id)
    return analyze_system(db, api_key.tenant_id, system.system_id,
                          system=system, limit=limit)


@router.get("/{system_id}/versions")
def list_versions(
    system_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    system = _system_or_404(db, api_key, system_id)
    rows = registry.list_versions(db, system)
    return {"system_id": system.system_id, "versions": [_version_dict(r) for r in rows]}


@router.get("/{system_id}/versions/{version}")
def get_version(
    system_id: str,
    version: int,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    system = _system_or_404(db, api_key, system_id)
    row = registry.get_version(db, system, version)
    if row is None:
        raise HTTPException(status_code=404, detail=f"version {version} not found")
    return _version_dict(row)
