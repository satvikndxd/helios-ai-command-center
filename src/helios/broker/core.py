"""
ToolBroker — the authoritative execution boundary.

Every tool invocation, whether proposed by a model or requested directly
over the API, goes through `ToolBroker.invoke`. Nothing else may call an
executor. The broker:

    1. resolves the tool manifest (unknown tool -> deny)
    2. validates arguments against the manifest's input schema
    3. evaluates permission scopes + resource constraints (deny by default)
    4. computes contextual risk ({risk, score, reasons})
    5. evaluates versioned policy (ALLOW | DENY | REQUIRE_APPROVAL)
    6. binds approvals to the exact payload hash (mutation invalidates)
    7. executes with an idempotency journal (safe retry)
    8. sanitizes results (secret scrubbing, injection quarantine)
    9. records every step as hierarchical TraceEvents

Approval reuses the existing ApprovalRequest/ActionEffect tables — the
proven payload-hash binding from Phase W4 is now the binding for ALL tools.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from helios.broker.manifest import ToolManifest, validate_args
from helios.broker.permissions import PermissionSet
from helios.broker.policy import ToolPolicy, get_policy
from helios.broker.registry import ToolRegistry, default_registry
from helios.broker.risk import assess_risk
from helios.broker.trace import TraceRecorder, args_preview
from helios.broker.types import (
    ALLOW,
    DENY,
    REQUIRE_APPROVAL,
    InvocationContext,
)
from helios.models import ActionEffect, ApprovalRequest
from helios.sentinel import detect_injection
from helios.web.actions import hash_args
from helios.web.sanitize import scrub_secrets


@dataclass
class BrokerResult:
    """Structured outcome of one brokered invocation."""

    status: str  # executed | denied | approval_required | error
    tool: str
    args_hash: str = ""
    permission: dict | None = None
    risk: dict | None = None
    policy: dict | None = None
    approval_id: str | None = None
    approval_mode: str | None = None  # existing | session | none
    result: dict | None = None
    effect_id: str | None = None
    replayed: bool = False
    reason: str = ""
    proposal_event_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    # V1.5 POLICY plane: system-level governance decision (when the call is
    # bound to a registered AI system). Escalation-only over the tool policy.
    governance: dict | None = None
    data_classification: dict | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "tool": self.tool,
            "args_hash": self.args_hash,
            "permission": self.permission,
            "risk": self.risk,
            "policy": self.policy,
            "governance": self.governance,
            "data_classification": self.data_classification,
            "approval_id": self.approval_id,
            "approval_mode": self.approval_mode,
            "result": self.result,
            "effect_id": self.effect_id,
            "replayed": self.replayed,
            "reason": self.reason,
            "proposal_event_id": self.proposal_event_id,
            "warnings": list(self.warnings),
        }


class ToolBroker:
    def __init__(self, registry: ToolRegistry | None = None, policy: ToolPolicy | None = None):
        self.registry = registry or default_registry()
        self.policy = policy or get_policy()

    # -- decision-only evaluation (shared by invoke and replay) -----------

    def evaluate(
        self,
        tool_name: str,
        args: dict,
        context: InvocationContext,
        permissions: PermissionSet,
        policy: ToolPolicy | None = None,
    ) -> dict:
        """
        Pure decision pipeline: manifest -> validation -> permissions ->
        risk -> policy. No side effects, no DB. Deterministic — this is
        exactly what replay re-runs against candidate policies.
        """
        policy = policy or self.policy
        tool = self.registry.get(tool_name)
        if tool is None:
            return {
                "decision": DENY,
                "reason": f"unknown tool '{tool_name}' — only manifested tools exist",
                "stage": "manifest",
            }
        manifest = tool.manifest

        errors = validate_args(manifest.input_schema, args)
        if errors:
            return {
                "decision": DENY,
                "reason": "argument validation failed: " + "; ".join(errors[:5]),
                "stage": "validation",
                "manifest": {"name": manifest.name, "version": manifest.version},
            }

        resource = tool.resource(args)

        permission_results = []
        for scope in manifest.scopes or ["tool.invoke"]:
            decision = permissions.check(scope, resource, context)
            permission_results.append(decision.to_dict())
            if not decision.allowed:
                return {
                    "decision": DENY,
                    "reason": f"permission denied for scope '{scope}': "
                    + "; ".join(decision.reasons),
                    "stage": "permission",
                    "resource": resource,
                    "permission": permission_results,
                    "manifest": {"name": manifest.name, "version": manifest.version},
                }

        risk = assess_risk(manifest, args, resource, context)
        policy_decision = policy.evaluate(manifest, risk, context)

        return {
            "decision": policy_decision.decision,
            "reason": policy_decision.reason,
            "stage": "policy",
            "resource": resource,
            "permission": permission_results,
            "risk": risk.to_dict(),
            "policy": policy_decision.to_dict(),
            "manifest": {"name": manifest.name, "version": manifest.version,
                         "capability": manifest.capability},
        }

    # -- full brokered invocation ----------------------------------------

    def invoke(
        self,
        db: Session,
        context: InvocationContext,
        tool_name: str,
        args: dict,
        *,
        permissions: PermissionSet,
        recorder: TraceRecorder,
        idempotency_key: str | None = None,
        session_approvals: list[dict] | None = None,
    ) -> BrokerResult:
        args = dict(args or {})
        args_hash = hash_args(tool_name, args)
        session_approvals = session_approvals or []

        # --- V1.5 IDENTITY/POLICY planes: resolve the registered AI system
        # and classify the payload data BEFORE the decision pipeline, so the
        # risk engine and governance overlay see real data classes.
        system = None
        if context.system_id:
            from helios.governance.systems import get_system

            system = get_system(db, context.tenant_id, context.system_id)
        from helios.governance.data import classify_args

        declared = list(context.data_classes or [])
        if system is not None:
            declared = sorted(set(declared) | set(system.data_classes or []))
        classification = classify_args(args, declared=declared)
        if system is not None:
            # Governed calls: the classified data posture feeds the risk engine
            # and the governance overlay. Legacy (unbound) calls keep exact V1
            # semantics — classification is recorded as evidence but does not
            # alter risk scoring, preserving backward compatibility.
            context.data_classes = sorted(
                set(declared) | set(classification["detected"])
                | {classification["effective"]}
            )

        proposal = recorder.record(
            "tool_proposal",
            tool_name,
            {
                "tool": tool_name,
                "args": args,
                "args_hash": args_hash,
                "context": context.to_dict(),
                "system_id": context.system_id,
                "data_classification": classification,
            },
        )

        # --- V1.5 lifecycle gate: a blocked/suspended/archived AI system does
        # not execute. Identity state is governance state.
        if system is not None and system.lifecycle in ("blocked", "suspended",
                                                        "archived"):
            reason = (
                f"ai system '{system.system_id}' is {system.lifecycle} — "
                "execution is not permitted"
            )
            if system.lifecycle == "blocked" and system.blocked_reason:
                reason += f" ({system.blocked_reason.get('detail', '')})"
            governance_payload = {
                "decision": "deny",
                "reason": reason,
                "explanation": [f"system lifecycle state: {system.lifecycle}"],
                "matched_rules": [{"set": "helios-identity", "version": "v1",
                                   "rule_id": "lifecycle_gate", "effect": "deny",
                                   "why": f"lifecycle == {system.lifecycle}"}],
                "policy_versions": ["helios-identity@v1"],
            }
            result_lifecycle = BrokerResult(
                status="denied", tool=tool_name, args_hash=args_hash,
                reason=reason, proposal_event_id=proposal.id,
                data_classification=classification,
                governance=governance_payload,
            )
            recorder.record("governance_evaluation", tool_name, governance_payload,
                            parent_id=proposal.id, status="deny")
            recorder.record("outcome", tool_name,
                            {"status": "denied", "reason": reason},
                            parent_id=proposal.id, status="denied")
            return result_lifecycle

        evaluation = self.evaluate(tool_name, args, context, permissions)
        result = BrokerResult(
            status="denied",
            tool=tool_name,
            args_hash=args_hash,
            permission={"checks": evaluation.get("permission")},
            risk=evaluation.get("risk"),
            policy=evaluation.get("policy"),
            reason=evaluation["reason"],
            proposal_event_id=proposal.id,
            data_classification=classification,
        )

        # Record each decision layer under the proposal.
        if evaluation.get("permission") is not None:
            perm_allowed = all(p["allowed"] for p in evaluation["permission"])
            recorder.record(
                "permission_evaluation",
                tool_name,
                {"checks": evaluation["permission"], "resource": evaluation.get("resource")},
                parent_id=proposal.id,
                status="ok" if perm_allowed else "denied",
            )
        if evaluation.get("risk") is not None:
            recorder.record(
                "risk_evaluation",
                tool_name,
                evaluation["risk"],
                parent_id=proposal.id,
                risk=evaluation["risk"]["risk"],
            )
        recorder.record(
            "policy_evaluation",
            tool_name,
            evaluation.get("policy")
            or {"decision": DENY, "reason": evaluation["reason"], "stage": evaluation["stage"]},
            parent_id=proposal.id,
            risk=(evaluation.get("risk") or {}).get("risk"),
            status="ok" if evaluation["decision"] == ALLOW else evaluation["decision"],
        )

        # --- V1.5 governance overlay (system-level policy, escalation-only) --
        # The ToolPolicy owns deny-by-default for the tool layer; governance
        # policies add system/model/data/autonomy restrictions on top. They
        # can never RELAX a tool-layer decision, only tighten it.
        if system is not None and evaluation.get("manifest") is not None:
            from helios.governance import engine as governance_engine

            governance = governance_engine.check_tool_action(
                db, context.tenant_id,
                system=system,
                tool=tool_name,
                capability=evaluation["manifest"].get("capability", "read"),
                action_risk=(evaluation.get("risk") or {}).get("risk", "low"),
                environment=context.environment,
                autonomy_level=context.effective_autonomy_level(),
                data_class=classification["effective"],
            )
            result.governance = governance.to_dict()
            recorder.record(
                "governance_evaluation",
                tool_name,
                governance.to_dict(),
                parent_id=proposal.id,
                risk=(evaluation.get("risk") or {}).get("risk"),
                status="ok" if governance.decision == ALLOW else governance.decision,
            )
            if governance.decision == "deny" and evaluation["decision"] != DENY:
                evaluation["decision"] = DENY
                evaluation["reason"] = (
                    f"governance policy denied: {governance.reason} "
                    f"[{', '.join(governance.policy_versions)}]"
                )
                evaluation["stage"] = "governance"
                result.reason = evaluation["reason"]
            elif (governance.decision in ("require_approval", "require_human_review")
                  and evaluation["decision"] == ALLOW):
                evaluation["decision"] = REQUIRE_APPROVAL
                evaluation["reason"] = (
                    f"governance policy requires human oversight: {governance.reason} "
                    f"[{', '.join(governance.policy_versions)}]"
                )
                evaluation["stage"] = "governance"
                result.reason = evaluation["reason"]

        if evaluation["decision"] == DENY:
            recorder.record(
                "outcome", tool_name,
                {"status": "denied", "reason": evaluation["reason"]},
                parent_id=proposal.id, status="denied",
            )
            return result

        tool = self.registry.get(tool_name)
        manifest = tool.manifest
        approval_id: str | None = None
        approval_mode = "none"

        if evaluation["decision"] == REQUIRE_APPROVAL:
            from helios.governance.oversight import (
                approval_is_valid,
                expire_stale,
                session_approval_valid,
            )

            # keep approval state honest before matching (lazy expiration)
            expire_stale(db, context.tenant_id)

            # 1. session-scoped standing approval for this exact tool,
            #    validated against expiry + environment/system/uses scope?
            matched_session_entry = next(
                (sa for sa in session_approvals
                 if session_approval_valid(sa, tool_name, context)),
                None,
            )
            if matched_session_entry is not None:
                approval_mode = "session"
                recorder.record(
                    "approval", tool_name,
                    {"mode": "session", "detail": "approved for session by user",
                     "granted_by": matched_session_entry.get("granted_by"),
                     "scope": {k: matched_session_entry.get(k) for k in
                               ("environment", "system_id", "expires_at",
                                "max_uses", "uses")
                               if matched_session_entry.get(k) is not None}},
                    parent_id=proposal.id, status="approved",
                )
            else:
                # 2. an APPROVED, UNEXPIRED request bound to this exact
                #    payload hash?
                approved_candidates = (
                    db.query(ApprovalRequest)
                    .filter(
                        ApprovalRequest.tenant_id == context.tenant_id,
                        ApprovalRequest.action == tool_name,
                        ApprovalRequest.args_hash == args_hash,
                        ApprovalRequest.status == "approved",
                    )
                    .all()
                )
                approved = next((a for a in approved_candidates
                                 if approval_is_valid(a)), None)
                if approved is not None:
                    approval_id = approved.id
                    approval_mode = "existing"
                    recorder.record(
                        "approval", tool_name,
                        {"mode": "payload_bound", "approval_id": approved.id,
                         "decided_by": approved.decided_by, "args_hash": args_hash},
                        parent_id=proposal.id, status="approved",
                    )
                else:
                    # 3. create a pending, payload-bound approval request.
                    governance_decision = (result.governance or {}).get("decision")
                    pending = ApprovalRequest(
                        tenant_id=context.tenant_id,
                        action=tool_name,
                        args_hash=args_hash,
                        risk=(evaluation.get("risk") or {}).get("risk", "high"),
                        decision_kind=("review" if governance_decision
                                       == "require_human_review" else None),
                        system_id=context.system_id,
                        summary={
                            "tool": tool_name,
                            "description": manifest.description,
                            "capability": manifest.capability,
                            "args": args_preview(args),
                            "resource": evaluation.get("resource"),
                            "environment": context.environment,
                            "agent_id": context.agent_id,
                            "user_id": context.user_id,
                            "session_id": context.session_id,
                            "run_id": context.run_id,
                            "system_id": context.system_id,
                            "risk": evaluation.get("risk"),
                            "policy": evaluation.get("policy"),
                            "governance": result.governance,
                            "data_classification": classification,
                            "args_editable": manifest.args_editable,
                            "proposal_event_id": proposal.id,
                        },
                    )
                    db.add(pending)
                    db.commit()
                    recorder.record(
                        "approval", tool_name,
                        {"mode": "pending", "approval_id": pending.id,
                         "args_hash": args_hash,
                         "risk": evaluation.get("risk"),
                         "policy_reason": evaluation["reason"]},
                        parent_id=proposal.id, status="pending",
                        risk=(evaluation.get("risk") or {}).get("risk"),
                    )
                    result.status = "approval_required"
                    result.approval_id = pending.id
                    result.reason = evaluation["reason"]
                    return result

        # --- execution (idempotency journal for effectful tools) --------
        effectful = manifest.capability in ("write", "execute", "destructive")
        if effectful and idempotency_key:
            existing = (
                db.query(ActionEffect)
                .filter(
                    ActionEffect.tenant_id == context.tenant_id,
                    ActionEffect.idempotency_key == idempotency_key,
                )
                .first()
            )
            if existing is not None:
                recorder.record(
                    "tool_execution", tool_name,
                    {"replayed": True, "idempotency_key": idempotency_key,
                     "effect_id": existing.id, "result": existing.result},
                    parent_id=proposal.id, status="replayed",
                )
                result.status = "executed"
                result.replayed = True
                result.effect_id = existing.id
                result.result = existing.result
                result.approval_id = approval_id
                result.approval_mode = approval_mode
                result.reason = "idempotent replay from effect journal"
                return result

        started = time.monotonic()
        try:
            raw = tool.execute(args, context)
        except Exception as exc:  # executor failure is an outcome, not a crash
            latency = int((time.monotonic() - started) * 1000)
            recorder.record(
                "tool_execution", tool_name,
                {"error": str(exc)[:500], "args_hash": args_hash},
                parent_id=proposal.id, status="error", latency_ms=latency,
            )
            recorder.record(
                "outcome", tool_name,
                {"status": "error", "reason": str(exc)[:500]},
                parent_id=proposal.id, status="error",
            )
            result.status = "error"
            result.reason = f"tool execution failed: {exc}"
            result.approval_id = approval_id
            result.approval_mode = approval_mode
            return result

        latency = int((time.monotonic() - started) * 1000)
        clean, warnings = sanitize_result(raw, external=bool(manifest.network))

        effect_id = None
        if effectful:
            effect = ActionEffect(
                tenant_id=context.tenant_id,
                idempotency_key=idempotency_key or f"auto-{proposal.id}",
                action=tool_name,
                args_hash=args_hash,
                approval_id=approval_id,
                status="executed",
                result=clean,
            )
            db.add(effect)
            db.commit()
            effect_id = effect.id

        recorder.record(
            "tool_execution", tool_name,
            {"result": clean, "warnings": warnings, "effect_id": effect_id,
             "idempotency_key": idempotency_key},
            parent_id=proposal.id, status="ok", latency_ms=latency,
            risk=(evaluation.get("risk") or {}).get("risk"),
        )

        # --- V1.5 EVIDENCE plane: explicit data-flow evidence --------------
        # Inbound: data that just entered the agent context (classified —
        # classes and counts only, never raw sensitive content).
        # Outbound: where data was written/sent, with the effective class.
        # Every lineage edge (Phase 5) must cite events like these.
        _record_data_flow(recorder, proposal.id, manifest, tool_name,
                          evaluation.get("resource") or {}, clean,
                          classification, effect_id)

        result.status = "executed"
        result.result = clean
        result.effect_id = effect_id
        result.approval_id = approval_id
        result.approval_mode = approval_mode
        result.warnings = warnings
        result.reason = evaluation["reason"]
        return result


def _source_kind(tool_name: str) -> str:
    prefix = tool_name.split(".", 1)[0]
    return {
        "fs": "file", "git": "repo", "github": "repo",
        "shell": "shell", "http": "external", "mcp": "external",
    }.get(prefix, prefix)


def _record_data_flow(recorder: TraceRecorder, proposal_id: str,
                      manifest, tool_name: str, resource: dict,
                      clean_result: dict, classification: dict,
                      effect_id: str | None) -> None:
    """
    Record data-flow evidence for one executed tool call.

    read/network  -> inbound event (`retrieval` when the tool reaches the
                     network, `data_access` otherwise) with the classes
                     detected in the result that entered the agent context.
    write/destructive -> outbound `data_access` event naming the destination
                     and the effective class of the data that flowed to it.
    Payloads carry classes/counts and resource identifiers only — never raw
    sensitive content (scrub_payload additionally scrubs on persist).
    """
    import json as _json

    from helios.governance.data import classify_text

    capability = manifest.capability
    if capability in ("read", "network"):
        try:
            blob = _json.dumps(clean_result, default=str)[:65536]
        except Exception:
            blob = ""
        found = classify_text(blob)
        event_type = "retrieval" if (manifest.network or capability == "network") \
            else "data_access"
        recorder.record(
            event_type, tool_name,
            {
                "direction": "inbound",
                "source_kind": _source_kind(tool_name),
                "source": resource,
                "classes_detected": found["classes"],
                "pii_counts": found["pii"],
                "sensitive_markers": found["sensitive_markers"],
                "effect_id": effect_id,
            },
            parent_id=proposal_id, status="ok",
        )
    elif capability in ("write", "destructive"):
        destination = dict(resource)
        if not destination:
            destination = {"detail": "see tool_execution payload"}
        recorder.record(
            "data_access", tool_name,
            {
                "direction": "outbound",
                "source_kind": _source_kind(tool_name),
                "destination": destination,
                "data_class": classification.get("effective", "PUBLIC"),
                "classes": sorted(set(classification.get("declared") or [])
                                  | set(classification.get("detected") or [])),
                "pii_counts": classification.get("pii_counts", {}),
                "effect_id": effect_id,
            },
            parent_id=proposal_id, status="ok",
        )


def sanitize_result(raw: dict, *, external: bool) -> tuple[dict, list[str]]:
    """
    Treat every tool result as untrusted input.

    - Secrets are ALWAYS scrubbed.
    - Prompt-injection patterns are detected and flagged; for tools that
      touch the network (external content) the offending text is withheld
      entirely. A tool result can never silently override policy — it is
      only ever data.
    """
    warnings: list[str] = []

    def _walk(value):
        if isinstance(value, str):
            clean, n = scrub_secrets(value)
            if n:
                warnings.append(f"secrets_redacted={n}")
            matches = detect_injection(clean)
            if matches:
                warnings.append("injection_detected")
                if external:
                    return ("[content withheld: prompt-injection patterns "
                            "detected in external tool output]")
            return clean
        if isinstance(value, dict):
            return {k: _walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v) for v in value]
        return value

    if not isinstance(raw, dict):
        raw = {"result": raw}
    return _walk(raw), sorted(set(warnings))
