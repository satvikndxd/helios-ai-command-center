from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Constraints(BaseModel):
    """
    Request-level constraints.

    Phase 1 captures them; later phases enforce them more deeply.
    """

    max_latency_ms: int | None = None
    max_cost_usd: float | None = None
    allowed_data_classes: list[str] = Field(default_factory=list)


class CompleteRequest(BaseModel):
    """
    Unified Helios completion request.

    In Phase 1, `input` is usually a string, but we keep it flexible.
    """

    input: Any

    task_type: str = "completion"
    risk_level: str = "low"

    session_id: str | None = None
    user_id: str | None = None

    context: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)

    provider: str | None = None
    model: str | None = None

    # Phase 2: ground the completion in the tenant's knowledge base.
    use_knowledge_base: bool = False
    top_k: int | None = Field(default=None, ge=1, le=20)

    constraints: Constraints = Field(default_factory=Constraints)


class CompleteResponse(BaseModel):
    trace_id: str
    output: str

    model: dict[str, str]
    cost_usd: float
    latency_ms: int

    citations: list[Any] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)


class DocumentIn(BaseModel):
    """Payload for knowledge-base document ingestion."""

    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    title: str
    chunk_count: int
    created_at: datetime


class FeedbackIn(BaseModel):
    """User/business feedback on a decision (FR-EV-008)."""

    rating: str = Field(pattern="^(up|down)$")
    comment: str | None = None
    outcome: str | None = None  # e.g. "ticket_resolved", "answer_rejected"


class ReviewResolveIn(BaseModel):
    verdict: str = Field(pattern="^(approve|reject)$")
    notes: str | None = None


class ReviewItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    trace_id: str
    reason: str
    status: str
    resolution: dict | None
    created_at: datetime


class DatasetBuildIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    source: str = Field(default="failures", pattern="^(failures|negative_feedback|all)$")
    limit: int = Field(default=100, ge=1, le=1000)


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    name: str
    version: int
    kind: str
    source: str
    item_count: int
    created_at: datetime


class SimulationRunIn(BaseModel):
    candidate_provider: str = "mock"
    candidate_model: str | None = None
    limit: int = Field(default=20, ge=1, le=200)
    task_type: str | None = None


class SimulationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    params: dict
    report: dict | None
    status: str
    created_at: datetime


class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    type: str
    name: str
    confidence: float
    created_at: datetime


class TraceOut(BaseModel):
    """
    Public trace representation for Phase 1.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    application_id: str

    session_id: str | None
    user_id: str | None

    task_type: str
    risk_level: str

    input_payload: dict
    request_payload: dict

    model_provider: str
    model_id: str

    output_text: str | None
    response_payload: dict | None

    citations: list
    tool_calls: list

    cost_usd: float
    latency_ms: int

    policy_result: dict | None
    evaluation_scores: dict | None

    status: str
    error: dict | None
    feedback: dict | None = None

    created_at: datetime
