"""
Governance policy engine (POLICY plane).

ToolPolicy (broker/policy.py) answers "may this TOOL run in this context?".
GovernancePolicy answers the system-level questions:

    "may this AI system deploy to this environment at this autonomy level?"
    "may this model receive this data class for this system?"
    "must a human review this action class?"

Design DNA is identical to the proven ToolPolicy: ordered rules, explicit
when-conditions, first match wins per set, deterministic, JSON round-trippable
(replayable), every decision explained with the matched conditions.

Outcomes (superset of the broker's):

    ALLOW                 no governance restriction
    REQUIRE_APPROVAL      a human must approve before proceeding
    REQUIRE_HUMAN_REVIEW  a human must review (evidence inspection) — stronger
                          than approval: the decision needs a reviewer verdict
    BLOCK_DEPLOYMENT      the system may not (stay) deployed in this posture
    DENY                  forbidden outright

Layering & defaults — where deny-by-default lives:
* Unknown tools, ungranted scopes, unmatched tool rules: DENY (broker layer,
  unchanged from V1).
* Unknown models on governed systems: DENY (model registry, Phase 3).
* Governance policy sets are the RESTRICTION overlay above those layers: no
  matching rule = no additional restriction (the lower layers already denied
  everything they own). Each persisted set may still declare an explicit
  `default_effect: deny` when an organization wants allow-list semantics for a
  subject class.
* HELIOS ships one built-in default set encoding the canonical autonomy
  ceiling (see DEFAULT_GOVERNANCE_POLICY below). It is versioned with the
  product, always evaluated first, and cannot be silently disabled — an org
  that disagrees overrides it with an explicit ALLOW rule in its own set and
  lives with the audit trail of doing so.

Merging across sets: strictest decision wins (allow < require_approval <
require_human_review < block_deployment < deny); the explanation lists every
contributing rule from every evaluated set — the decision is never a black box.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from helios.governance.data import SEVERITY, normalize_class

# --- outcome vocabulary -------------------------------------------------------

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REQUIRE_HUMAN_REVIEW = "require_human_review"
BLOCK_DEPLOYMENT = "block_deployment"

EFFECTS = (ALLOW, DENY, REQUIRE_APPROVAL, REQUIRE_HUMAN_REVIEW, BLOCK_DEPLOYMENT)

# Strictness order for merging decisions across policy sets.
SEVERITY_OF_EFFECT = {
    ALLOW: 0,
    REQUIRE_APPROVAL: 1,
    REQUIRE_HUMAN_REVIEW: 2,
    BLOCK_DEPLOYMENT: 3,
    DENY: 4,
}

SUBJECT_KINDS = ("deployment", "model_call", "tool_action", "system")


# --- subject ------------------------------------------------------------------


@dataclass
class GovernanceSubject:
    """
    What a governance policy is evaluated against.

    All fields are plain JSON values so the subject can be serialized into a
    trace event and replayed byte-for-byte against candidate policies.
    """

    kind: str                                    # deployment | model_call | tool_action | system
    tenant_id: str = ""
    system_id: str | None = None
    environment: str | None = None
    autonomy_level: int | None = None
    risk_class: str | None = None                # system risk classification
    lifecycle: str | None = None
    data_class: str | None = None                # effective data classification
    model: dict | None = None                    # {provider, model_id, version, approval_status}
    tool: str | None = None
    capability: str | None = None                # read | write | execute | network | destructive
    action_risk: str | None = None               # low | medium | high | critical (contextual)
    owner: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "tenant_id": self.tenant_id,
            "system_id": self.system_id,
            "environment": self.environment,
            "autonomy_level": self.autonomy_level,
            "risk_class": self.risk_class,
            "lifecycle": self.lifecycle,
            "data_class": self.data_class,
            "model": self.model,
            "tool": self.tool,
            "capability": self.capability,
            "action_risk": self.action_risk,
            "owner": self.owner,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GovernanceSubject":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


@dataclass
class GovernanceDecision:
    decision: str
    reason: str
    explanation: list[str] = field(default_factory=list)
    matched_rules: list[dict] = field(default_factory=list)
    policy_versions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "explanation": list(self.explanation),
            "matched_rules": [dict(m) for m in self.matched_rules],
            "policy_versions": list(self.policy_versions),
        }


# --- rule matching --------------------------------------------------------------


def _level_matches(spec, level: int | None) -> tuple[bool, str]:
    """autonomy_level conditions: {"gte": n} | {"lte": n} | {"eq": n} | [ints]."""
    if level is None:
        return False, "autonomy_level unknown"
    if isinstance(spec, list):
        ok = level in spec
        return ok, f"autonomy_level {level} in {spec}"
    if isinstance(spec, dict):
        checks = []
        ok = True
        if "gte" in spec:
            ok &= level >= spec["gte"]
            checks.append(f"autonomy_level {level} >= {spec['gte']}")
        if "lte" in spec:
            ok &= level <= spec["lte"]
            checks.append(f"autonomy_level {level} <= {spec['lte']}")
        if "eq" in spec:
            ok &= level == spec["eq"]
            checks.append(f"autonomy_level {level} == {spec['eq']}")
        return ok, ", ".join(checks) or "autonomy_level (empty condition)"
    ok = level == spec
    return ok, f"autonomy_level {level} == {spec}"


def _data_class_matches(spec, data_class: str | None) -> tuple[bool, str]:
    """data_class conditions: {"min": "CONFIDENTIAL"} | {"in": [...]}."""
    if data_class is None:
        return False, "data_class unknown"
    severity = SEVERITY[normalize_class(data_class)]
    if isinstance(spec, dict):
        if "min" in spec:
            threshold = SEVERITY[normalize_class(spec["min"])]
            ok = severity >= threshold
            return ok, (f"data class '{data_class}' "
                        + (">=" if ok else "<") + f" '{spec['min']}'")
        if "in" in spec:
            allowed = {normalize_class(c) for c in spec["in"]}
            ok = normalize_class(data_class) in allowed
            return ok, f"data class '{data_class}' in {sorted(allowed)}"
    if isinstance(spec, list):
        allowed = {normalize_class(c) for c in spec}
        ok = normalize_class(data_class) in allowed
        return ok, f"data class '{data_class}' in {sorted(allowed)}"
    ok = normalize_class(data_class) == normalize_class(str(spec))
    return ok, f"data class == '{spec}'"


def rule_matches(rule: dict, subject: GovernanceSubject) -> tuple[bool, str]:
    """
    Evaluate a rule's `when` conditions against the subject (AND semantics).
    Returns (matched, human-readable condition trace) — the explanation is
    generated from the actual subject state, never canned text.
    """
    when = rule.get("when") or {}
    checks: list[str] = []

    kinds = when.get("subject")
    if kinds is not None:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if subject.kind not in kinds:
            return False, f"subject '{subject.kind}' not in {kinds}"
        checks.append(f"subject == '{subject.kind}'")

    env = when.get("environment")
    if env is not None:
        envs = env if isinstance(env, list) else [env]
        if subject.environment not in envs:
            return False, f"environment '{subject.environment}' not in {envs}"
        checks.append(f"environment in {envs}")

    if "autonomy_level" in when:
        ok, why = _level_matches(when["autonomy_level"], subject.autonomy_level)
        if not ok:
            return False, why
        checks.append(why)

    risk = when.get("risk_class")
    if risk is not None:
        classes = [str(r).upper() for r in (risk if isinstance(risk, list) else [risk])]
        if (subject.risk_class or "").upper() not in classes:
            return False, f"system risk '{subject.risk_class}' not in {classes}"
        checks.append(f"risk_class in {classes}")

    lifecycle = when.get("lifecycle")
    if lifecycle is not None:
        states = lifecycle if isinstance(lifecycle, list) else [lifecycle]
        if subject.lifecycle not in states:
            return False, f"lifecycle '{subject.lifecycle}' not in {states}"
        checks.append(f"lifecycle in {states}")

    if "data_class" in when:
        ok, why = _data_class_matches(when["data_class"], subject.data_class)
        if not ok:
            return False, why
        checks.append(why)

    model_spec = when.get("model")
    if model_spec is not None:
        model = subject.model or {}
        for key, pattern in (model_spec if isinstance(model_spec, dict) else {}).items():
            actual = str(model.get(key, ""))
            patterns = pattern if isinstance(pattern, list) else [pattern]
            if not any(fnmatch.fnmatch(actual, str(p)) for p in patterns):
                return False, f"model.{key} '{actual}' !~ {patterns}"
            checks.append(f"model.{key} ~ {patterns}")

    tool_pat = when.get("tool")
    if tool_pat is not None:
        if not fnmatch.fnmatch(subject.tool or "", tool_pat):
            return False, f"tool '{subject.tool}' !~ '{tool_pat}'"
        checks.append(f"tool ~ '{tool_pat}'")

    cap = when.get("capability")
    if cap is not None:
        caps = cap if isinstance(cap, list) else [cap]
        if subject.capability not in caps:
            return False, f"capability '{subject.capability}' not in {caps}"
        checks.append(f"capability in {caps}")

    min_risk = when.get("min_action_risk")
    if min_risk is not None:
        from helios.broker.types import RISK_LEVELS
        try:
            actual_idx = RISK_LEVELS.index(subject.action_risk or "")
            ok = actual_idx >= RISK_LEVELS.index(min_risk)
        except ValueError:
            ok = False  # unknown risk fails closed
        if not ok:
            return False, f"action risk '{subject.action_risk}' below '{min_risk}'"
        checks.append(f"action risk '{subject.action_risk}' >= '{min_risk}'")

    owner = when.get("owner")
    if owner is not None:
        if not fnmatch.fnmatch(subject.owner or "", owner):
            return False, f"owner '{subject.owner}' !~ '{owner}'"
        checks.append(f"owner ~ '{owner}'")

    return True, ", ".join(checks) if checks else "unconditional"


def validate_rule(rule: dict) -> list[str]:
    """Static validation of one rule (used at policy-set creation time)."""
    errors: list[str] = []
    if not rule.get("id"):
        errors.append("rule missing 'id'")
    effect = rule.get("effect")
    if effect not in EFFECTS:
        errors.append(f"rule '{rule.get('id')}': effect must be one of {EFFECTS}")
    when = rule.get("when")
    if when is not None and not isinstance(when, dict):
        errors.append(f"rule '{rule.get('id')}': 'when' must be an object")
    if isinstance(when, dict):
        kinds = when.get("subject")
        if kinds is not None:
            kinds = kinds if isinstance(kinds, list) else [kinds]
            for k in kinds:
                if k not in SUBJECT_KINDS:
                    errors.append(
                        f"rule '{rule.get('id')}': unknown subject kind '{k}' "
                        f"(expected one of {SUBJECT_KINDS})")
        for cls in ([when.get("data_class", {}).get("min")]
                    if isinstance(when.get("data_class"), dict) else []):
            if cls is not None:
                from helios.governance.data import parse_class
                try:
                    parse_class(cls)
                except ValueError:
                    errors.append(f"rule '{rule.get('id')}': unknown data class '{cls}'")
    return errors


# --- policy sets -----------------------------------------------------------------


@dataclass
class GovernancePolicySet:
    """
    An ordered, versioned governance rule set. Plain data end-to-end:
    JSON round-trippable, persistable, replayable against historical subjects.
    """

    name: str
    version: str
    rules: list[dict]
    description: str = ""
    default_effect: str = ALLOW
    scope: dict = field(default_factory=dict)  # {"system_ids": [...], "environments": [...]}

    def __post_init__(self):
        if self.default_effect not in EFFECTS:
            raise ValueError(f"default_effect must be one of {EFFECTS}")
        self.rules = [dict(r) for r in self.rules]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "default_effect": self.default_effect,
            "scope": dict(self.scope),
            "rules": [dict(r) for r in self.rules],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GovernancePolicySet":
        return cls(
            name=data["name"],
            version=data["version"],
            rules=data.get("rules") or [],
            description=data.get("description", ""),
            default_effect=data.get("default_effect", ALLOW),
            scope=data.get("scope") or {},
        )

    def applies_to(self, subject: GovernanceSubject) -> bool:
        """Scope filters: empty scope = applies to everything in the tenant."""
        system_ids = self.scope.get("system_ids")
        if system_ids and subject.system_id not in system_ids:
            return False
        environments = self.scope.get("environments")
        if environments and subject.environment not in environments:
            return False
        return True

    def evaluate(self, subject: GovernanceSubject) -> GovernanceDecision:
        """First matching rule wins; otherwise the set's default effect."""
        explanation: list[str] = []
        for rule in self.rules:
            matched, why = rule_matches(rule, subject)
            if matched:
                effect = rule["effect"]
                # block_deployment is deployment-specific; elsewhere it is a
                # hard stop (fail closed) — documented behavior, not a crash.
                if effect == BLOCK_DEPLOYMENT and subject.kind != "deployment":
                    effect = DENY
                    why += " (block_deployment escalated to deny: subject is not a deployment)"
                explanation.append(
                    f"[{self.name}@{self.version}] rule '{rule['id']}' matched: {why}")
                return GovernanceDecision(
                    decision=effect,
                    reason=rule.get("reason", rule["id"]),
                    explanation=explanation,
                    matched_rules=[{"set": self.name, "version": self.version,
                                    "rule_id": rule["id"], "effect": effect,
                                    "why": why}],
                    policy_versions=[f"{self.name}@{self.version}"],
                )
            explanation.append(
                f"[{self.name}@{self.version}] rule '{rule['id']}' did not match: {why}")

        return GovernanceDecision(
            decision=self.default_effect,
            reason=(f"no rule matched in {self.name}@{self.version} "
                    f"(default: {self.default_effect})"),
            explanation=explanation,
            matched_rules=[],
            policy_versions=[f"{self.name}@{self.version}"],
        )


