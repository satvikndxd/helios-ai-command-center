"""
Tool policy engine — versioned, deterministic, replayable.

A policy is an ordered list of rules; the first matching rule wins.
Rules match on tool name (glob), capability, scope, risk level threshold,
environment, and autonomy — and decide ALLOW, DENY, or REQUIRE_APPROVAL
with a human-readable reason.

Policies are plain data (JSON round-trippable), so a historical run can be
re-evaluated against the same policy version, a newer one, or a candidate.
"""

from __future__ import annotations

import fnmatch

from helios.broker.types import (
    ALLOW,
    DENY,
    REQUIRE_APPROVAL,
    InvocationContext,
    PolicyDecision,
    RiskAssessment,
    risk_at_least,
)
from helios.broker.manifest import ToolManifest


class ToolPolicy:
    """An ordered, versioned rule set. Deterministic: first match wins."""

    def __init__(self, version: str, rules: list[dict], description: str = ""):
        self.version = version
        self.description = description
        self.rules = [dict(r) for r in rules]

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "description": self.description,
            "rules": [dict(r) for r in self.rules],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ToolPolicy":
        return cls(
            version=data["version"],
            rules=data.get("rules", []),
            description=data.get("description", ""),
        )

    def evaluate(
        self,
        manifest: ToolManifest,
        risk: RiskAssessment,
        context: InvocationContext,
    ) -> PolicyDecision:
        explanation: list[str] = []
        for rule in self.rules:
            matched, why = _rule_matches(rule, manifest, risk, context)
            if matched:
                explanation.append(f"rule '{rule['id']}' matched: {why}")
                return PolicyDecision(
                    decision=rule["effect"],
                    policy_version=self.version,
                    rule_id=rule["id"],
                    reason=rule.get("reason", rule["id"]),
                    explanation=explanation,
                )
            explanation.append(f"rule '{rule['id']}' did not match: {why}")

        # No rule matched: fail closed.
        return PolicyDecision(
            decision=DENY,
            policy_version=self.version,
            rule_id="default_deny",
            reason="no policy rule matched (deny by default)",
            explanation=explanation,
        )


def _rule_matches(
    rule: dict,
    manifest: ToolManifest,
    risk: RiskAssessment,
    context: InvocationContext,
) -> tuple[bool, str]:
    match = rule.get("match", {})
    checks: list[str] = []

    tool_pat = match.get("tool")
    if tool_pat is not None:
        if not fnmatch.fnmatch(manifest.name, tool_pat):
            return False, f"tool '{manifest.name}' !~ '{tool_pat}'"
        checks.append(f"tool ~ '{tool_pat}'")

    cap = match.get("capability")
    if cap is not None:
        caps = cap if isinstance(cap, list) else [cap]
        if manifest.capability not in caps:
            return False, f"capability '{manifest.capability}' not in {caps}"
        checks.append(f"capability in {caps}")

    min_risk = match.get("min_risk")
    if min_risk is not None:
        if not risk_at_least(risk.risk, min_risk):
            return False, f"risk '{risk.risk}' below '{min_risk}'"
        checks.append(f"risk '{risk.risk}' >= '{min_risk}'")

    max_risk = match.get("max_risk")
    if max_risk is not None:
        if risk_at_least(risk.risk, max_risk) and risk.risk != max_risk:
            return False, f"risk '{risk.risk}' above '{max_risk}'"
        checks.append(f"risk '{risk.risk}' <= '{max_risk}'")

    env = match.get("environment")
    if env is not None:
        envs = env if isinstance(env, list) else [env]
        if context.environment not in envs:
            return False, f"environment '{context.environment}' not in {envs}"
        checks.append(f"environment in {envs}")

    autonomy = match.get("autonomy")
    if autonomy is not None:
        if context.autonomy != autonomy:
            return False, f"autonomy '{context.autonomy}' != '{autonomy}'"
        checks.append(f"autonomy == '{autonomy}'")

    approval_mode = match.get("approval")
    if approval_mode is not None:
        if manifest.approval != approval_mode:
            return False, f"manifest approval '{manifest.approval}' != '{approval_mode}'"
        checks.append(f"manifest approval == '{approval_mode}'")

    return True, ", ".join(checks) if checks else "unconditional"


DEFAULT_POLICY = ToolPolicy(
    version="helios-default-v1",
    description=(
        "Default control-plane policy: reads flow, writes are risk-gated, "
        "production writes by autonomous agents are forbidden, anything "
        "high-risk needs a human."
    ),
    rules=[
        {
            "id": "deny_autonomous_production_writes",
            "match": {
                "capability": ["write", "execute", "destructive"],
                "environment": "production",
                "autonomy": "autonomous",
            },
            "effect": DENY,
            "reason": "production write forbidden for autonomous agents",
        },
        {
            "id": "approval_always_tools",
            "match": {"approval": "always"},
            "effect": REQUIRE_APPROVAL,
            "reason": "tool manifest requires approval for every invocation",
        },
        {
            "id": "deny_critical_destructive",
            "match": {"capability": "destructive", "min_risk": "critical"},
            "effect": DENY,
            "reason": "critical-risk destructive actions are never automatic",
        },
        {
            "id": "approval_for_high_risk",
            "match": {"min_risk": "high"},
            "effect": REQUIRE_APPROVAL,
            "reason": "high/critical-risk actions require human approval",
        },
        {
            "id": "approval_for_production_writes",
            "match": {
                "capability": ["write", "execute", "destructive"],
                "environment": "production",
            },
            "effect": REQUIRE_APPROVAL,
            "reason": "all production writes require human approval",
        },
        {
            "id": "allow_low_medium",
            "match": {"max_risk": "medium"},
            "effect": ALLOW,
            "reason": "low/medium-risk action within granted permissions",
        },
    ],
)

# Named policy registry (versioned; replay can target any of these).
POLICIES: dict[str, ToolPolicy] = {DEFAULT_POLICY.version: DEFAULT_POLICY}


def get_policy(version: str | None = None) -> ToolPolicy:
    if version is None:
        return DEFAULT_POLICY
    if version in POLICIES:
        return POLICIES[version]
    raise KeyError(f"unknown policy version '{version}'")


def register_policy(policy: ToolPolicy) -> None:
    existing = POLICIES.get(policy.version)
    if existing is not None and existing.to_dict() != policy.to_dict():
        raise ValueError(f"policy version '{policy.version}' already registered")
    POLICIES[policy.version] = policy
