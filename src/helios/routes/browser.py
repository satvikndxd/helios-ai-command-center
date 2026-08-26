"""
Browser session routes (Phase W3).

* Connecting a session encrypts the cookie profile into the vault and
  NEVER echoes it back.  No vault key configured -> 503, fail closed.
* Fresh-context reads are policy-checked like any other read.
* Authenticated reads (with a session) additionally require an approved
  ApprovalRequest bound to that exact (url, session) payload.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.models import ApiKey, BrowserSession, WebAccessJob
from helios.security import get_api_key
from helios.web.actions import ActionDenied, require_approval
from helios.web.browser import BrowserDenied, BrowserWorker
from helios.web.policy import WebAccessPolicy
from helios.web.vault import VaultError, encrypt_profile

router = APIRouter(tags=["browser"])


class SessionIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=100)
    domain_allowlist: list[str] = Field(min_length=1)
    cookies: dict[str, str] = Field(default_factory=dict)


class BrowserReadIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


def _serialize(session: BrowserSession) -> dict:
    # encrypted_profile is intentionally absent: cookies never leave the vault.
    return {
        "id": session.id,
        "user_id": session.user_id,
        "source": session.source,
        "domain_allowlist": session.domain_allowlist,
        "status": session.status,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "last_used_at": session.last_used_at.isoformat() if session.last_used_at else None,
    }


@router.post("/v1/browser/sessions", status_code=201)
async def connect_session(
    payload: SessionIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Connect a user browser session. Cookies go straight into the vault."""
    try:
        blob = encrypt_profile({"cookies": payload.cookies})
    except VaultError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    session = BrowserSession(
        tenant_id=api_key.tenant_id,
        user_id=payload.user_id,
        source=payload.source,
        domain_allowlist=payload.domain_allowlist,
        encrypted_profile=blob,
    )
    db.add(session)
    db.commit()
    return _serialize(session)


@router.get("/v1/browser/sessions")
async def list_sessions(
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    sessions = (
        db.query(BrowserSession)
        .filter(BrowserSession.tenant_id == api_key.tenant_id)
        .all()
    )
    return {"sessions": [_serialize(s) for s in sessions]}


@router.delete("/v1/browser/sessions/{session_id}")
async def disconnect_session(
    session_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    session = (
        db.query(BrowserSession)
        .filter(
            BrowserSession.id == session_id,
            BrowserSession.tenant_id == api_key.tenant_id,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    db.delete(session)
    db.commit()
    return {"deleted": session_id}


@router.post("/v1/browser/read")
async def browser_read(
    payload: BrowserReadIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Read a page through the browser worker.

    Fresh context (no session): domain must pass the standard web policy.
    With a session: an approved `browser_read_authenticated` approval bound
    to this exact url+session is required, and the session's own domain
    allowlist applies inside the worker.
    """
    session = None
    if payload.session_id:
        session = (
            db.query(BrowserSession)
            .filter(
                BrowserSession.id == payload.session_id,
                BrowserSession.tenant_id == api_key.tenant_id,
                BrowserSession.status == "active",
            )
            .first()
        )
        if not session:
            raise HTTPException(status_code=404, detail="session not found")
        try:
            require_approval(
                db,
                api_key.tenant_id,
                "browser_read_authenticated",
                {"url": payload.url, "session_id": payload.session_id},
            )
        except ActionDenied as exc:
            raise HTTPException(
                status_code=403,
                detail={"approval_required": True, "reason": str(exc)},
            ) from exc
    else:
        policy = WebAccessPolicy()
        if not policy.domain_allowed(payload.url):
            raise HTTPException(
                status_code=403,
                detail={"blocked": True, "reason": "domain not on the public allowlist"},
            )

    worker = BrowserWorker()
    try:
        document = worker.read_page(payload.url, session=session)
    except BrowserDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except VaultError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if session is not None:
        from datetime import datetime, timezone

        session.last_used_at = datetime.now(timezone.utc)

    job = WebAccessJob(
        tenant_id=api_key.tenant_id,
        operation="browser_read",
        request={"url": payload.url, "session_id": payload.session_id},
        status="completed",
        policy_decision={"allowed": True},
        source_status=[],
        documents_meta=[
            {
                "url": document.url,
                "title": document.title,
                "content_sha256": document.content_hash(),
                "trust": document.trust,
                "events": worker.events,  # navigate/read/blocked — no cookies
            }
        ],
    )
    db.add(job)
    db.commit()

    return {
        "job_id": job.id,
        "events": worker.events,
        "document": document.model_dump(mode="json"),
    }
