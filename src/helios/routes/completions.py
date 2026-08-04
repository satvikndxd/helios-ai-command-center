import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from helios.config import settings
from helios.cost import compute_cost
from helios.db import get_db
from helios.models import ApiKey, DecisionTrace
from helios.normalization import normalize_request
from helios.providers import choose_provider_model
from helios.schemas import CompleteRequest, CompleteResponse
from helios.security import get_api_key


router = APIRouter(tags=["completions"])


@router.post("/v1/ai/complete", response_model=CompleteResponse)
async def create_completion(
    payload: CompleteRequest,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Unified Helios AI endpoint.

    Phase 1 responsibilities:
    - authenticate
    - normalize
    - choose provider
    - call provider
    - capture trace
    - return response
    """

    started_at = time.perf_counter()
    trace_id = str(uuid.uuid4())

    normalized = normalize_request(payload, api_key)

    try:
        provider, model_id, provider_name = choose_provider_model(normalized, settings)

        normalized["provider"] = provider_name
        normalized["model"] = model_id

        result = await provider.complete(normalized, settings)

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        cost_usd = compute_cost(model_id, result.usage, provider_name)

        trace = DecisionTrace(
            id=trace_id,
            tenant_id=api_key.tenant_id,
            application_id=api_key.application_id,
            session_id=payload.session_id,
            user_id=payload.user_id,
            task_type=payload.task_type,
            risk_level=payload.risk_level,
            input_payload={
                "input": payload.input,
                "context": payload.context,
                "parameters": payload.parameters,
                "constraints": payload.constraints.model_dump(),
            },
            request_payload=normalized,
            model_provider=provider_name,
            model_id=model_id,
            output_text=result.output_text,
            response_payload=result.raw,
            citations=result.citations,
            tool_calls=[],
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            policy_result={
                "action": "allow",
                "stage": "phase1",
                "notes": "No policy engine yet.",
            },
            evaluation_scores=None,
            status="success",
            error=None,
        )

        db.add(trace)
        db.commit()

        return CompleteResponse(
            trace_id=trace_id,
            output=result.output_text,
            model={
                "provider": provider_name,
                "model_id": model_id,
            },
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            citations=result.citations,
            policy=trace.policy_result or {},
        )

    except Exception as exc:
        latency_ms = int((time.perf_counter() - started_at) * 1000)

        # Persist an error trace if possible.
        # This is important: failed AI decisions are still decisions.
        try:
            trace = DecisionTrace(
                id=trace_id,
                tenant_id=api_key.tenant_id,
                application_id=api_key.application_id,
                session_id=payload.session_id,
                user_id=payload.user_id,
                task_type=payload.task_type,
                risk_level=payload.risk_level,
                input_payload={
                    "input": payload.input,
                    "context": payload.context,
                    "parameters": payload.parameters,
                    "constraints": payload.constraints.model_dump(),
                },
                request_payload=normalized,
                model_provider=normalized.get("provider", "unknown"),
                model_id=normalized.get("model", "unknown"),
                output_text=None,
                response_payload=None,
                citations=[],
                tool_calls=[],
                cost_usd=0.0,
                latency_ms=latency_ms,
                policy_result={
                    "action": "error",
                    "stage": "phase1",
                    "notes": "Provider or gateway failure.",
                },
                evaluation_scores=None,
                status="error",
                error={
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                },
            )

            db.add(trace)
            db.commit()

        except Exception:
            db.rollback()

        raise HTTPException(
            status_code=502,
            detail={
                "error": str(exc),
                "trace_id": trace_id,
            },
        )
