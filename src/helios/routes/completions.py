import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from helios import policy as policy_engine
from helios import sentinel
from helios.config import settings
from helios.cost import compute_cost
from helios.db import get_db
from helios.models import ApiKey, DecisionTrace, EvaluationJob
from helios.normalization import normalize_request
from helios.providers import get_provider
from helios.registry import default_model_for, select_route
from helios.retrieval import build_context_prompt, chunks_to_citations, search
from helios.schemas import CompleteRequest, CompleteResponse
from helios.security import get_api_key


router = APIRouter(tags=["completions"])
logger = logging.getLogger("helios.gateway")


def _enqueue_evaluation(db: Session, trace_id: str) -> None:
    """Best-effort cold-path handoff; must never break the hot path."""
    try:
        db.add(EvaluationJob(trace_id=trace_id, status="pending"))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("failed to enqueue evaluation job for trace %s", trace_id)


def _persist_trace(db: Session, **kwargs) -> None:
    try:
        db.add(DecisionTrace(**kwargs))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("failed to persist trace %s", kwargs.get("id"))


def _base_trace_fields(trace_id: str, payload: CompleteRequest, api_key: ApiKey) -> dict:
    return {
        "id": trace_id,
        "tenant_id": api_key.tenant_id,
        "application_id": api_key.application_id,
        "session_id": payload.session_id,
        "user_id": payload.user_id,
        "task_type": payload.task_type,
        "risk_level": payload.risk_level,
        "input_payload": {
            "input": payload.input,
            "context": payload.context,
            "parameters": payload.parameters,
            "constraints": payload.constraints.model_dump(),
        },
    }


