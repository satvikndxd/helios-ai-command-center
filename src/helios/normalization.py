from typing import Any

from helios.models import ApiKey
from helios.schemas import CompleteRequest


def input_to_text(value: Any) -> str:
    """
    Convert arbitrary input payloads into provider-facing text.

    Phase 1 is intentionally simple:
    - string stays string
    - dict/list becomes str(...)

    Later phases should support structured prompts, tool schemas, and multimodal input.
    """

    if isinstance(value, str):
        return value

    return str(value)


def normalize_request(request: CompleteRequest, api_key: ApiKey) -> dict[str, Any]:
    """
    Normalize incoming request into Helios' internal decision schema.

    This normalized payload becomes part of the DecisionTrace.
    """

    return {
        "task_type": request.task_type,
        "risk_level": request.risk_level,
        "session_id": request.session_id,
        "user_id": request.user_id,
        "input": request.input,
        "input_text": input_to_text(request.input),
        "context": request.context,
        "parameters": request.parameters,
        "constraints": request.constraints.model_dump(),
        "requested_provider": request.provider,
        "requested_model": request.model,
        "tenant_id": api_key.tenant_id,
        "application_id": api_key.application_id,
    }
