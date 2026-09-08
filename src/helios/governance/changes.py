"""
ASSURANCE PLANE — AI Change Management.

No production governance change happens invisibly. A change moves through:

    draft -> replayed -> in_review -> approved -> deployed (-> rolled_back)
                                   \-> rejected

Every change carries author, current + candidate state, evidence (replay
comparison + evaluation), and a rollback target. Policy changes can be
replayed against historical runs BEFORE deployment.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from helios.models import ChangeRecord


CHANGE_KINDS = ("policy", "model", "prompt", "tool", "config")
STATUSES = ("draft", "replayed", "in_review", "approved", "rejected",
            "deployed", "rolled_back")


class ChangeError(ValueError):
    pass


def create_change(db: Session, tenant_id: str, data: dict, author: str) -> ChangeRecord:
    kind = data.get("kind")
    if kind not in CHANGE_KINDS:
        raise ChangeError(f"kind must be one of {CHANGE_KINDS}")
    change = ChangeRecord(
        tenant_id=tenant_id,
        system_id=data.get("system_id"),
        kind=kind,
        title=data.get("title") or f"{kind} change",
        author=author,
        current=dict(data.get("current") or {}),
        candidate=dict(data.get("candidate") or {}),
        rollback_target=dict(data.get("current") or {}),
        status="draft",
    )
    db.add(change)
    db.commit()
    return change


def attach_evidence(db: Session, change: ChangeRecord, key: str, value: dict) -> ChangeRecord:
    evidence = dict(change.evidence or {})
    evidence[key] = value
    change.evidence = evidence
    if key == "replay":
        change.status = "replayed"
    db.commit()
    return change


def transition(db: Session, change: ChangeRecord, status: str, actor: str) -> ChangeRecord:
    if status not in STATUSES:
        raise ChangeError(f"status must be one of {STATUSES}")
    allowed = {
        "draft": {"replayed", "in_review", "rejected"},
        "replayed": {"in_review", "rejected"},
        "in_review": {"approved", "rejected"},
        "approved": {"deployed", "rejected"},
        "deployed": {"rolled_back"},
        "rejected": set(),
        "rolled_back": set(),
    }
    if status not in allowed.get(change.status, set()):
        raise ChangeError(f"cannot move from '{change.status}' to '{status}'")
    change.status = status
    change.decided_by = actor
    change.decided_at = datetime.now(timezone.utc)
    db.commit()
    return change


def change_to_dict(change: ChangeRecord) -> dict:
    return {
        "id": change.id,
        "system_id": change.system_id,
        "kind": change.kind,
        "title": change.title,
        "author": change.author,
        "status": change.status,
        "current": change.current,
        "candidate": change.candidate,
        "evidence": change.evidence,
        "rollback_target": change.rollback_target,
        "decided_by": change.decided_by,
        "decided_at": change.decided_at.isoformat() if change.decided_at else None,
        "created_at": change.created_at.isoformat() if change.created_at else None,
    }
