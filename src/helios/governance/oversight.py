"""
Human oversight (POLICY/EVIDENCE planes).

Extends the V1 approval machinery (payload-hash binding stays untouched) into
a full oversight system:

    approval / denial      decide with comments + accountability
    expiration             approvals lapse; expired approvals never authorize
    scope binding          session approvals bind to environment/system/uses
    delegation             a pending decision can be reassigned; only the
                           delegate may decide it
    escalation             reassignment upward with a recorded reason
    compliance analysis    "Where was a human involved? Where SHOULD one have
                           been? Did the AI bypass required oversight?"

The analysis compares the system's DECLARED oversight requirements against
the actual DecisionRecord evidence — surfacing gaps where policy enforcement
does not implement what the organization declared (a policy gap, not a
speculation). Every finding cites record/event ids.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from helios.models import ApprovalRequest, DecisionRecord

WRITE_CAPABILITIES = ("write", "execute", "destructive")
HIGH_RISKS = ("high", "critical")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# --- expiration ----------------------------------------------------------------


def expire_stale(db: Session, tenant_id: str | None = None) -> int:
    """
    Lazy expiration sweep: pending OR approved requests past expires_at become
    `expired` (with an audit comment). Called on approval listing and before
    broker matching — deterministic, no background worker required. An
    approval that lapsed is terminal: it can never authorize again.
    """
    q = db.query(ApprovalRequest).filter(
        ApprovalRequest.status.in_(("pending", "approved")),
        ApprovalRequest.expires_at.isnot(None),
        ApprovalRequest.expires_at < _now(),
    )
    if tenant_id:
        q = q.filter(ApprovalRequest.tenant_id == tenant_id)
    stale = q.all()
    for approval in stale:
        previous = approval.status
        approval.status = "expired"
        approval.comments = list(approval.comments or []) + [{
            "by": "helios", "at": _now().isoformat(),
            "text": f"approval expired (was {previous}; past expires_at)",
        }]
    if stale:
        db.commit()
    return len(stale)


def mark_lapsed(db: Session, approval: ApprovalRequest) -> None:
    """
    Record that an already-decided approval lapsed before it was used.

    A human decision is never rewritten (`denied` stays `denied`), but the
    lapse itself is audit-relevant evidence: the authorization existed and
    then stopped existing.
    """
    expires = _parse_ts(approval.expires_at)
    if expires is None or expires >= _now():
        return
    comments = list(approval.comments or [])
    if any(c.get("text", "").startswith("approval lapsed") for c in comments):
        return  # already noted
    comments.append({
        "by": "helios", "at": _now().isoformat(),
        "text": f"approval lapsed at {expires.isoformat()} without being used",
    })
    approval.comments = comments
    db.commit()


def approval_is_valid(approval: ApprovalRequest) -> bool:
    """An approved request authorizes execution only while unexpired."""
    if approval.status != "approved":
        return False
    expires = _parse_ts(approval.expires_at)
    return expires is None or expires > _now()


# --- session (standing) approvals ------------------------------------------------


def session_approval_valid(entry: dict, tool_name: str, context) -> bool:
    """
    Validate one session-scoped standing approval entry against a call:
    tool match + not expired + environment/system scope binding.
    """
    if entry.get("tool") != tool_name:
        return False
    expires = _parse_ts(entry.get("expires_at"))
    if expires is not None and expires <= _now():
        return False
    environment = entry.get("environment")
    if environment and getattr(context, "environment", None) != environment:
        return False
    system_id = entry.get("system_id")
    if system_id and getattr(context, "system_id", None) != system_id:
        return False
    max_uses = entry.get("max_uses")
    if max_uses is not None and int(entry.get("uses", 0)) >= int(max_uses):
        return False
    return True


def consume_session_approval(session, tool_name: str, context) -> None:
    """Increment the use counter of the matching session approval (max_uses)."""
    # Deep-copy entries: mutating the loaded JSON in place and reassigning the
    # same objects is invisible to SQLAlchemy's change tracking.
    entries = [dict(e) for e in (session.session_approvals or [])]
    changed = False
    for entry in entries:
        if session_approval_valid(entry, tool_name, context) \
                and entry.get("max_uses") is not None:
            entry["uses"] = int(entry.get("uses", 0)) + 1
            changed = True
            break
    if changed:
        session.session_approvals = entries


# --- comments / delegation / escalation ------------------------------------------


def add_comment(approval: ApprovalRequest, by: str, text: str) -> ApprovalRequest:
    approval.comments = list(approval.comments or []) + [
        {"by": by, "at": _now().isoformat(), "text": text[:2000]}
    ]
    return approval


def delegate(approval: ApprovalRequest, to: str, by: str,
             reason: str | None = None) -> ApprovalRequest:
    """Reassign a pending decision. Only the delegate may decide afterwards."""
    if approval.status != "pending":
        raise ValueError(f"only pending approvals can be delegated "
                         f"(this one is {approval.status})")
    approval.delegated_to = to
    add_comment(approval, by,
                f"delegated to {to}" + (f": {reason}" if reason else ""))
    return approval


def escalation_comment(approval: ApprovalRequest, by: str, to: str,
                       reason: str) -> ApprovalRequest:
    """Escalation = delegation upward with a mandatory recorded reason."""
    if not reason:
        raise ValueError("escalation requires a reason")
    approval.delegated_to = to
    approval.summary = dict(approval.summary or {}, escalated=True,
                            escalated_by=by, escalation_reason=reason[:500])
    add_comment(approval, by, f"ESCALATED to {to}: {reason}")
    return approval


def check_decider(approval: ApprovalRequest, decided_by: str) -> None:
    """Delegation binding: a delegated approval is decided by the delegate."""
    if approval.delegated_to and decided_by != approval.delegated_to:
        raise PermissionError(
            f"approval was delegated to '{approval.delegated_to}'; "
            f"'{decided_by}' may not decide it"
        )


# --- compliance analysis ----------------------------------------------------------


def _capability_of(tool_name: str) -> str:
    try:
        from helios.broker.registry import default_registry

        tool = default_registry().get(tool_name)
        return tool.manifest.capability if tool else "read"
    except Exception:
        return "read"


def analyze_system(db: Session, tenant_id: str, system_id: str,
                   *, system=None, limit: int = 500) -> dict:
    """
    Oversight compliance for one AI system, derived from DecisionRecords:

    * involved        where a human actually decided (with reviewer + refs)
    * required        where policy demanded oversight
    * bypassed        required oversight + execution without it (enforcement
                      failure — should be impossible; flagged with evidence)
    * requirement_gaps declared requirements NOT implemented by the policies
                      that ran (e.g. org declared "writes need approval" but a
                      write executed with oversight.required == false)
    * pending/expired/denied queues + decision-time stats
    """
    expire_stale(db, tenant_id)
    records = (
        db.query(DecisionRecord)
        .filter(DecisionRecord.tenant_id == tenant_id,
                DecisionRecord.system_id == system_id,
                DecisionRecord.kind == "tool_action")
        .order_by(DecisionRecord.created_at.desc())
        .limit(limit)
        .all()
    )
    requirements = dict((system.oversight if system else None) or {})

    involved, required, bypassed, gaps = [], [], [], []
    decision_times: list[float] = []

    for record in records:
        oversight = record.oversight or {}
        ref = {"record_id": record.id, "run_id": record.run_id,
               "action": record.action, "decision": record.decision,
               "approval_id": oversight.get("approval_id")}
        if oversight.get("required"):
            required.append(ref)
        if oversight.get("actual") in ("approved", "existing", "session",
                                        "payload_bound"):
            involved.append({**ref, "reviewer": oversight.get("reviewer")})
        if oversight.get("actual") == "denied":
            involved.append({**ref, "reviewer": oversight.get("reviewer"),
                             "outcome": "denied"})
        if (oversight.get("required") and record.decision == "EXECUTED"
                and oversight.get("actual") == "none"):
            bypassed.append(ref)

        capability = _capability_of(record.action)
        environment = (record.actor or {}).get("environment")
        risk = record.risk or "low"
        executed = record.decision in ("EXECUTED", "APPROVED")

        if executed and not oversight.get("required"):
            if requirements.get("writes") == "approval" \
                    and capability in WRITE_CAPABILITIES:
                gaps.append({**ref, "requirement": "writes",
                             "detail": f"{capability} action executed without "
                                       "declared approval requirement"})
            if requirements.get("production_writes") == "approval" \
                    and environment == "production" \
                    and capability in WRITE_CAPABILITIES:
                gaps.append({**ref, "requirement": "production_writes",
                             "detail": "production write executed without "
                                       "declared approval requirement"})
            if requirements.get("high_risk") == "approval" and risk in HIGH_RISKS:
                gaps.append({**ref, "requirement": "high_risk",
                             "detail": f"{risk}-risk action executed without "
                                       "declared approval requirement"})

    # approval-level stats for this system
    approvals = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.tenant_id == tenant_id,
                ApprovalRequest.system_id == system_id)
        .all()
    )
    for approval in approvals:
        created = _parse_ts(approval.created_at)
        decided = _parse_ts(approval.decided_at)
        if created and decided:
            decision_times.append((decided - created).total_seconds())

    pending = [a.id for a in approvals if a.status == "pending"]
    expired = [a.id for a in approvals if a.status == "expired"]
    denied = [a.id for a in approvals if a.status == "denied"]

    return {
        "system_id": system_id,
        "requirements": requirements,
        "window": {"records_analyzed": len(records), "limit": limit},
        "required_count": len(required),
        "involved": involved,
        "bypassed": bypassed,
        "requirement_gaps": gaps,
        "approvals": {
            "pending": pending,
            "expired": expired,
            "denied": denied,
            "total": len(approvals),
            "avg_decision_time_s": (
                round(sum(decision_times) / len(decision_times), 2)
                if decision_times else None
            ),
        },
        "compliant": not bypassed and not gaps,
    }
