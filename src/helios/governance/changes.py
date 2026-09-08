"""
AI Change Management (ASSURANCE plane).

    CURRENT -> CANDIDATE -> REPLAY -> EVALUATION -> RISK COMPARISON
            -> HUMAN REVIEW -> APPROVED -> DEPLOYED   (+ REJECTED, ROLLED_BACK)

No production governance change happens invisibly. A ChangeRecord carries:
author, timestamps, previous + candidate snapshots, replay/evaluation/risk
evidence, a payload-hash-bound approval, deployment state, and a rollback
target. Deploy applies the candidate through the SAME services the manual
paths use (policy activation, system update, model update) — so every deploy
inherits their versioning and governance events. Rollback re-applies the
previous snapshot and opens its own (rollback) change record: rollbacks are
changes too.

Change families:
    policy_set   candidate = a GovernancePolicySet document; deploy creates
                 the new version and activates it (archiving the previous
                 active same-name set); rollback re-activates the previous.
    system       candidate = partial system fields (models, tools,
                 data_classes, policies, oversight, deployment incl. prompt /
                 system_instruction, autonomy_level, environment...);
                 deploy applies via update_system (versioned snapshot);
                 rollback restores the recorded snapshot fields.
    model_asset  candidate = registry metadata and/or approval_status;
                 deploy applies via update_model / set_approval;
                 rollback restores previous values.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from helios.broker.trace import TraceRecorder
from helios.models import ApprovalRequest, ChangeRecord
from helios.web.actions import hash_args

CHANGE_TYPES = (
    "model", "model_version", "prompt", "policy", "tool", "dataset",
    "system_instruction", "workflow", "agent_config", "rollback",
)

STATES = (
    "candidate", "replay", "evaluation", "risk_comparison",
    "human_review", "approved", "deployed", "rejected", "rolled_back",
)

TRANSITIONS = {
    "candidate": ("replay", "rejected"),
    "replay": ("evaluation", "rejected"),
    "evaluation": ("risk_comparison", "rejected"),
    "risk_comparison": ("human_review", "rejected"),
    "human_review": ("approved", "rejected"),
    "approved": ("deployed", "rejected"),
    "deployed": ("rolled_back",),
}

FAMILIES = ("policy_set", "system", "model_asset")


class ChangeError(ValueError):
    """Invalid change operation."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _history(change: ChangeRecord, to_state: str, by: str,
             note: str | None = None) -> list:
    history = list((change.evidence or {}).get("history") or [])
    history.append({"from": change.state, "to": to_state, "by": by,
                    "at": _now(), "note": note})
    return history


def _set_state(db: Session, change: ChangeRecord, to_state: str, by: str,
               note: str | None = None) -> None:
    if to_state not in TRANSITIONS.get(change.state, ()):
        raise ChangeError(
            f"illegal transition {change.state} -> {to_state} "
            f"(allowed: {TRANSITIONS.get(change.state, ())})")
    change.evidence = dict(change.evidence or {},
                           history=_history(change, to_state, by, note))
    change.state = to_state
    db.commit()


def _record_event(db: Session, tenant_id: str, change: ChangeRecord,
                  event: str, detail: dict) -> None:
    recorder = TraceRecorder(db, tenant_id=tenant_id, system_id=change.system_id)
    recorder.record(
        "governance_change", f"{change.change_type}:{change.id[:8]}",
        {"event": event, "change_id": change.id, "state": change.state,
         "author": change.author, **detail},
    )


# --- creation -------------------------------------------------------------------


def _resolve_previous(db: Session, tenant_id: str, target: dict) -> dict:
    family = target.get("family")
    if family == "policy_set":
        from helios.governance import engine as governance_engine
        from helios.models import GovernancePolicySet as PolicySetRow

        name = target.get("name")
        if not name:
            raise ChangeError("policy_set target requires 'name'")
        active = (
            db.query(PolicySetRow)
            .filter(PolicySetRow.tenant_id == tenant_id,
                    PolicySetRow.name == name,
                    PolicySetRow.status == "active")
            .order_by(PolicySetRow.version.desc())
            .first()
        )
        if active is None:
            return {"active": None}
        return {"active": governance_engine.policy_set_to_dict(active)}
    if family == "system":
        from helios.governance import systems as system_registry

        system = system_registry.get_system(db, tenant_id,
                                            target.get("system_id", ""))
        if system is None:
            raise ChangeError(f"system '{target.get('system_id')}' not found")
        return system_registry.snapshot(system)
    if family == "model_asset":
        from helios.governance import models_registry
        from helios.models import ModelAsset

        asset = (
            db.query(ModelAsset)
            .filter(ModelAsset.id == target.get("model_asset_id"),
                    ModelAsset.tenant_id == tenant_id)
            .first()
        )
        if asset is None:
            raise ChangeError("model asset not found")
        return models_registry.model_to_dict(asset)
    raise ChangeError(f"target.family must be one of {FAMILIES}")


