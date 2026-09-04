"""
Shared broker types: one risk vocabulary, one decision vocabulary.

Everything here is deterministic and JSON-serializable so that any decision
can be stored in a trace and replayed byte-for-byte later.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# The single canonical risk vocabulary for the control plane.
RISK_LEVELS = ("low", "medium", "high", "critical")

# Policy decision types.
ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"


def risk_at_least(risk: str, threshold: str) -> bool:
    """True if `risk` is at or above `threshold` in the canonical ordering."""
    try:
        return RISK_LEVELS.index(risk) >= RISK_LEVELS.index(threshold)
    except ValueError:
        # Unknown risk strings are treated as critical: fail closed.
        return True


@dataclass
class InvocationContext:
    """
    Who/what is asking, and in what context.

    The permission, risk, and policy layers all key off this — the same tool
    call can be low risk for one context and critical for another.
    """

    tenant_id: str
    environment: str = "dev"           # dev | staging | production
    organization: str | None = None
    project: str | None = None
    agent_id: str | None = None        # agent identity (session-bound)
    user_id: str | None = None         # human on whose behalf the agent acts
    session_id: str | None = None
    run_id: str | None = None
    autonomy: str = "supervised"       # supervised | autonomous
    data_classes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "environment": self.environment,
            "organization": self.organization,
            "project": self.project,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "autonomy": self.autonomy,
            "data_classes": list(self.data_classes),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InvocationContext":
        return cls(
            tenant_id=data["tenant_id"],
            environment=data.get("environment", "dev"),
            organization=data.get("organization"),
            project=data.get("project"),
            agent_id=data.get("agent_id"),
            user_id=data.get("user_id"),
            session_id=data.get("session_id"),
            run_id=data.get("run_id"),
            autonomy=data.get("autonomy", "supervised"),
            data_classes=list(data.get("data_classes") or []),
        )


@dataclass
class PermissionDecision:
    allowed: bool
    scope: str
    reasons: list[str] = field(default_factory=list)
    matched_grant: dict | None = None

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "scope": self.scope,
            "reasons": list(self.reasons),
            "matched_grant": self.matched_grant,
        }


@dataclass
class RiskAssessment:
    risk: str                  # low | medium | high | critical
    score: float               # 0.0 .. 1.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"risk": self.risk, "score": round(self.score, 4), "reasons": list(self.reasons)}


@dataclass
class PolicyDecision:
    decision: str              # allow | deny | require_approval
    policy_version: str
    rule_id: str
    reason: str
    explanation: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "policy_version": self.policy_version,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "explanation": list(self.explanation),
        }


class BrokerDenied(Exception):
    """Raised when the broker refuses an invocation (permission/policy/validation)."""

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail or {}
