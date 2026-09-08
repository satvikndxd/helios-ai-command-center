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
    BLOCK_DEPLOYMENT,
    DENY,
    REQUIRE_APPROVAL,
    REQUIRE_HUMAN_REVIEW,
    InvocationContext,
    PolicyDecision,
    RiskAssessment,
    risk_at_least,
)
from helios.broker.manifest import ToolManifest
from helios.governance.classification import at_least as data_class_at_least, max_class


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
        data_class: str = "public",
    ) -> PolicyDecision:
        explanation: list[str] = []
        for rule in self.rules:
            matched, why = _rule_matches(rule, manifest, risk, context, data_class)
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
    data_class: str = "public",
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

    # --- V1.5 system-governance match keys -------------------------------

    min_autonomy = match.get("min_autonomy_level")
    if min_autonomy is not None:
        if context.autonomy_level < min_autonomy:
            return False, f"autonomy L{context.autonomy_level} below L{min_autonomy}"
        checks.append(f"autonomy L{context.autonomy_level} >= L{min_autonomy}")

    max_autonomy = match.get("max_autonomy_level")
    if max_autonomy is not None:
        if context.autonomy_level > max_autonomy:
            return False, f"autonomy L{context.autonomy_level} above L{max_autonomy}"
        checks.append(f"autonomy L{context.autonomy_level} <= L{max_autonomy}")

    min_data = match.get("min_data_class")
    if min_data is not None:
        if not data_class_at_least(data_class, min_data):
            return False, f"data class '{data_class}' below '{min_data}'"
        checks.append(f"data class '{data_class}' >= '{min_data}'")

    data_in = match.get("data_class_in")
    if data_in is not None:
        if data_class not in data_in:
            return False, f"data class '{data_class}' not in {data_in}"
        checks.append(f"data class '{data_class}' in {data_in}")

    system_risk = match.get("system_risk_class")
    if system_risk is not None:
        risks = system_risk if isinstance(system_risk, list) else [system_risk]
        if context.system_risk_class not in risks:
            return False, f"system risk '{context.system_risk_class}' not in {risks}"
        checks.append(f"system risk in {risks}")

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
            "id": "block_confidential_data_writes",
            "match": {
                "capability": ["write", "execute", "destructive", "network"],
                "min_data_class": "confidential",
            },
            "effect": REQUIRE_HUMAN_REVIEW,
            "reason": "actions carrying confidential/sensitive/PII data require "
                      "human review",
        },
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
