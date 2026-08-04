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

    constraints: Constraints = Field(default_factory=Constraints)


class CompleteResponse(BaseModel):
    trace_id: str
    output: str

    model: dict[str, str]
    cost_usd: float
    latency_ms: int

    citations: list[Any] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)


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

    created_at: datetime
