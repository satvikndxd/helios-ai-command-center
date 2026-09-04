"""
Contextual action risk engine.

Risk is NOT the tool name. The same `github.merge_pr` is LOW against a
scratch branch in dev and CRITICAL against a protected branch in
production. The engine is deterministic: additive signals, each recorded as
a human-readable reason, folded into {risk, score, reasons} that goes on
the trace verbatim.
"""

from __future__ import annotations

import re

from helios.broker.manifest import ToolManifest
from helios.broker.types import InvocationContext, RiskAssessment
from helios.config import settings


_BASE_BY_CAPABILITY = {
    "read": 0.05,
    "network": 0.15,
    "write": 0.30,
    "execute": 0.35,
    "destructive": 0.75,
}

_BASE_BY_RISK_CLASS = {"low": 0.0, "medium": 0.15, "high": 0.35, "critical": 0.6}

# Shell fragments that escalate risk when found in command arguments.
_DANGEROUS_SHELL = re.compile(
    r"(rm\s+-rf|sudo\b|curl[^|]*\|\s*(ba)?sh|mkfs|dd\s+if=|chmod\s+777|"
    r">\s*/etc/|shutdown|reboot|:\(\)\s*\{)",
    re.IGNORECASE,
)

_SENSITIVE_PATH = re.compile(
    r"(\.env|id_rsa|\.ssh/|credentials|secrets?\.|\.pem\b|\.key\b|password)",
    re.IGNORECASE,
)


def _protected_branches() -> set[str]:
    return {b.strip() for b in settings.protected_branches.split(",") if b.strip()}


def assess_risk(
    manifest: ToolManifest,
    args: dict,
    resource: dict,
    context: InvocationContext,
) -> RiskAssessment:
    """Deterministic contextual risk scoring. Higher score = more dangerous."""
    reasons: list[str] = []
    score = 0.0

    cap = manifest.capability
    score += _BASE_BY_CAPABILITY.get(cap, 0.5)
    reasons.append(f"{cap} operation")

    score += _BASE_BY_RISK_CLASS.get(manifest.risk_class, 0.6)
    if manifest.risk_class in ("high", "critical"):
        reasons.append(f"tool risk class {manifest.risk_class}")

    # --- environment ------------------------------------------------------
    if context.environment == "production":
        if cap in ("write", "execute", "destructive"):
            score += 0.30
            reasons.append("production environment")
        else:
            score += 0.10
            reasons.append("production environment (read)")
    elif context.environment == "staging" and cap in ("write", "execute", "destructive"):
        score += 0.10
        reasons.append("staging environment")

    # --- target branch protection ----------------------------------------
    protected = _protected_branches()
    for field in ("github.branch", "github.base", "git.branch"):
        branch = resource.get(field)
        if branch and str(branch) in protected and cap in ("write", "execute", "destructive"):
            score += 0.30
            reasons.append(f"protected branch '{branch}'")
            break

    # --- argument content signals ----------------------------------------
    flat_args = " ".join(str(v) for v in args.values())
    if manifest.name.startswith("shell.") and _DANGEROUS_SHELL.search(flat_args):
        score += 0.35
        reasons.append("dangerous shell pattern in command")
    if _SENSITIVE_PATH.search(flat_args):
        score += 0.25
        reasons.append("touches sensitive path or secret material")

    # --- actor context ----------------------------------------------------
    if context.autonomy == "autonomous" and cap in ("write", "execute", "destructive"):
        score += 0.15
        reasons.append("autonomous agent (no human in the loop)")
    if context.data_classes and cap in ("write", "network"):
        sensitive = {"pii", "phi", "financial", "secret"} & set(context.data_classes)
        if sensitive:
            score += 0.20
            reasons.append(f"sensitive data classes in scope: {sorted(sensitive)}")

    # --- irreversibility --------------------------------------------------
    if not manifest.idempotent and cap in ("write", "execute", "destructive"):
        score += 0.05
        reasons.append("non-idempotent side effect")

    score = min(score, 1.0)
    if score >= 0.75:
        risk = "critical"
    elif score >= 0.5:
        risk = "high"
    elif score >= 0.25:
        risk = "medium"
    else:
        risk = "low"

    return RiskAssessment(risk=risk, score=score, reasons=reasons)
