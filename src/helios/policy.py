"""
Helios policy engine — policy-as-code, Phase-1 scale (FR-GOV-001/002).

Deterministic rules evaluated at two enforcement points:
- preflight: before any provider call
- output: after the model responds, before returning to the caller

Actions: allow | redact | deny. Every decision is recorded on the trace so
audits can answer "which policies passed/failed and why" (FR-GOV-005).
"""

from dataclasses import dataclass, field


EXTERNAL_PROVIDERS = {"groq", "openrouter", "gemini", "openai", "anthropic"}
HIGH_RISK = {"high", "critical"}


@dataclass
class PolicyViolation:
    policy: str
    severity: str
    message: str

    def to_dict(self) -> dict:
        return {"policy": self.policy, "severity": self.severity, "message": self.message}


@dataclass
class PolicyResult:
    action: str  # allow | redact | deny
    stage: str  # preflight | output
    violations: list[PolicyViolation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "stage": self.stage,
            "violations": [v.to_dict() for v in self.violations],
        }


def preflight(
    *,
    risk_level: str,
    pii: dict[str, int],
    injection_matches: list[str],
    provider_name: str,
) -> PolicyResult:
    """Input-side policy checks. Runs after Sentinel scanning, before the model."""
    violations: list[PolicyViolation] = []
    action = "allow"

    if pii:
        if risk_level in HIGH_RISK:
            # policy: no_pii_in_high_risk_requests
            violations.append(
                PolicyViolation(
                    policy="no_pii_in_high_risk_requests",
                    severity="critical",
                    message=f"PII detected ({', '.join(sorted(pii))}) in a "
                    f"{risk_level}-risk request; blocked outright.",
                )
            )
            action = "deny"
        elif provider_name in EXTERNAL_PROVIDERS:
            # policy: redact_pii_before_external_models
            violations.append(
                PolicyViolation(
                    policy="redact_pii_before_external_models",
                    severity="medium",
                    message=f"PII ({', '.join(sorted(pii))}) redacted before "
                    f"external provider '{provider_name}'.",
                )
            )
            action = "redact"

    if injection_matches:
        if risk_level in HIGH_RISK:
            violations.append(
                PolicyViolation(
                    policy="block_injection_high_risk",
                    severity="critical",
                    message="Prompt-injection patterns in a high-risk request.",
                )
            )
            action = "deny"
        else:
            violations.append(
                PolicyViolation(
                    policy="warn_injection_low_risk",
                    severity="low",
                    message="Prompt-injection patterns detected; request allowed "
                    "but flagged for evaluation.",
                )
            )

    return PolicyResult(action=action, stage="preflight", violations=violations)


def output_check(
    *,
    task_type: str,
    risk_level: str,
    citations: list,
    output_leaks: dict[str, int],
) -> PolicyResult:
    """Output-side policy checks, before the response reaches the caller."""
    violations: list[PolicyViolation] = []
    action = "allow"

    if output_leaks:
        # policy: block_pii_leakage_in_output
        violations.append(
            PolicyViolation(
                policy="block_pii_leakage_in_output",
                severity="critical",
                message=f"Model output contains leak-class PII: "
                f"{', '.join(sorted(output_leaks))}.",
            )
        )
        action = "deny"

    if task_type == "answer" and risk_level in HIGH_RISK and not citations:
        # policy: high_risk_answers_require_citations
        violations.append(
            PolicyViolation(
                policy="high_risk_answers_require_citations",
                severity="high",
                message="High-risk answers must be grounded with citations "
                "(set use_knowledge_base=true).",
            )
        )
        action = "deny"

    return PolicyResult(action=action, stage="output", violations=violations)
