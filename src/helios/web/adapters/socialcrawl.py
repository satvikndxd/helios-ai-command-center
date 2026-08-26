"""
SocialCrawl — optional remote API connector (soft dependency, never hard).

Enabled by `HELIOS_SOCIALCRAWL_API_KEY` (endpoint override via
`HELIOS_SOCIALCRAWL_URL`).  Unconfigured -> honest `unconfigured` health
and AdapterUnavailable at call time.  Platform-specific responses are
normalized into SourceDocument while the raw provider response hash is
preserved in warnings for provenance.
"""

from __future__ import annotations

import hashlib
import json
import os

from helios.web.adapters.base import BaseSourceAdapter
from helios.web.types import (
    AdapterUnavailable,
    HealthStatus,
    SourceCapabilities,
    SourceDocument,
    WebAccessRequest,
)

KEY_ENV = "HELIOS_SOCIALCRAWL_API_KEY"
URL_ENV = "HELIOS_SOCIALCRAWL_URL"
DEFAULT_URL = "https://api.socialcrawl.dev/v1"


class SocialCrawlAdapter(BaseSourceAdapter):
    name = "socialcrawl"
    version = "0.1.0"
    trust_level = "optional-remote"
    capabilities = SourceCapabilities(search=True)

    @property
    def api_key(self) -> str | None:
        return os.environ.get(KEY_ENV) or None

    @property
    def endpoint(self) -> str:
        return os.environ.get(URL_ENV, DEFAULT_URL)

    def health(self) -> HealthStatus:
        if not self.api_key:
            return HealthStatus(
                adapter=self.name,
                status="unconfigured",
                detail=f"set {KEY_ENV} to enable the SocialCrawl connector",
            )
        return HealthStatus(adapter=self.name, status="healthy", detail=self.endpoint)

    def search(self, request: WebAccessRequest) -> list[SourceDocument]:
        if not self.api_key:
            raise AdapterUnavailable(f"socialcrawl is not configured ({KEY_ENV} unset)")
        data = self._get_json(
            f"{self.endpoint.rstrip('/')}/search",
            params={"q": request.query, "limit": request.max_results},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        raw_hash = hashlib.sha256(
            json.dumps(data, sort_keys=True, default=str).encode()
        ).hexdigest()

        docs = []
        for item in data.get("results", [])[: request.max_results]:
            docs.append(
                SourceDocument(
                    source=item.get("platform") or self.name,
                    operation="search",
                    url=item.get("url"),
                    title=item.get("title"),
                    author=item.get("author"),
                    published_at=item.get("published_at"),
                    content=str(item.get("text") or "")[:8000],
                    content_type=item.get("type") or "post",
                    source_adapter=self.name,
                    adapter_version=self.version,
                    citations=[{"url": item.get("url")}] if item.get("url") else [],
                    warnings=[f"provider_response_sha256={raw_hash[:16]}"],
                )
            )
        return docs