def create_change(db: Session, tenant_id: str, data: dict) -> ChangeRecord:
    change_type = data.get("change_type")
    if change_type not in CHANGE_TYPES:
        raise ChangeError(f"change_type must be one of {CHANGE_TYPES}")
    author = data.get("author")
    if not author:
        raise ChangeError("changes require an author (accountability)")
    target = data.get("target") or {}
    candidate = data.get("candidate")
    if not isinstance(candidate, dict) or not candidate:
        raise ChangeError("candidate must be a non-empty object")

    previous = _resolve_previous(db, tenant_id, target)

    change = ChangeRecord(
        tenant_id=tenant_id,
        system_id=target.get("system_id"),
        change_type=change_type,
        title=(data.get("title") or f"{change_type} change")[:255],
        description=data.get("description"),
        author=author,
        target=target,
        previous=previous,
        candidate=candidate,
        state="candidate",
        evidence={"history": [{"from": None, "to": "candidate", "by": author,
                               "at": _now(), "note": data.get("note")}]},
    )
    db.add(change)
    db.commit()
    _record_event(db, tenant_id, change, "change_created",
                  {"change_type": change_type, "target": target})
    return change


# --- evidence steps ----------------------------------------------------------------


def attach_replay(db: Session, change: ChangeRecord, report: dict,
                  by: str) -> ChangeRecord:
    """Store the replay report and advance candidate -> replay."""
    if change.state != "candidate":
        raise ChangeError(f"replay evidence attaches in state 'candidate' "
                          f"(current: {change.state})")
    change.evidence = dict(change.evidence or {}, replay=report)
    db.commit()
    _set_state(db, change, "replay", by, note="replay report attached")
    return change


def attach_evaluation(db: Session, change: ChangeRecord, evaluation: dict,
                      by: str) -> ChangeRecord:
    if change.state != "replay":
        raise ChangeError(f"evaluation attaches in state 'replay' "
                          f"(current: {change.state})")
    change.evidence = dict(
        change.evidence or {},
        evaluation={"score": evaluation.get("score"),
                    "passed": evaluation.get("passed"),
                    "status": evaluation.get("status"),
                    "metrics": {
                        name: {"score": r.get("score"), "passed": r.get("passed")}
                        for name, r in (evaluation.get("results") or {}).items()
                    },
                    "evidence": evaluation.get("evidence")},
    )
    db.commit()
    _set_state(db, change, "evaluation", by, note="evaluation attached")
    return change


def build_risk_comparison(db: Session, change: ChangeRecord,
                          by: str) -> ChangeRecord:
    """Deterministic risk comparison derived from the attached evidence."""
    if change.state != "evaluation":
        raise ChangeError(f"risk comparison builds in state 'evaluation' "
                          f"(current: {change.state})")
    replay = (change.evidence or {}).get("replay") or {}
    evaluation = (change.evidence or {}).get("evaluation") or {}
    summary = replay.get("summary") or {}
    comparison = {
        "tool_layer": summary.get("tool_layer", {}),
        "governance_layer": summary.get("governance_layer", {}),
        "changed_decisions": summary.get("changed_decisions", 0),
        "current_evaluation": {"score": evaluation.get("score"),
                               "passed": evaluation.get("passed")},
        "risk_flags": [],
    }
    gov = summary.get("governance_layer") or {}
    tool = summary.get("tool_layer") or {}
    if gov.get("newly_allowed", 0) > 0:
        comparison["risk_flags"].append(
            f"{gov['newly_allowed']} previously-restricted decisions would be "
            "ALLOWED under the candidate")
    if gov.get("newly_gated", 0) > 0:
        comparison["risk_flags"].append(
            f"+{gov['newly_gated']} actions would newly REQUIRE APPROVAL under "
            "candidate governance (human-oversight load)")
    if gov.get("newly_blocked", 0) > 0:
        comparison["risk_flags"].append(
            f"+{gov['newly_blocked']} actions would be newly BLOCKED under "
            "candidate governance (possible false-positive blocks)")
    if (tool.get("denied_delta") or 0) > 0:
        comparison["risk_flags"].append(
            f"+{tool['denied_delta']} actions would be newly DENIED "
            "(possible false-positive blocks)")
    if (tool.get("approval_delta") or 0) > 0:
        comparison["risk_flags"].append(
            f"+{tool['approval_delta']} actions would newly REQUIRE APPROVAL "
            "(human-oversight load)")
    if evaluation.get("passed") is False:
        comparison["risk_flags"].append(
            "current governance evaluation is failing — fix before deploying")
    change.evidence = dict(change.evidence or {}, risk_comparison=comparison)
    db.commit()
    _set_state(db, change, "risk_comparison", by,
               note=f"{len(comparison['risk_flags'])} risk flag(s)")
    return change


