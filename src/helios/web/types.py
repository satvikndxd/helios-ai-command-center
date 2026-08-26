"""
Normalized types for the web access plane.

Every retrieved item becomes a `SourceDocument`: extracted content **plus
provenance**, never just a text blob.  The `trust` label is set to
`untrusted_external_content` and must survive prompt assembly, TUI
rendering, storage, and evaluation.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

# The one and only trust label for externally-retrieved content.
UNTRUSTED = "untrusted_external_content"

# Operation risk classes. Reads may be automatic; writes always need approval.
READ_OPERATIONS = {"search", "read", "transcript", "health"}
WRITE_OPERATIONS = {"post", "send", "delete", "like", "follow", "purchase", "update"}


class SourceCapabilities(BaseModel):
    search: bool = False
    read: bool = False
    transcript: bool = False
    authenticated: bool = False  # requires an approved session/credential


class HealthStatus(BaseModel):
    adapter: str
    status: str  # "healthy" | "degraded" | "rate_limited" | "unconfigured" | "unavailable"
    detail: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WebAccessRequest(BaseModel):
    """A broker-level request. The plan is not permission — policy runs next."""

    operation: str  # "search" | "read" | "transcript"
    query: str | None = None
    url: str | None = None
    sources: list[str] = Field(default_factory=list)  # preferred adapter order
    max_results: int = Field(default=10, ge=1, le=50)
    risk_level: str = "low"


class SourceDocument(BaseModel):
    """Normalized retrieval result with provenance. Content is evidence."""

    source: str
    operation: str
    url: str | None = None
    title: str | None = None
    author: str | None = None
    published_at: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content: str = ""
    content_type: str = "page"  # page | post | issue | transcript | feed
    trust: str = UNTRUSTED
    source_adapter: str = ""
    adapter_version: str = ""
    citations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


class SourceStatus(BaseModel):
    """
    Per-source outcome for a broker dispatch — the failure-honesty record.
    If X was rate-limited we say so; we never fabricate a successful search.
    """

    source: str
    status: str  # "ok" | "unavailable" | "rate_limited" | "skipped" | "error" | "unconfigured"
    detail: str | None = None
    results: int = 0


class PolicyDecision(BaseModel):
    allowed: bool
    requires_approval: bool = False
    reasons: list[str] = Field(default_factory=list)


class AdapterUnavailable(Exception):
    """Adapter cannot serve the request (unconfigured, down, unsupported)."""


class AdapterRateLimited(AdapterUnavailable):
    """Adapter is rate limited — must be reported, never papered over."""
