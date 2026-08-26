"""
Web access routes — the governed research read path (Phase W1).

Every dispatch: policy preflight -> adapter fallback chain -> sanitization
-> normalized documents with provenance + per-source status, persisted as a
WebAccessJob for auditability.  Policy denials return 403 and are persisted
too (blocked jobs are evidence, same as blocked completions).
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.models import ApiKey, WebAccessJob
from helios.security import get_api_key
from helios.web.broker import WebAccessBroker, default_broker
from helios.web.types import WebAccessRequest

router = APIRouter(tags=["web"])


@lru_cache(maxsize=1)
def get_broker() -> WebAccessBroker:
    return default_broker()


class WebSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    sources: list[str] = Field(default_factory=list)
    max_results: int = Field(default=10, ge=1, le=50)


class WebReadIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    sources: list[str] = Field(default_factory=list)


class WebTranscriptIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


def _run(
    request: WebAccessRequest,
    api_key: ApiKey,
    db: Session,
    broker: WebAccessBroker,
) -> dict:
    decision, documents, statuses = broker.dispatch(request)

    job = WebAccessJob(
        tenant_id=api_key.tenant_id,
        operation=request.operation,
        request=request.model_dump(),
        status="completed" if decision.allowed else "blocked",
        policy_decision=decision.model_dump(),
        source_status=[s.model_dump() for s in statuses],
        documents_meta=[
            {
                "url": d.url,
                "title": d.title,
                "source": d.source,
                "adapter": d.source_adapter,
                "content_sha256": d.content_hash(),
                "trust": d.trust,
                "warnings": d.warnings,
            }
            for d in documents
        ],
    )
    db.add(job)
    db.commit()

    if not decision.allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "blocked": True,
                "job_id": job.id,
                "requires_approval": decision.requires_approval,
                "reasons": decision.reasons,
            },
        )

    return {
        "job_id": job.id,
        "operation": request.operation,
        "policy": decision.model_dump(),
        "source_status": [s.model_dump() for s in statuses],
        "documents": [d.model_dump(mode="json") for d in documents],
    }


@router.get("/v1/web/sources")
async def list_sources(
    api_key: ApiKey = Depends(get_api_key),
    broker: WebAccessBroker = Depends(get_broker),
):
    """Adapter registry with version, trust level, capabilities, and health."""
    return {"sources": broker.sources()}


@router.post("/v1/web/search")
async def web_search(
    payload: WebSearchIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
    broker: WebAccessBroker = Depends(get_broker),
):
    request = WebAccessRequest(
        operation="search",
        query=payload.query,
        sources=payload.sources,
        max_results=payload.max_results,
    )
    return _run(request, api_key, db, broker)


@router.post("/v1/web/read")
async def web_read(
    payload: WebReadIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
    broker: WebAccessBroker = Depends(get_broker),
):
    request = WebAccessRequest(
        operation="read", url=payload.url, sources=payload.sources, max_results=1
    )
    return _run(request, api_key, db, broker)


@router.post("/v1/web/transcript")
async def web_transcript(
    payload: WebTranscriptIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
    broker: WebAccessBroker = Depends(get_broker),
):
    request = WebAccessRequest(operation="transcript", url=payload.url, max_results=1)
    return _run(request, api_key, db, broker)


@router.get("/v1/web/jobs")
async def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Recent web access jobs for this tenant (audit view)."""
    jobs = (
        db.query(WebAccessJob)
        .filter(WebAccessJob.tenant_id == api_key.tenant_id)
        .order_by(WebAccessJob.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "jobs": [
            {
                "id": j.id,
                "operation": j.operation,
                "status": j.status,
                "source_status": j.source_status,
                "documents": len(j.documents_meta),
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            for j in jobs
        ]
    }