# --- human review -------------------------------------------------------------------


def change_digest(change: ChangeRecord) -> str:
    """The exact content a review approval binds to."""
    return hash_args(f"change:{change.id}", {
        "previous": change.previous,
        "candidate": change.candidate,
        "replay_summary": ((change.evidence or {}).get("replay") or {}).get("summary"),
        "risk_comparison": (change.evidence or {}).get("risk_comparison"),
    })


def submit_for_review(db: Session, change: ChangeRecord, by: str) -> ChangeRecord:
    """Advance to human_review and create the payload-bound approval request."""
    if change.state != "risk_comparison":
        raise ChangeError(f"review submission requires state 'risk_comparison' "
                          f"(current: {change.state})")
    _set_state(db, change, "human_review", by, note="submitted for human review")

    action = f"change:{change.id}"
    digest = change_digest(change)
    pending = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.tenant_id == change.tenant_id,
                ApprovalRequest.action == action,
                ApprovalRequest.args_hash == digest,
                ApprovalRequest.status == "pending")
        .first()
    )
    if pending is None:
        pending = ApprovalRequest(
            tenant_id=change.tenant_id,
            action=action,
            args_hash=digest,
            risk="high",
            decision_kind="review",
            system_id=change.system_id,
            summary={
                "kind": "ai_change_review",
                "change_id": change.id,
                "change_type": change.change_type,
                "title": change.title,
                "author": change.author,
                "previous": change.previous,
                "candidate": change.candidate,
                "replay_summary": ((change.evidence or {}).get("replay") or {}).get("summary"),
                "risk_comparison": (change.evidence or {}).get("risk_comparison"),
            },
        )
        db.add(pending)
        db.flush()  # assign the id before referencing it
    change.approval_id = pending.id
    db.commit()
    return change


def approve_change(db: Session, change: ChangeRecord, approval_id: str,
                   by: str) -> ChangeRecord:
    if change.state != "human_review":
        raise ChangeError(f"approval requires state 'human_review' "
                          f"(current: {change.state})")
    approval = db.get(ApprovalRequest, approval_id or "")
    if (approval is None or approval.tenant_id != change.tenant_id
            or approval.action != f"change:{change.id}"
            or approval.status != "approved"):
        raise ChangeError("no approved request found for this change")
    if approval.args_hash != change_digest(change):
        raise ChangeError(
            "approval does not bind to the current change content "
            "(evidence changed after review)")
    _set_state(db, change, "approved", by, note=f"approval {approval.id}")
    return change


def reject_change(db: Session, change: ChangeRecord, by: str,
                  reason: str) -> ChangeRecord:
    if not reason:
        raise ChangeError("rejection requires a reason")
    _set_state(db, change, "rejected", by, note=reason)
    _record_event(db, change.tenant_id, change, "change_rejected",
                  {"by": by, "reason": reason})
    return change


# --- deployment + rollback -----------------------------------------------------------


