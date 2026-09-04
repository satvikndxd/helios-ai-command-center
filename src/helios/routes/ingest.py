"""
External trace ingestion (OpenTelemetry-compatible).

HELIOS governs agents it runs — but it can also observe agents it does NOT
run. POST OTLP/JSON spans and they land in the same hierarchical trace
store (`event_type=external_span`), inspectable next to native runs.

    external agent -> OTLP/JSON -> HELIOS -> observe / evaluate

No rewrite of the external agent required: anything that can emit OTLP/JSON
(or this endpoint's simplified {spans: [...]} form) can be ingested.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.broker.trace import TraceRecorder, event_to_dict
from helios.db import get_db
from helios.models import ApiKey, TraceEvent
from helios.security import get_api_key

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


class OtlpIn(BaseModel):
    resourceSpans: list[dict] = Field(default_factory=list)
    # simplified alternative: [{"traceId", "spanId", "parentSpanId", "name",
    #                           "attributes": {...}, "durationMs": ...}]
    spans: list[dict] = Field(default_factory=list)


def _flatten_otlp(payload: OtlpIn) -> list[dict]:
    spans: list[dict] = list(payload.spans)
    for resource_span in payload.resourceSpans:
        for scope_span in resource_span.get("scopeSpans", []) or []:
            for span in scope_span.get("spans", []) or []:
                attributes = {}
                for attr in span.get("attributes", []) or []:
                    value = attr.get("value", {})
                    attributes[attr.get("key", "")] = (
                        value.get("stringValue")
                        or value.get("intValue")
                        or value.get("boolValue")
                    )
                duration_ms = 0
                try:
                    duration_ms = int(
                        (int(span.get("endTimeUnixNano", 0))
                         - int(span.get("startTimeUnixNano", 0))) / 1_000_000
                    )
                except (TypeError, ValueError):
                    pass
                spans.append({
                    "traceId": span.get("traceId", ""),
                    "spanId": span.get("spanId", ""),
                    "parentSpanId": span.get("parentSpanId"),
                    "name": span.get("name", "span"),
                    "attributes": attributes,
                    "durationMs": duration_ms,
                })
    return spans


@router.post("/otel", status_code=201)
def ingest_otel(
    payload: OtlpIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    spans = _flatten_otlp(payload)
    ingested = []
    recorders: dict[str, TraceRecorder] = {}
    for span in spans:
        trace_id = str(span.get("traceId") or "unknown")[:24]
        run_id = f"ext-{trace_id}"
        recorder = recorders.get(run_id)
        if recorder is None:
            last = (
                db.query(TraceEvent.seq)
                .filter(TraceEvent.run_id == run_id)
                .order_by(TraceEvent.seq.desc())
                .first()
            )
            recorder = TraceRecorder(
                db, tenant_id=api_key.tenant_id, run_id=run_id,
                start_seq=last[0] if last else 0,
            )
            recorders[run_id] = recorder
        event = recorder.record(
            "external_span",
            str(span.get("name", "span")),
            {
                "span_id": span.get("spanId"),
                "parent_span_id": span.get("parentSpanId"),
                "attributes": span.get("attributes") or {},
                "source": "otel",
            },
            latency_ms=int(span.get("durationMs") or 0),
        )
        ingested.append(event.id)
    return {"ingested": len(ingested), "runs": sorted(recorders)}


@router.get("/runs/{run_id}/events")
def external_run_events(
    run_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    events = (
        db.query(TraceEvent)
        .filter(TraceEvent.run_id == run_id,
                TraceEvent.tenant_id == api_key.tenant_id)
        .order_by(TraceEvent.seq)
        .limit(1000)
        .all()
    )
    return {"run_id": run_id, "events": [event_to_dict(e) for e in events]}
