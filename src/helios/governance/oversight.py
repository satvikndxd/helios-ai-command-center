"""
Human oversight — approvals as governance records.

Answers, from real recorded state:
  * Where was a human involved?
  * Where SHOULD a human have been involved?
  * Did the AI bypass required human oversight?

Built on the existing ApprovalRequest + DecisionRecord tables.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from helios.models import ApprovalRequest, DecisionRecord


def parse_expiry(ttl_seconds: int | None) -> datetime | None:
    if not ttl_seconds:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(ttl_seconds))


def add_comment(approval: ApprovalRequest, author: str, text: str) -> None:
    approval.comments = list(approval.comments or []) + [{
        "author": author,
        "text": text[:2000],
        "at": datetime.now(timezone.utc).isoformat(),
    }]


def expire_stale(db: Session, tenant_id: str) -> int:
    """Mark any pending approvals past their expiry as expired. Returns count."""
    now = datetime.now(timezone.utc)
    pending = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.tenant_id == tenant_id,
                ApprovalRequest.status == "pending",
                ApprovalRequest.expires_at.isnot(None))
        .all()
    )
    expired = 0
    for approval in pending:
        expires = approval.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if now >= expires:
            approval.status = "expired"
            expired += 1
    if expired:
        db.commit()
    return expired


def oversight_report(db: Session, tenant_id: str, system_id: str) -> dict:
    """
    Reconstruct the human-oversight picture for one AI system from its
    DecisionRecords (the normalized evidence) plus approval status.
    """
    records = (
        db.query(DecisionRecord)
        .filter(DecisionRecord.tenant_id == tenant_id,
                DecisionRecord.system_id == system_id)
        .order_by(DecisionRecord.created_at.desc())
        .limit(500)
        .all()
    )

    involved: list[dict] = []       # a human actually decided
    required_pending: list[dict] = []  # oversight required, not yet granted
    bypassed: list[dict] = []       # oversight required but action proceeded without it

    for record in records:
        oversight = record.human_oversight
        entry = {
            "decision_id": record.id, "action": record.action,
            "decision": record.decision, "risk": record.risk,
            "reviewer": record.reviewer, "created_at":
                record.created_at.isoformat() if record.created_at else None,
        }
        if oversight in ("approved", "denied"):
            involved.append(entry)
        elif oversight == "required":
            # required and the decision did not execute -> pending human
            required_pending.append(entry)
        elif oversight == "bypassed":
            bypassed.append(entry)

    return {
        "system_id": system_id,
        "decisions_reviewed": len(records),
        "human_involved": involved,
        "awaiting_human": required_pending,
        "bypassed_oversight": bypassed,
        "bypass_detected": bool(bypassed),
        "summary": {
            "involved": len(involved),
            "awaiting": len(required_pending),
            "bypassed": len(bypassed),
        },
    }