def _apply_policy_set(db: Session, change: ChangeRecord, by: str) -> dict:
    from helios.governance import engine as governance_engine

    candidate = dict(change.candidate)
    name = change.target.get("name") or candidate.get("name")
    version = candidate.get("version")
    if not name or not version:
        raise ChangeError("policy candidate requires name and version")
    existing_active = (change.previous or {}).get("active") or {}
    try:
        row = governance_engine.create_policy_set(
            db, change.tenant_id,
            {"name": name, "version": version,
             "rules": candidate.get("rules") or [],
             "description": candidate.get("description", ""),
             "default_effect": candidate.get("default_effect", "allow"),
             "scope": candidate.get("scope") or {},
             "author": by},
            status="active",
        )
    except ValueError as exc:
        raise ChangeError(str(exc))
    return {
        "applied": {"family": "policy_set", "policy_set_id": row.id,
                    "name": name, "version": version},
        "rollback_target": {
            "family": "policy_set", "name": name,
            "restore_version": existing_active.get("version"),
            "archive_version": version,
        },
    }


def _apply_system(db: Session, change: ChangeRecord, by: str) -> dict:
    from helios.governance import systems as system_registry

    system = system_registry.get_system(
        db, change.tenant_id, change.target.get("system_id", ""))
    if system is None:
        raise ChangeError("target system not found")
    candidate = dict(change.candidate)
    previous_snapshot = dict(change.previous or {})
    try:
        system_registry.update_system(
            db, system, candidate, author=by,
            change_summary=f"change {change.id} ({change.change_type})")
    except ValueError as exc:
        raise ChangeError(str(exc))
    restore = {k: previous_snapshot.get(k) for k in candidate
               if k in previous_snapshot}
    return {
        "applied": {"family": "system", "system_id": system.system_id,
                    "fields": sorted(candidate), "new_version": system.version},
        "rollback_target": {"family": "system",
                            "system_id": system.system_id,
                            "restore_fields": restore},
    }


def _apply_model_asset(db: Session, change: ChangeRecord, by: str) -> dict:
    from helios.governance import models_registry
    from helios.models import ModelAsset

    asset = (
        db.query(ModelAsset)
        .filter(ModelAsset.id == change.target.get("model_asset_id"),
                ModelAsset.tenant_id == change.tenant_id)
        .first()
    )
    if asset is None:
        raise ChangeError("target model asset not found")
    candidate = dict(change.candidate)
    status = candidate.pop("approval_status", None)
    if candidate:
        try:
            models_registry.update_model(db, asset, candidate)
        except ValueError as exc:
            raise ChangeError(str(exc))
    if status is not None:
        try:
            models_registry.set_approval(db, asset, status, by=by,
                                         reason=f"change {change.id}")
        except ValueError as exc:
            raise ChangeError(str(exc))
    previous = dict(change.previous or {})
    restore = {k: previous.get(k) for k in list(candidate) + (["approval_status"] if status else [])
               if k in previous}
    return {
        "applied": {"family": "model_asset", "model_asset_id": asset.id,
                    "fields": sorted(candidate) + (["approval_status"] if status else [])},
        "rollback_target": {"family": "model_asset",
                            "model_asset_id": asset.id,
                            "restore_fields": restore},
    }


_APPLIERS = {
    "policy_set": _apply_policy_set,
    "system": _apply_system,
    "model_asset": _apply_model_asset,
}


def deploy_change(db: Session, change: ChangeRecord, by: str) -> ChangeRecord:
    """Apply the candidate through the same services manual paths use."""
    if change.state != "approved":
        raise ChangeError(
            f"deployment requires state 'approved' (current: {change.state}) — "
            "no production governance change happens invisibly")
    family = (change.target or {}).get("family")
    applier = _APPLIERS.get(family)
    if applier is None:
        raise ChangeError(f"unknown target family '{family}'")
    result = applier(db, change, by)
    change.rollback_target = result["rollback_target"]
    change.evidence = dict(change.evidence or {}, deployment=result["applied"])
    change.deployed_at = datetime.now(timezone.utc)
    change.deployed_by = by
    _set_state(db, change, "deployed", by, note=f"applied {result['applied']}")
    _record_event(db, change.tenant_id, change, "change_deployed",
                  {"by": by, "applied": result["applied"]})
    return change