@router.post("/v1/ai/complete", response_model=CompleteResponse)
async def create_completion(
    payload: CompleteRequest,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Unified Helios AI endpoint — the hot path.

    authenticate -> normalize -> sentinel scan -> route -> policy preflight
    -> (optional) retrieve grounded context (untrusted; injection-scanned)
    -> provider call with fallback chain -> sentinel/policy output checks
    -> trace capture -> async evaluation handoff -> respond
    """
    started_at = time.perf_counter()
    trace_id = str(uuid.uuid4())

    normalized = normalize_request(payload, api_key)
    citations: list[dict] = []
    report = sentinel.SentinelReport()

    # ---- Sentinel: input scanning -------------------------------------------
    report.pii = sentinel.detect_pii(normalized["input_text"])
    report.injection_matches = sentinel.detect_injection(normalized["input_text"])

    # ---- Router v2: explainable candidate chain -----------------------------
    decision = select_route(
        requested_provider=normalized.get("requested_provider"),
        risk_level=payload.risk_level,
        input_text=normalized["input_text"],
        max_cost_usd=payload.constraints.max_cost_usd,
        settings=settings,
    )
    normalized["routing"] = decision.to_dict()

    # ---- Policy: preflight ---------------------------------------------------
    pre = policy_engine.preflight(
        risk_level=payload.risk_level,
        pii=report.pii,
        injection_matches=report.injection_matches,
        provider_name=decision.chain[0],
    )
    policy_record = {"preflight": pre.to_dict(), "sentinel": report.to_dict()}

    def _latency() -> int:
        return int((time.perf_counter() - started_at) * 1000)

    if pre.action == "deny":
        _persist_trace(
            db,
            **_base_trace_fields(trace_id, payload, api_key),
            request_payload=normalized,
            model_provider="none",
            model_id="none",
            output_text=None,
            response_payload=None,
            citations=[],
            tool_calls=[],
            cost_usd=0.0,
            latency_ms=_latency(),
            policy_result={**policy_record, "action": "deny", "stage": "preflight"},
            evaluation_scores=None,
            status="blocked",
            error=None,
        )
        raise HTTPException(
            status_code=403,
            detail={"trace_id": trace_id, "policy": pre.to_dict()},
        )

    if pre.action == "redact":
        redacted, _counts = sentinel.redact_pii(normalized["input_text"])
        normalized["input_text"] = redacted
        normalized["pii_redacted"] = True

    try:
        # ---- Retrieval (retrieved content is UNTRUSTED) ---------------------
        if payload.use_knowledge_base:
            retrieved = await search(
                db=db,
                tenant_id=api_key.tenant_id,
                query=normalized["input_text"],
                settings=settings,
                top_k=payload.top_k,
            )
            # Drop chunks containing injection payloads (poisoned documents).
            clean = []
            for c in retrieved:
                if sentinel.detect_injection(c.content):
                    report.dropped_chunks.append(c.chunk_id)
                else:
                    clean.append(c)
            retrieved = clean
            if retrieved:
                normalized["retrieved_context"] = [
                    {
                        "chunk_id": c.chunk_id,
                        "document_id": c.document_id,
                        "title": c.document_title,
                        "content": c.content,
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
            policy_record["sentinel"] = report.to_dict()

        # ---- Provider call with fallback chain (FR-RT-005) ------------------
        result = None
        provider_name = decision.chain[0]
        model_id = "unknown"
        last_error: Exception | None = None
        for candidate in decision.chain:
            model_id = normalized.get("requested_model") or default_model_for(
                candidate, settings
            )
            normalized["provider"] = candidate
            normalized["model"] = model_id
            try:
                provider = get_provider(candidate, settings)
                result = await provider.complete(normalized, settings)
                provider_name = candidate
                decision.attempts.append({"provider": candidate, "ok": True})
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                decision.attempts.append(
                    {"provider": candidate, "ok": False, "error": str(exc)}
                )
        normalized["routing"] = decision.to_dict()
        if result is None:
            raise last_error or RuntimeError("no provider available")

        # ---- Sentinel + policy: output checks -------------------------------
        report.output_leaks = sentinel.scan_output_for_leaks(result.output_text)
        out = policy_engine.output_check(
            task_type=payload.task_type,
            risk_level=payload.risk_level,
            citations=citations,
            output_leaks=report.output_leaks,
        )
        policy_record["output"] = out.to_dict()
        policy_record["sentinel"] = report.to_dict()

        latency_ms = _latency()
        cost_usd = compute_cost(model_id, result.usage, provider_name)

        if out.action == "deny":
            _persist_trace(
                db,
                **_base_trace_fields(trace_id, payload, api_key),
                request_payload=normalized,
                model_provider=provider_name,
                model_id=model_id,
                output_text=result.output_text,
                response_payload=result.raw,
                citations=citations,
                tool_calls=[],
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                policy_result={**policy_record, "action": "deny", "stage": "output"},
                evaluation_scores=None,
                status="blocked",
                error=None,
            )
            _enqueue_evaluation(db, trace_id)
            raise HTTPException(
                status_code=403,
                detail={"trace_id": trace_id, "policy": out.to_dict()},
            )

        final_policy = {**policy_record, "action": "allow", "stage": "output"}
        _persist_trace(
            db,
            **_base_trace_fields(trace_id, payload, api_key),
            request_payload=normalized,
            model_provider=provider_name,
            model_id=model_id,
            output_text=result.output_text,
            response_payload=result.raw,
            citations=citations or result.citations,
            tool_calls=[],
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            policy_result=final_policy,
            evaluation_scores=None,
            status="success",
            error=None,
        )
        _enqueue_evaluation(db, trace_id)

        return CompleteResponse(
            trace_id=trace_id,
            output=result.output_text,
            model={"provider": provider_name, "model_id": model_id},
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            citations=citations or result.citations,
            policy=final_policy,
        )

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # Failed AI decisions are still decisions: persist an error trace.
        _persist_trace(
            db,
            **_base_trace_fields(trace_id, payload, api_key),
            request_payload=normalized,
            model_provider=normalized.get("provider", "unknown"),
            model_id=normalized.get("model", "unknown"),
            output_text=None,
            response_payload=None,
            citations=[],
            tool_calls=[],
            cost_usd=0.0,
            latency_ms=_latency(),
            policy_result={**policy_record, "action": "error", "stage": "gateway"},
            evaluation_scores=None,
            status="error",
            error={"type": exc.__class__.__name__, "message": str(exc)},
        )
        raise HTTPException(
            status_code=502,
            detail={"error": str(exc), "trace_id": trace_id},
        )
