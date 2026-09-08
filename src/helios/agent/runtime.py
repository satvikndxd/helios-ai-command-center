"""
The agent run loop.

Explicit state machine — the TUI/API always knows exactly what the agent is
doing; a blocked or approval-waiting agent is never a generic "working":

    thinking            model is being consulted
    tool_pending        a tool proposal is entering the broker
    running             an approved/allowed tool is executing
    awaiting_approval   a human must decide before anything happens
    blocked             a human denied the pending action, or governance
                        denied the model call (recorded + explained)
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


class AgentRuntime:
    def __init__(self, broker: ToolBroker | None = None):
        self.broker = broker or ToolBroker(default_registry())

    # -- helpers -----------------------------------------------------------

    def _context(self, db: Session, session: AgentSession, run: AgentRun) -> InvocationContext:
        from helios.governance.autonomy import normalize_level
        from helios.governance.systems import get_system

        level: int | None = None
        if session.system_id:
            system = get_system(db, session.tenant_id, session.system_id)
            if system is not None:
                level = system.autonomy_level
        return InvocationContext(
            tenant_id=session.tenant_id,
            environment=session.environment,
            agent_id=session.agent_id,
            user_id=session.user_id,
            session_id=session.id,
            run_id=run.id,
            autonomy=session.autonomy,
            system_id=session.system_id,
            autonomy_level=level if level is not None
            else normalize_level(session.autonomy),
        )

    def _recorder(self, db: Session, session: AgentSession, run: AgentRun) -> TraceRecorder:
        last = (
            db.query(TraceEvent.seq)
            .filter(TraceEvent.run_id == run.id)
            .order_by(TraceEvent.seq.desc())
            .first()
        )
        return TraceRecorder(
            db, tenant_id=session.tenant_id, run_id=run.id, session_id=session.id,
            system_id=session.system_id,
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

    def _model_preflight(self, db: Session, session: AgentSession,
                         run: AgentRun, recorder: TraceRecorder,
                         conversation: str, provider_name: str,
                         model_id: str) -> dict:
        """
        POLICY plane preflight for the model call (V1.5).

        Classifies the DATA being sent (the conversation — user messages,
        assistant outputs, tool results; NOT HELIOS's own prompt scaffolding,
        which is known-internal boilerplate), joined with the declared data
        classes of the registered system. Then asks the Model Registry whether
        THIS model may receive THIS data in THIS environment. The decision is
        recorded as evidence either way — a denied model call is never silent.
        """
        from helios.governance import models_registry
        from helios.governance.data import classify_text, max_class
        from helios.governance.systems import get_system

        system = (
            get_system(db, session.tenant_id, session.system_id)
            if session.system_id else None
        )

        # Lifecycle gate: blocked/suspended/archived systems do not call models.
        if system is not None and system.lifecycle in ("blocked", "suspended",
                                                        "archived"):
            reason = (f"ai system '{system.system_id}' is {system.lifecycle} — "
                      "execution is not permitted")
            preflight = {
                "decision": "deny",
                "reasons": [reason],
                "checks": [{"check": "system_lifecycle", "passed": False,
                            "reason": f"lifecycle state: {system.lifecycle}"}],
                "model": {"provider": provider_name, "model_id": model_id,
                          "approval_status": "n/a"},
                "system_id": system.system_id,
                "data_classification": {"declared": list(system.data_classes or []),
                                        "detected": [], "pii_counts": {},
                                        "effective": "UNKNOWN"},
            }
            recorder.record(
                "policy_evaluation", f"model_preflight:{provider_name}/{model_id}",
                {"plane": "identity", **preflight}, status="denied",
            )
            return preflight

        declared = list(system.data_classes or []) if system else []
        detected = classify_text(conversation[:262_144])
        effective_class = max_class(set(declared) | set(detected["classes"]))
        data_classes = sorted(set(declared) | set(detected["classes"]))

        preflight = models_registry.check_model_usage(
            db, session.tenant_id, provider_name, model_id,
            data_classes=data_classes,
            environment=session.environment,
            governed=system is not None,
        )
        preflight["data_classification"] = {
            "declared": declared,
            "detected": detected["classes"],
            "pii_counts": detected["pii"],
            "effective": effective_class,
        }
        preflight["system_id"] = system.system_id if system else None
        if system is not None and system.environment != session.environment:
            preflight["warnings"] = [
                f"session environment '{session.environment}' differs from "
                f"registered system environment '{system.environment}'"
            ]

        recorder.record(
            "policy_evaluation", f"model_preflight:{provider_name}/{model_id}",
            {"plane": "model", "provider": provider_name, "model": model_id,
             "environment": session.environment, **preflight},
            status="ok" if preflight["decision"] == "allow" else "denied",
        )

        # --- governance policy layer (system-level rules) --------------------
        from helios.governance import engine as governance_engine
        from helios.governance.autonomy import normalize_level

        governance = governance_engine.check_model_call(
            db, session.tenant_id, system=system,
            model=preflight["model"], data_class=effective_class,
            environment=session.environment,
        )
        preflight["governance"] = governance.to_dict()
        recorder.record(
            "governance_evaluation", f"model_preflight:{provider_name}/{model_id}",
            {"plane": "model", "data_class": effective_class, **governance.to_dict()},
            status="ok" if governance.decision == "allow" else governance.decision,
        )

        if preflight["decision"] == "allow":
            if governance.decision in ("deny", "block_deployment"):
                preflight["decision"] = "deny"
                preflight["reasons"] = (
                    [f"governance policy: {governance.reason}"] + preflight["reasons"]
                )
            elif governance.decision in ("require_approval", "require_human_review"):
                self._model_oversight_gate(
                    db, session, recorder, preflight, governance,
                    provider_name=provider_name, model_id=model_id,
                    effective_class=effective_class,
                    autonomy_level=(system.autonomy_level if system
                                    else normalize_level(session.autonomy)),
                )
        return preflight

    def _model_oversight_gate(self, db: Session, session: AgentSession,
                              recorder: TraceRecorder, preflight: dict,
                              governance, *, provider_name: str, model_id: str,
                              effective_class: str, autonomy_level: int) -> None:
        """
        Human-oversight gate for model calls: REQUIRE_APPROVAL /
        REQUIRE_HUMAN_REVIEW produce a payload-hash-bound ApprovalRequest for
        the EXACT governance posture (system, environment, data class,
        autonomy, matched rules). An approval satisfies only that posture —
        a policy or data-class change requires a fresh human decision.
        """
        from helios.models import ApprovalRequest
        from helios.web.actions import hash_args

        action = f"model.call:{provider_name}/{model_id}"
        posture = {
            "system_id": session.system_id,
            "environment": session.environment,
            "data_class": effective_class,
            "autonomy_level": autonomy_level,
            "rules": [{"set": m["set"], "version": m["version"], "rule_id": m["rule_id"]}
                      for m in governance.matched_rules],
        }
        posture_hash = hash_args(action, posture)

        approved = (
            db.query(ApprovalRequest)
            .filter(ApprovalRequest.tenant_id == session.tenant_id,
                    ApprovalRequest.action == action,
                    ApprovalRequest.args_hash == posture_hash,
                    ApprovalRequest.status == "approved")
            .first()
        )
        if approved is not None:
            recorder.record(
                "approval", action,
                {"mode": "payload_bound", "approval_id": approved.id,
                 "decided_by": approved.decided_by, "posture": posture,
                 "governance": governance.to_dict()},
                status="approved",
            )
            preflight["approval_id"] = approved.id
            return

        pending = (
            db.query(ApprovalRequest)
            .filter(ApprovalRequest.tenant_id == session.tenant_id,
                    ApprovalRequest.action == action,
                    ApprovalRequest.args_hash == posture_hash,
                    ApprovalRequest.status == "pending")
            .first()
        )
        if pending is None:
            pending = ApprovalRequest(
                tenant_id=session.tenant_id,
                action=action,
                args_hash=posture_hash,
                risk="high",
                system_id=session.system_id,
                summary={
                    "kind": "model_oversight",
                    "governance_decision": governance.decision,
                    "reason": governance.reason,
                    "explanation": governance.explanation,
                    "matched_rules": governance.matched_rules,
                    "posture": posture,
                    "model": preflight["model"],
                    "environment": session.environment,
                    "data_classification": preflight["data_classification"],
                    "session_id": session.id,
                },
            )
            db.add(pending)
            db.commit()
        recorder.record(
            "approval", action,
            {"mode": "pending", "approval_id": pending.id, "posture": posture,
             "governance": governance.to_dict()},
            status="pending", risk="high",
        )
        preflight["decision"] = "review_required"
        preflight["approval_id"] = pending.id
        preflight["reasons"] = [f"human oversight required: {governance.reason}"]

    async def _model_call(self, db: Session, session: AgentSession,
                          run: AgentRun, recorder: TraceRecorder) -> dict | None:
        manifests = self.broker.registry.list()
        prompt = SYSTEM_PROMPT.format(tools=render_tools(manifests))
        transcript = render_transcript(
            [{"role": "system", "content": prompt}] + list(session.messages or [])
        )
        provider_name = session.model_provider
        model_id = session.model_id or settings.default_model

        conversation = "\n".join(
            str(m.get("content", "")) for m in (session.messages or [])
        )
        preflight = self._model_preflight(db, session, run, recorder,
                                          conversation, provider_name, model_id)
        if preflight["decision"] == "review_required":
            reason = "; ".join(preflight["reasons"])
            run.error = {
                "message": f"model call requires human oversight: {reason}"[:1000],
                "approval_id": preflight.get("approval_id"),
            }
            self._set_state(db, recorder, run, "blocked",
                            f"model call requires human oversight: {reason}"[:200])
            recorder.record(
                "outcome", "model_oversight_required",
                {"provider": provider_name, "model": model_id,
                 "approval_id": preflight.get("approval_id"),
                 "reasons": preflight["reasons"],
                 "governance": preflight.get("governance")},
                status="blocked",
            )
            return None
        if preflight["decision"] != "allow":
            reason = "; ".join(preflight["reasons"])
            run.error = {"message": f"model governance denied: {reason}"[:1000]}
            self._set_state(db, recorder, run, "blocked",
                            f"model call denied: {reason}"[:200])
            recorder.record(
                "outcome", "model_call_denied",
                {"provider": provider_name, "model": model_id,
                 "reasons": preflight["reasons"], "checks": preflight["checks"]},
                status="blocked",
            )
            return None

        provider = get_provider(provider_name, settings)
        request = {
            "input_text": transcript,
            "model": model_id,
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
             "usage": result.usage, "output_preview": (result.output_text or "")[:500],
             "preflight": {"decision": preflight["decision"],
                           "data_classification": preflight["data_classification"]}},
            latency_ms=latency, cost_usd=cost,
        )
        return parse_proposal(result.output_text or "")

    # -- main loop ---------------------------------------------------------

    async def run_message(self, db: Session, session: AgentSession,
                          run: AgentRun) -> AgentRun:
        """Drive a run until it completes, fails, or needs a human."""
        recorder = self._recorder(db, session, run)
        try:
            run = await self._loop(db, session, run, recorder)
        except Exception as exc:  # runtime failure is a state, not a 500
            run.error = {"message": str(exc)[:1000]}
            self._set_state(db, recorder, run, "failed", str(exc)[:200])
            recorder.record("outcome", "failed", {"error": str(exc)[:500]},
                            status="error")
        self._project_decisions(db, session, run)
        return run

    @staticmethod
    def _project_decisions(db: Session, session: AgentSession,
                           run: AgentRun) -> None:
        """
        EVIDENCE plane: refresh this run's normalized DecisionRecords from the
        stored events. Fail-safe — a projection error must never break the
        control path (the events themselves are already committed evidence).
        """
        try:
            from helios.governance.decisions import build_for_run

            build_for_run(db, session.tenant_id, run.id)
        except Exception:  # noqa: BLE001 - evidence projection is fail-safe
            db.rollback()

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
            if proposal is None:
                # Model governance preflight denied the call; the run is
                # blocked with an explained, recorded outcome.
                return run

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
        result = self.broker.invoke(
            db, self._context(db, session, run), tool_name, args,
            permissions=PermissionSet(session.grants or []),
            recorder=recorder,
            idempotency_key=idempotency_key or f"{run.id}:{recorder.seq}",
            session_approvals=list(session.session_approvals or []),
        )

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
        # session approvals with max_uses consume one use per execution
        if result.status == "executed" and result.approval_mode == "session":
            from helios.governance.oversight import consume_session_approval

            consume_session_approval(session, tool_name,
                                     self._context(db, session, run))
            db.commit()
        self._append_message(db, session, {
            "role": "tool", "tool": tool_name,
            "content": json.dumps(payload, default=str)[:8000],
        })
        return result.status

    # -- resume after an approval decision ---------------------------------

    async def resume(self, db: Session, session: AgentSession,
                     run: AgentRun) -> AgentRun:
        run = await self._resume_inner(db, session, run)
        self._project_decisions(db, session, run)
        return run

    async def _resume_inner(self, db: Session, session: AgentSession,
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

        from helios.governance.oversight import session_approval_valid

        context = self._context(db, session, run)
        session_approved = any(
            session_approval_valid(sa, pending.get("tool"), context)
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
            # The broker RE-GATED the action (expired/tampered/scope-invalid
            # approval): the run waits for the new human decision — it must
            # never continue as if the gated action had happened.
            return run
        return await self._loop(db, session, run, recorder)