def rollback_change(db: Session, change: ChangeRecord, by: str,
                    reason: str | None = None) -> ChangeRecord:
    """
    Re-apply the previous snapshot and open a rollback ChangeRecord —
    rollbacks are governed changes too.
    """
    if change.state != "deployed":
        raise ChangeError(f"rollback requires state 'deployed' "
                          f"(current: {change.state})")
    target = dict(change.rollback_target or {})
    family = target.get("family")
    if family == "policy_set":
        from helios.governance import engine as governance_engine
        from helios.models import GovernancePolicySet as PolicySetRow

        restore_version = target.get("restore_version")
        archive_version = target.get("archive_version")
        if archive_version:
            row = (
                db.query(PolicySetRow)
                .filter(PolicySetRow.tenant_id == change.tenant_id,
                        PolicySetRow.name == target.get("name"),
                        PolicySetRow.version == archive_version)
                .first()
            )
            if row is not None:
                governance_engine.archive_policy_set(db, row)
        if restore_version:
            previous_row = (
                db.query(PolicySetRow)
                .filter(PolicySetRow.tenant_id == change.tenant_id,
                        PolicySetRow.name == target.get("name"),
                        PolicySetRow.version == restore_version)
                .first()
            )
            if previous_row is not None:
                governance_engine.activate_policy_set(db, previous_row)
        applied = {"restored_version": restore_version,
                   "archived_version": archive_version}
    elif family == "system":
        from helios.governance import systems as system_registry

        system = system_registry.get_system(
            db, change.tenant_id, target.get("system_id", ""))
        if system is None:
            raise ChangeError("rollback target system not found")
        restore_fields = target.get("restore_fields") or {}
        if not restore_fields:
            raise ChangeError("rollback target has no restorable fields")
        system_registry.update_system(
            db, system, restore_fields, author=by,
            change_summary=f"rollback of change {change.id}")
        applied = {"restored_fields": sorted(restore_fields),
                   "new_version": system.version}
    elif family == "model_asset":
        from helios.governance import models_registry
        from helios.models import ModelAsset

        asset = db.get(ModelAsset, target.get("model_asset_id", ""))
        if asset is None:
            raise ChangeError("rollback target model asset not found")
        restore_fields = dict(target.get("restore_fields") or {})
        status = restore_fields.pop("approval_status", None)
        if restore_fields:
            models_registry.update_model(db, asset, restore_fields)
        if status:
            models_registry.set_approval(db, asset, status, by=by,
                                         reason=f"rollback of change {change.id}")
        applied = {"restored_fields": sorted(restore_fields)
                   + (["approval_status"] if status else [])}
    else:
        raise ChangeError(f"unknown rollback family '{family}'")

    rollback_record = ChangeRecord(
        tenant_id=change.tenant_id,
        system_id=change.system_id,
        change_type="rollback",
        title=f"Rollback: {change.title}"[:255],
        description=reason or f"rollback of change {change.id}",
        author=by,
        target=dict(change.target or {}),
        previous={"state_before_rollback": change.candidate},
        candidate={"restored": change.previous, "applied": applied},
        state="deployed",
        evidence={
            "rollback_of": change.id,
            "history": [{"from": None, "to": "deployed", "by": by,
                         "at": _now(), "note": reason or "rollback"}],
            "deployment": applied,
        },
        deployed_at=datetime.now(timezone.utc),
        deployed_by=by,
    )
    db.add(rollback_record)
    _set_state(db, change, "rolled_back", by, note=reason or "rolled back")
    db.commit()
    _record_event(db, change.tenant_id, change, "change_rolled_back",
                  {"by": by, "rollback_record": rollback_record.id,
                   "applied": applied})
    return change


# --- serialization ---------------------------------------------------------------------


def change_to_dict(change: ChangeRecord) -> dict:
    return {
        "id": change.id,
        "system_id": change.system_id,
        "change_type": change.change_type,
        "title": change.title,
        "description": change.description,
        "author": change.author,
        "target": change.target,
        "previous": change.previous,
        "candidate": change.candidate,
        "state": change.state,
        "evidence": change.evidence,
        "approval_id": change.approval_id,
        "rollback_target": change.rollback_target,
        "deployed_at": change.deployed_at.isoformat() if change.deployed_at else None,
        "deployed_by": change.deployed_by,
        "created_at": change.created_at.isoformat() if change.created_at else None,
        "updated_at": change.updated_at.isoformat() if change.updated_at else None,
    }


def _json_default(obj):
    return str(obj)


def digest_of(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, default=_json_default)
