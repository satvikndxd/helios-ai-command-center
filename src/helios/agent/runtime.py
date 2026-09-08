"""
The agent run loop.

Explicit state machine — the TUI/API always knows exactly what the agent is
doing; a blocked or approval-waiting agent is never a generic "working":

    thinking            model is being consulted
    tool_pending        a tool proposal is entering the broker
    running             an approved/allowed tool is executing
    awaiting_approval   a human must decide before anything happens
    blocked             a human denied the pending action
    completed | failed | cancelled

Invariant: the ONLY way any proposal becomes an effect is
`ToolBroker.invoke`. The runtime owns conversation state and the state
machine; it never executes anything itself.
"""

from __future__ import annotations

import json
import time

from sqlalchemy.orm import Session

from helios.agent.planner import (
    SYSTEM_PROMPT,
    parse_proposal,
    render_tools,
    render_transcript,
)
from helios.broker.core import ToolBroker
from helios.broker.permissions import PermissionSet
from helios.broker.policy import get_policy
from helios.broker.registry import default_registry
from helios.broker.trace import TraceRecorder
from helios.broker.types import InvocationContext
from helios.config import settings
from helios.cost import compute_cost
from helios.models import AgentRun, AgentSession, ApprovalRequest, TraceEvent
from helios.providers import get_provider


TERMINAL_STATES = {"completed", "failed", "cancelled", "blocked"}


class ModelGovernanceBlocked(Exception):
    """The model call was blocked by the Model Registry governance gate."""


def _emit_tool_decision(db, context, tool_name, result) -> None:
    """Write a normalized DecisionRecord for one brokered tool decision."""
    from helios.governance.decisions import record_tool_decision

    decision = {
        "executed": "allow",
        "denied": "deny",
        "approval_required": "require_approval",
        "error": "error",
    }.get(result.status, result.status)
    record_tool_decision(
        db, context,
        tool=tool_name,
        decision=decision,
        reason=result.reason,
        risk=result.risk,
        policy=result.policy,
        resource=result.resource,
        data_classes=result.data_classes,
        human_oversight=result.human_oversight,
        reviewer=result.decided_by,
        approval_id=result.approval_id,
        trace_event_id=result.proposal_event_id,
    )


