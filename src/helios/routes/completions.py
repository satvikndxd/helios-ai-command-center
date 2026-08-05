import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from helios.config import settings
from helios.cost import compute_cost
from helios.db import get_db
from helios.models import ApiKey, DecisionTrace, EvaluationJob
from helios.normalization import normalize_request
from helios.providers import choose_provider_model
from helios.retrieval import build_context_prompt, chunks_to_citations, search
from helios.schemas import CompleteRequest, CompleteResponse
from helios.security import get_api_key


router = APIRouter(tags=["completions"])
logger = logging.getLogger("helios.gateway")


def _enqueue_evaluation(db: Session, trace_id: str) -> None:
    """
    Enqueue async (cold-path) evaluation for a trace.

    Best-effort by design: the completion has already succeeded and been
    returned to the caller conceptually, so a queue hiccup must never turn a
    good AI decision into a 5xx. Failures are logged, not raised.
    """
    try:
        db.add(EvaluationJob(trace_id=trace_id, status="pending"))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("failed to enqueue evaluation job for trace %s", trace_id)


@router.post("/v1/ai/complete", response_model=CompleteResponse)
async def create_completion(
    payload: CompleteRequest,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Unified Helios AI endpoint.

    Responsibilities:
    - authenticate
    - normalize
    - (optional) retrieve grounded context from the tenant's knowledge base
    - choose provider
    - call provider
    - capture trace (with citations)
    - return response
    """

    started_at = time.perf_counter()
    trace_id = str(uuid.uuid4())

    normalized = normalize_request(payload, api_key)
    citations: list[dict] = []

    try:
        # Phase 2: RAG. Retrieval failures fail the request loudly — silently
        # answering WITHOUT the knowledge base the caller asked for would be an
        # ungrounded response masquerading as a grounded one.
        if payload.use_knowledge_base:
            retrieved = await search(
                db=db,
                tenant_id=api_key.tenant_id,
                query=normalized["input_text"],
                settings=settings,
                top_k=payload.top_k,
            )
            if retrieved:
                normalized["retrieved_context"] = [
                    {
                        "chunk_id": c.chunk_id,
                        "document_id": c.document_id,
                        "title": c.document_title,
                        "score": round(c.score, 4),
                    }
                    for c in retrieved
                ]
                normalized["input_text"] = build_context_prompt(
                    retrieved, normalized["input_text"]
                )
                citations = chunks_to_citations(retrieved)
            else:
                normalized["retrieved_context"] = []

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
            citations=citations or result.citations,
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

        # Hand off to the cold path. Never blocks or breaks the hot path.
        _enqueue_evaluation(db, trace_id)

        return CompleteResponse(
            trace_id=trace_id,
            output=result.output_text,
            model={
                "provider": provider_name,
                "model_id": model_id,
            },
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            citations=citations or result.citations,
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