# The built-in default: the canonical autonomy ceiling. Production deployments
# allow L0-L2, require approval at L3, and block L4/L5 outright.
DEFAULT_GOVERNANCE_POLICY = GovernancePolicySet(
    name="helios-governance-default",
    version="v1",
    description=(
        "Built-in HELIOS governance defaults: the canonical production "
        "autonomy ceiling (L0-L2 allow, L3 approval, L4/L5 blocked). "
        "Versioned with the product; always evaluated first."
    ),
    rules=[
        {
            "id": "prod-deploy-block-l4-plus",
            "when": {"subject": ["deployment"], "environment": ["production"],
                     "autonomy_level": {"gte": 4}},
            "effect": BLOCK_DEPLOYMENT,
            "reason": "L4/L5 autonomy may not be deployed to production",
        },
        {
            "id": "prod-deploy-l3-approval",
            "when": {"subject": ["deployment"], "environment": ["production"],
                     "autonomy_level": {"eq": 3}},
            "effect": REQUIRE_APPROVAL,
            "reason": "L3 conditional autonomy in production requires an approved deployment",
        },
        {
            "id": "prod-unrestricted-runtime-block",
            "when": {"environment": ["production"], "autonomy_level": {"gte": 5}},
            "effect": DENY,
            "reason": "L5 unrestricted autonomy is denied in production at any stage",
        },
    ],
)


def merge_decisions(decisions: list[GovernanceDecision]) -> GovernanceDecision:
    """Strictest decision wins; explanations and matched rules accumulate."""
    if not decisions:
        return GovernanceDecision(
            decision=ALLOW, reason="no governance policies apply",
            explanation=["no active governance policy sets"],
        )
    winner = max(decisions, key=lambda d: SEVERITY_OF_EFFECT[d.decision])
    explanation: list[str] = []
    matched: list[dict] = []
    versions: list[str] = []
    for decision in decisions:
        explanation.extend(decision.explanation)
        matched.extend(decision.matched_rules)
        versions.extend(decision.policy_versions)
    merged = GovernanceDecision(
        decision=winner.decision,
        reason=winner.reason,
        explanation=explanation,
        matched_rules=matched,
        policy_versions=sorted(set(versions)),
    )
    return merged
