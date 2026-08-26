"""
Action workflows (Phase W4) — typed write actions behind approvals,
idempotency, and the audit trail.

* Every write action is TYPED (a registry entry with a risk class and an
  executor) — a model-generated arbitrary payload cannot become an action.
* Execution requires an ApprovalRequest bound to the exact
  (action, args_hash): approving one payload does not authorize another.
* Every execution writes an ActionEffect row keyed by idempotency_key;
  a retry with the same key returns the recorded effect instead of
  executing again.
* Scheduled research observes and reports; it never writes externally.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from helios.models import ActionEffect, ApprovalRequest, ScheduledResearch


def hash_args(action: str, args: dict) -> str:
    """Canonical hash binding an approval to one exact payload."""
    canonical = json.dumps({"action": action, "args": args}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ActionDenied(Exception):
    pass


# -- typed action registry -------------------------------------------------
#
# Executors are deliberately inert in the OSS MVP: they produce a structured
# "prepared action" that a connector (GitHub App, webhook, MCP write tool)
# turns into a real side effect on the enterprise track.  The governance
# path — typing, approval binding, idempotency, audit — is fully real.

def _prepare_github_issue(args: dict) -> dict:
    return {
        "prepared": "github_open_issue",
        "repo": args.get("repo"),
        "title": args.get("title"),
        "body_sha256": hashlib.sha256(str(args.get("body", "")).encode()).hexdigest(),
    }


def _prepare_webhook_notification(args: dict) -> dict:
    return {"prepared": "webhook_notify", "channel": args.get("channel"),
            "summary": str(args.get("summary", ""))[:200]}


ACTION_REGISTRY: dict[str, dict] = {
    "github_open_issue": {
        "risk": "high",
        "executor": _prepare_github_issue,
        "description": "Open a GitHub issue (reviewable, scoped)",
    },
    "webhook_notify": {
        "risk": "medium",
        "executor": _prepare_webhook_notification,
        "description": "Send a notification to a configured webhook",
    },
    "browser_read_authenticated": {
        "risk": "high",
        "executor": None,  # executed by the browser worker, still approval-bound
        "description": "Read a page using a stored authenticated browser session",
    },
}


def propose_action(
    db: Session, tenant_id: str, action: str, args: dict, summary: dict | None = None
) -> ApprovalRequest:
    if action not in ACTION_REGISTRY:
        raise ActionDenied(f"unknown action '{action}' — only typed actions exist")
    approval = ApprovalRequest(
        tenant_id=tenant_id,
        action=action,
        args_hash=hash_args(action, args),
        summary=summary or {"action": action, "args_preview": {k: str(v)[:80] for k, v in args.items()}},
        risk=ACTION_REGISTRY[action]["risk"],
    )
    db.add(approval)
    db.commit()
    return approval


def require_approval(
    db: Session, tenant_id: str, action: str, args: dict
) -> ApprovalRequest:
    """Find an APPROVED request bound to this exact action+args."""
    args_hash = hash_args(action, args)
    approval = (
        db.query(ApprovalRequest)
        .filter(
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.action == action,
            ApprovalRequest.args_hash == args_hash,
            ApprovalRequest.status == "approved",
        )
        .first()
    )
    if not approval:
        raise ActionDenied(
            f"action '{action}' requires an approved ApprovalRequest for this "
            "exact payload (args hash mismatch or not approved)"
        )
    return approval


def execute_action(
    db: Session,
    tenant_id: str,
    action: str,
    args: dict,
    idempotency_key: str,
) -> tuple[ActionEffect, bool]:
    """
    Execute a typed, approved action exactly once per idempotency key.

    Returns (effect, replayed): replayed=True means the effect journal
    already had this key and NO new execution happened.
    """
    if not idempotency_key:
        raise ActionDenied("idempotency_key is required for write actions")

    existing = (
        db.query(ActionEffect)
        .filter(
            ActionEffect.tenant_id == tenant_id,
            ActionEffect.idempotency_key == idempotency_key,
        )
        .first()
    )
    if existing:
        return existing, True

    approval = require_approval(db, tenant_id, action, args)
    executor: Callable | None = ACTION_REGISTRY[action]["executor"]
    if executor is None:
        raise ActionDenied(f"action '{action}' is not executable via this endpoint")

    result = executor(args)
    effect = ActionEffect(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        action=action,
        args_hash=hash_args(action, args),
        approval_id=approval.id,
        status="executed",
        result=result,
    )
    db.add(effect)
    db.commit()
    return effect, False


# -- scheduled research (observe -> diff -> report, never write) -----------


def run_scheduled_research(db: Session, schedule: ScheduledResearch, broker) -> dict:
    """
    One watch cycle: search -> compare content hashes with the previous run
    -> store a report.  On change, the report flags `change_detected` so a
    human (or an approved notification action) can follow up — the schedule
    itself never posts or modifies anything externally.
    """
    from helios.web.types import WebAccessRequest

    request = WebAccessRequest(
        operation="search",
        query=schedule.query,
        sources=schedule.sources or [],
        max_results=10,
    )
    decision, documents, statuses = broker.dispatch(request)

    hashes = sorted(d.content_hash() for d in documents)
    previous = set(schedule.last_content_hashes or [])
    new_hashes = [h for h in hashes if h not in previous]

    report = {
        "query": schedule.query,
        "allowed": decision.allowed,
        "source_status": [s.model_dump() for s in statuses],
        "documents": len(documents),
        "new_documents": len(new_hashes),
        "change_detected": bool(new_hashes) and bool(previous),
        "citations": [d.url for d in documents if d.url],
    }

    schedule.last_content_hashes = hashes
    schedule.last_run_at = datetime.now(timezone.utc)
    schedule.last_report = report
    db.commit()
    return report