class AgentRuntime:
    def __init__(self, broker: ToolBroker | None = None):
        self.broker = broker or ToolBroker(default_registry())

    # -- helpers -----------------------------------------------------------

    def _context(self, db: Session, session: AgentSession, run: AgentRun) -> InvocationContext:
        system = self._system(db, session)
        return InvocationContext(
            tenant_id=session.tenant_id,
            environment=session.environment,
            agent_id=session.agent_id,
            user_id=session.user_id,
            session_id=session.id,
            run_id=run.id,
            autonomy=session.autonomy,
            autonomy_level=getattr(session, "autonomy_level", 2),
            system_id=session.system_id,
            system_risk_class=system.risk_class if system is not None else None,
            data_classes=list(system.data_classes or []) if system is not None else [],
        )

    @staticmethod
    def _system(db: Session, session: AgentSession):
        if not session.system_id:
            return None
        from helios.governance.systems import get_system
        return get_system(db, session.tenant_id, session.system_id)

    def _recorder(self, db: Session, session: AgentSession, run: AgentRun) -> TraceRecorder:
        last = (
            db.query(TraceEvent.seq)
            .filter(TraceEvent.run_id == run.id)
            .order_by(TraceEvent.seq.desc())
            .first()
        )
        return TraceRecorder(
            db, tenant_id=session.tenant_id, run_id=run.id, session_id=session.id,
            start_seq=last[0] if last else 0,
        )

    @staticmethod
    def _set_state(db: Session, recorder: TraceRecorder, run: AgentRun,
                   state: str, detail: str = "") -> None:
        if run.state == state:
            return
        previous = run.state
        run.state = state
        db.commit()
        recorder.record("state_change", state,
                        {"from": previous, "to": state, "detail": detail})

    @staticmethod
    def _append_message(db: Session, session: AgentSession, message: dict) -> None:
        session.messages = list(session.messages or []) + [message]
        db.commit()

    # -- model call --------------------------------------------------------

    def _model_governance_gate(self, db: Session, session: AgentSession,
                               run: AgentRun, recorder: TraceRecorder) -> None:
        """
        POLICY PLANE: gate the model call itself when the session is bound to
        a registered AI system. Unknown/unapproved models, or approved models
        receiving data classes they are not cleared for, block the run.
        """
        if not session.system_id:
            return  # unbound sessions keep V1 behavior
        from helios.governance.classification import classify_text
        from helios.governance.model_registry import evaluate_model_use

        declared = self._session_data_classes_for(db, session)
        latest_user = next(
            (m["content"] for m in reversed(session.messages or [])
             if m.get("role") == "user"), "",
        )
        classification = classify_text(latest_user, declared)
        model_id = session.model_id or settings.default_model
        decision = evaluate_model_use(
            db, session.tenant_id, session.model_provider, model_id,
            data_classes=classification.classes, environment=session.environment,
        )
        recorder.record(
            "model_governance", f"{session.model_provider}/{model_id}",
            {"decision": decision.to_dict(), "data_classification": classification.to_dict()},
            status="ok" if decision.allowed else "denied",
            risk=decision.risk_class,
        )
        from helios.governance.decisions import record_model_use_decision
        record_model_use_decision(db, session, run, decision, classification)
        if not decision.allowed:
            raise ModelGovernanceBlocked(
                f"model use denied ({decision.status}): "
                + "; ".join(decision.reasons)
            )

    @staticmethod
    def _session_data_classes_for(db: Session, session: AgentSession) -> list[str]:
        if not session.system_id:
            return []
        from helios.governance.systems import get_system
        system = get_system(db, session.tenant_id, session.system_id)
        return list(system.data_classes or []) if system is not None else []

    async def _model_call(self, db: Session, session: AgentSession,
                          run: AgentRun, recorder: TraceRecorder) -> dict:
        self._model_governance_gate(db, session, run, recorder)
        manifests = self.broker.registry.list()
        prompt = SYSTEM_PROMPT.format(tools=render_tools(manifests))
        transcript = render_transcript(
            [{"role": "system", "content": prompt}] + list(session.messages or [])
        )
        provider = get_provider(session.model_provider, settings)
        request = {
            "input_text": transcript,
            "model": session.model_id or settings.default_model,
            "parameters": {"max_tokens": settings.default_max_tokens},
            "agent_step": run.steps,
        }
        started = time.monotonic()
        result = await provider.complete(request, settings)
        latency = int((time.monotonic() - started) * 1000)
        cost = compute_cost(result.model, result.usage or {}, result.provider)
        run.steps += 1
        run.cost_usd += cost
        run.latency_ms += latency
        db.commit()
        recorder.record(
            "model_call", f"{result.provider}/{result.model}",
            {"provider": result.provider, "model": result.model,
             "usage": result.usage, "output_preview": (result.output_text or "")[:500]},
            latency_ms=latency, cost_usd=cost,
        )
        return parse_proposal(result.output_text or "")

    # -- main loop ---------------------------------------------------------

    async def run_message(self, db: Session, session: AgentSession,
                          run: AgentRun) -> AgentRun:
        """Drive a run until it completes, fails, or needs a human."""
        recorder = self._recorder(db, session, run)
        try:
            return await self._loop(db, session, run, recorder)
        except ModelGovernanceBlocked as exc:
            run.error = {"message": str(exc)[:1000], "kind": "model_governance"}
            self._set_state(db, recorder, run, "blocked", str(exc)[:200])
            recorder.record("outcome", "blocked", {"reason": str(exc)[:500],
                            "kind": "model_governance"}, status="blocked")
            return run
        except Exception as exc:  # runtime failure is a state, not a 500
            run.error = {"message": str(exc)[:1000]}
            self._set_state(db, recorder, run, "failed", str(exc)[:200])
            recorder.record("outcome", "failed", {"error": str(exc)[:500]},
                            status="error")
            return run

    async def _loop(self, db: Session, session: AgentSession,
                    run: AgentRun, recorder: TraceRecorder) -> AgentRun:
        while True:
            db.refresh(run)
            if run.cancel_requested:
                self._set_state(db, recorder, run, "cancelled", "user cancelled")
                recorder.record("outcome", "cancelled", {}, status="cancelled")
                return run
            if run.steps >= settings.agent_max_steps:
                run.error = {"message": "max steps exceeded"}
                self._set_state(db, recorder, run, "failed", "max steps exceeded")
                recorder.record("outcome", "failed",
                                {"error": "max steps exceeded"}, status="error")
                return run

            self._set_state(db, recorder, run, "thinking")
            proposal = await self._model_call(db, session, run, recorder)

            if proposal["type"] == "final":
                run.output_text = proposal["content"]
                self._append_message(db, session,
                                     {"role": "assistant", "content": proposal["content"]})
                self._set_state(db, recorder, run, "completed")
                recorder.record("outcome", "completed",
                                {"output_preview": proposal["content"][:500]})
                return run

            # tool proposal
            tool_name, args = proposal["tool"], proposal["args"]
            self._append_message(db, session, {
                "role": "assistant",
                "content": json.dumps({"type": "tool_call", "tool": tool_name,
                                       "args": args,
                                       "reasoning": proposal.get("reasoning", "")}),
            })
            self._set_state(db, recorder, run, "tool_pending",
                            f"proposed {tool_name}")

            outcome = self._invoke(db, session, run, recorder, tool_name, args)
            if outcome == "awaiting_approval":
                return run
            # denied / executed / error: loop continues so the agent can react

    def _invoke(self, db: Session, session: AgentSession, run: AgentRun,
                recorder: TraceRecorder, tool_name: str, args: dict,
                idempotency_key: str | None = None) -> str:
        """Send one proposal through the broker; feed the outcome back."""
        self._set_state(db, recorder, run, "running", f"executing {tool_name}")
        context = self._context(db, session, run)
        result = self.broker.invoke(
            db, context, tool_name, args,
            permissions=PermissionSet(session.grants or []),
            recorder=recorder,
            idempotency_key=idempotency_key or f"{run.id}:{recorder.seq}",
            session_approvals=list(session.session_approvals or []),
        )
        _emit_tool_decision(db, context, tool_name, result)

        if result.status == "approval_required":
            run.pending = {
                "tool": tool_name,
                "args": args,
                "args_hash": result.args_hash,
                "approval_id": result.approval_id,
                "risk": result.risk,
                "policy": result.policy,
                "reason": result.reason,
            }
            self._set_state(db, recorder, run, "awaiting_approval",
                            f"{tool_name} requires human approval")
            return "awaiting_approval"

        payload = {
            "status": result.status,
            "reason": result.reason,
            "risk": result.risk,
            "result": result.result,
            "warnings": result.warnings,
        }
        self._append_message(db, session, {
            "role": "tool", "tool": tool_name,
            "content": json.dumps(payload, default=str)[:8000],
        })
        return result.status

    # -- resume after an approval decision ---------------------------------

    async def resume(self, db: Session, session: AgentSession,
                     run: AgentRun) -> AgentRun:
        recorder = self._recorder(db, session, run)
        if run.state != "awaiting_approval":
            return run
        pending = dict(run.pending or {})
        approval = db.get(ApprovalRequest, pending.get("approval_id", ""))
        if approval is None:
            run.error = {"message": "pending approval vanished"}
            self._set_state(db, recorder, run, "failed", "approval missing")
            return run

        session_approved = any(
            sa.get("tool") == pending.get("tool")
            for sa in (session.session_approvals or [])
        )
        if approval.status == "pending" and not session_approved:
            return run  # still waiting for a human

        if approval.status == "denied" and not session_approved:
            # Explicit BLOCKED state — never rendered as generic "working".
            run.pending = None
            self._append_message(db, session, {
                "role": "tool", "tool": pending.get("tool", ""),
                "content": json.dumps({
                    "status": "denied_by_human",
                    "reason": f"denied by {approval.decided_by or 'reviewer'}",
                }),
            })
            self._set_state(db, recorder, run, "blocked",
                            f"{pending.get('tool')} denied by {approval.decided_by}")
            recorder.record("outcome", "blocked",
                            {"tool": pending.get("tool"),
                             "denied_by": approval.decided_by}, status="blocked")
            return run

        # approved (payload-bound or for-session): execute the exact payload.
        # If the approver edited arguments, the approval was re-bound to the
        # edited payload hash and run.pending was updated at decide time.
        args = pending.get("args") or {}
        run.pending = None
        db.commit()
        outcome = self._invoke(db, session, run, recorder, pending["tool"], args,
                               idempotency_key=f"{run.id}:approval:{approval.id}")
        if outcome == "awaiting_approval":
            # The approval expired (or was otherwise re-gated): the broker
            # created a fresh pending request. Stop and wait for a human.
            return run
        return await self._loop(db, session, run, recorder)
