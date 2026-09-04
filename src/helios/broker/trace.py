"""
Hierarchical trace recorder.

Every broker/runtime decision becomes a TraceEvent row under an agent run:

    agent_run
      ├── model_call
      ├── tool_proposal
      │     ├── permission_evaluation
      │     ├── risk_evaluation
      │     ├── policy_evaluation
      │     ├── approval
      │     └── tool_execution
      ├── state_change
      └── outcome

Secrets are scrubbed before anything is persisted.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from helios.models import TraceEvent
from helios.web.sanitize import scrub_secrets


def scrub_payload(payload: dict) -> dict:
    """Deep-scrub secret-looking strings from a JSON-serializable payload."""
    def _walk(value):
        if isinstance(value, str):
            clean, _ = scrub_secrets(value)
            return clean
        if isinstance(value, dict):
            return {k: _walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v) for v in value]
        return value

    try:
        return _walk(payload)
    except Exception:
        # Never let trace hygiene break the control path.
        return {"unserializable": True, "repr": scrub_secrets(repr(payload))[0][:2000]}


class TraceRecorder:
    """Appends ordered TraceEvents for one run (or a standalone invocation)."""

    def __init__(
        self,
        db: Session,
        *,
        tenant_id: str,
        run_id: str | None = None,
        session_id: str | None = None,
        start_seq: int = 0,
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.session_id = session_id
        self._seq = start_seq

    def record(
        self,
        event_type: str,
        name: str,
        payload: dict,
        *,
        parent_id: str | None = None,
        risk: str | None = None,
        status: str = "ok",
        latency_ms: int = 0,
        cost_usd: float = 0.0,
    ) -> TraceEvent:
        self._seq += 1
        event = TraceEvent(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            session_id=self.session_id,
            parent_id=parent_id,
            seq=self._seq,
            event_type=event_type,
            name=name[:255],
            payload=scrub_payload(payload),
            risk=risk,
            status=status,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
        )
        self.db.add(event)
        self.db.commit()
        return event

    @property
    def seq(self) -> int:
        return self._seq


def event_to_dict(event: TraceEvent) -> dict:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "session_id": event.session_id,
        "parent_id": event.parent_id,
        "seq": event.seq,
        "event_type": event.event_type,
        "name": event.name,
        "payload": event.payload,
        "risk": event.risk,
        "status": event.status,
        "latency_ms": event.latency_ms,
        "cost_usd": event.cost_usd,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def args_preview(args: dict, limit: int = 160) -> dict:
    """Short, scrubbed preview of an argument payload for summaries."""
    preview = {}
    for key, value in args.items():
        text = json.dumps(value, default=str) if not isinstance(value, str) else value
        clean, _ = scrub_secrets(text)
        preview[key] = clean[:limit]
    return preview
