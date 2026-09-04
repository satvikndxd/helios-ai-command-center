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

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "tool": self.tool,
            "args_hash": self.args_hash,
            "permission": self.permission,
            "risk": self.risk,
            "policy": self.policy,
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

        proposal = recorder.record(
            "tool_proposal",
            tool_name,
            {
                "tool": tool_name,
                "args": args,
                "args_hash": args_hash,
                "context": context.to_dict(),
            },
        )

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
            # 1. session-scoped standing approval for this exact tool?
            if any(sa.get("tool") == tool_name for sa in session_approvals):
                approval_mode = "session"
                recorder.record(
                    "approval", tool_name,
                    {"mode": "session", "detail": "approved for session by user"},
                    parent_id=proposal.id, status="approved",
                )
            else:
                # 2. an APPROVED request bound to this exact payload hash?
                approved = (
                    db.query(ApprovalRequest)
                    .filter(
                        ApprovalRequest.tenant_id == context.tenant_id,
                        ApprovalRequest.action == tool_name,
                        ApprovalRequest.args_hash == args_hash,
                        ApprovalRequest.status == "approved",
                    )
                    .first()
                )
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
                    pending = ApprovalRequest(
                        tenant_id=context.tenant_id,
                        action=tool_name,
                        args_hash=args_hash,
                        risk=(evaluation.get("risk") or {}).get("risk", "high"),
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
                            "risk": evaluation.get("risk"),
                            "policy": evaluation.get("policy"),
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

        result.status = "executed"
        result.result = clean
        result.effect_id = effect_id
        result.approval_id = approval_id
        result.approval_mode = approval_mode
        result.warnings = warnings
        result.reason = evaluation["reason"]
        return result


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
