"""
Typed schemas for the workflow layer.

Everything domain-specific is CONFIGURATION on these models; the engine and
governance core contain no industry conditionals.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field

# Explicit failure states — never fabricate.
STATUS_COMPLETED = "completed"
STATUS_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
STATUS_INVALID_INPUT = "invalid_input"
STATUS_BLOCKED = "blocked"
STATUS_ERROR = "error"

RISK_LEVELS = ["informational", "low", "medium", "high", "critical"]

# Reasoning categories every claim must declare.
KIND_FACT = "fact"
KIND_COMPUTATION = "computation"
KIND_INTERPRETATION = "interpretation"
KIND_RECOMMENDATION = "recommendation"


class RetrievalConfig(BaseModel):
    enabled: bool = False
    query_template: str = "{input}"  # .format(**input) over workflow input
    top_k: int = Field(default=4, ge=1, le=20)


class RiskRule(BaseModel):
    """Config-driven escalation: `fact` compared against `value` -> `risk`."""

    fact: str          # computed fact name, e.g. "threshold_violations"
    op: str = "gt"     # gt | gte | lt | lte | eq | nonzero
    value: float = 0
    risk: str = "high"


class ApprovalConfig(BaseModel):
    required_for: list[str] = Field(default_factory=lambda: ["high", "critical"])
    action: str | None = None  # typed action proposed for consequential follow-up


class ReasoningConfig(BaseModel):
    task_template: str  # .format(**input); the analyst instruction
    output_guidance: str = (
        "Structure the answer as INTERPRETATION (what the evidence shows) and "
        "RECOMMENDATION (what to do next). Only use the COMPUTED FACTS and "
        "EVIDENCE provided. If the evidence is insufficient, reply exactly "
        "INSUFFICIENT_EVIDENCE."
    )


class WorkflowDefinition(BaseModel):
    id: str
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)  # {"required": [...]}
    source_types: list[str] = Field(default_factory=list)
    require_sources: bool = True
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    analysis_steps: list[str] = Field(default_factory=list)  # pack step names
    reasoning: ReasoningConfig
    base_risk: str = "informational"
    risk_rules: list[RiskRule] = Field(default_factory=list)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    output_schema: list[str] = Field(
        default_factory=lambda: ["facts", "interpretation", "recommendation"]
    )
    audit: dict[str, Any] = Field(default_factory=dict)


class ActionSpec(BaseModel):
    name: str
    risk: str = "high"
    description: str


class WorkspaceConfig(BaseModel):
    id: str
    name: str
    description: str
    domain: str
    terminology: dict[str, str] = Field(default_factory=dict)
    system_instructions: str = ""
    source_types: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    workflows: list[WorkflowDefinition] = Field(default_factory=list)
    actions: list[ActionSpec] = Field(default_factory=list)
    policies: dict[str, Any] = Field(default_factory=dict)
    risk_config: dict[str, Any] = Field(default_factory=dict)
    evaluation_config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def workflow(self, workflow_id: str) -> WorkflowDefinition | None:
        return next((w for w in self.workflows if w.id == workflow_id), None)


class WorkspacePack(BaseModel):
    """A complete domain pack: config + deterministic steps + demo data."""

    model_config = {"arbitrary_types_allowed": True}

    config: WorkspaceConfig
    # name -> fn(input, sources, workspace) -> {"facts": [...], ...}
    analysis_steps: dict[str, Callable] = Field(default_factory=dict)
    seed_sources: list[dict] = Field(default_factory=list)
    seed_documents: list[dict] = Field(default_factory=list)  # {title, content}
    seed_relationships: list[dict] = Field(default_factory=list)
    # {source: (name, type), relationship_type, target: (name, type)}


class Fact(BaseModel):
    """A deterministically computed fact — the source of truth for numbers."""

    name: str
    value: Any
    unit: str | None = None
    detail: str | None = None
    kind: str = KIND_COMPUTATION


class Evidence(BaseModel):
    """One piece of evidence with provenance."""

    kind: str  # computation | record | document
    source: str  # e.g. "deterministic-analysis", source name, document title
    reference: str | None = None  # source/document/chunk id
    excerpt: str | None = None
    trust: str = "internal"
    score: float | None = None
    timestamp: str | None = None


class Claim(BaseModel):
    """An output claim tied to its evidence and reasoning category."""

    claim: str
    category: str  # fact | computation | interpretation | recommendation
    evidence_refs: list[int] = Field(default_factory=list)  # indices into evidence
    confidence: float = 0.5
    timestamp: str | None = None
