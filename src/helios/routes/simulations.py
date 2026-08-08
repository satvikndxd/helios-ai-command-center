from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from helios.config import settings
from helios.db import get_db
from helios.evaluators import default_pipeline
from helios.models import ApiKey, DecisionTrace, SimulationRun
from helios.providers import get_provider
from helios.registry import default_model_for
from helios.schemas import SimulationOut, SimulationRunIn
from helios.security import get_api_key


router = APIRouter(tags=["simulations"])


@router.post("/v1/simulations/run", response_model=SimulationOut, status_code=201)
async def run_simulation(
    payload: SimulationRunIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Helios Simulator (FR-SIM-002/005): replay recent production traffic against
    a candidate provider/model and produce a deployment risk report comparing
    evaluation pass-rates against the originals.

    Replayed outputs are evaluated with the SAME pipeline as production, so
    the comparison is apples-to-apples. Nothing is persisted as live traffic.
    """
    query = (
        db.query(DecisionTrace)
        .filter(
            DecisionTrace.tenant_id == api_key.tenant_id,
            DecisionTrace.status == "success",
        )
        .order_by(DecisionTrace.created_at.desc())
    )
    if payload.task_type:
        query = query.filter(DecisionTrace.task_type == payload.task_type)
    traces = query.limit(payload.limit).all()

    if not traces:
        raise HTTPException(status_code=422, detail="No successful traces to replay")

    provider = get_provider(payload.candidate_provider, settings)
    model_id = payload.candidate_model or default_model_for(
        payload.candidate_provider, settings
    )
    pipeline = default_pipeline()

    replayed = 0
    candidate_failures = 0
    baseline_failures = 0
    provider_errors = 0
    failure_examples: list[dict] = []

    for trace in traces:
        # Baseline: production evaluation result (cold path may still be pending).
        base_scores = trace.evaluation_scores or {}
        base_failed = any(not s.get("passed", True) for s in base_scores.values())
        if base_failed:
            baseline_failures += 1

        # Candidate: replay the ORIGINAL prompt (incl. retrieved context).
        request = {
            "input_text": (trace.request_payload or {}).get(
                "input_text", str((trace.input_payload or {}).get("input", ""))
            ),
            "parameters": (trace.request_payload or {}).get("parameters", {}),
            "model": model_id,
        }
        try:
            result = await provider.complete(request, settings)
        except Exception as exc:  # noqa: BLE001
            provider_errors += 1
            failure_examples.append(
                {"trace_id": trace.id, "error": str(exc)[:200]}
            )
            continue

        # Evaluate the candidate output with the same pipeline (in-memory trace).
        pseudo = DecisionTrace(
            tenant_id=trace.tenant_id,
            application_id=trace.application_id,
            task_type=trace.task_type,
            risk_level=trace.risk_level,
            input_payload=trace.input_payload,
            request_payload=trace.request_payload,
            model_provider=payload.candidate_provider,
            model_id=model_id,
            output_text=result.output_text,
            citations=trace.citations or [],
            tool_calls=[],
            cost_usd=0.0,
            latency_ms=0,
            status="success",
        )
        scores = pipeline.run(pseudo)
        failed = [k for k, s in scores.items() if not s.get("passed", True)]
        if failed:
            candidate_failures += 1
            if len(failure_examples) < 5:
                failure_examples.append(
                    {
                        "trace_id": trace.id,
                        "failed_evaluators": failed,
                        "output_preview": (result.output_text or "")[:160],
                    }
                )
        replayed += 1

    baseline_rate = round(baseline_failures / len(traces), 4)
    candidate_rate = round(candidate_failures / replayed, 4) if replayed else 1.0
    regression = candidate_rate > baseline_rate

    report = {
        "traces_sampled": len(traces),
        "replayed": replayed,
        "provider_errors": provider_errors,
        "baseline_failure_rate": baseline_rate,
        "candidate_failure_rate": candidate_rate,
        "failure_rate_delta": round(candidate_rate - baseline_rate, 4),
        "regression_detected": regression,
        "failure_examples": failure_examples,
        "recommendation": (
            "do_not_deploy"
            if regression or provider_errors
            else "canary_1_percent"
        ),
    }

    run = SimulationRun(
        tenant_id=api_key.tenant_id,
        params={
            "candidate_provider": payload.candidate_provider,
            "candidate_model": model_id,
            "limit": payload.limit,
            "task_type": payload.task_type,
        },
        report=report,
        status="completed",
    )
    db.add(run)
    db.commit()
    return run


@router.get("/v1/simulations/{run_id}", response_model=SimulationOut)
def get_simulation(
    run_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    run = (
        db.query(SimulationRun)
        .filter(SimulationRun.id == run_id, SimulationRun.tenant_id == api_key.tenant_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Simulation not found")
    return run
