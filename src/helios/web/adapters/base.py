"""
The common adapter contract.

An adapter does NOT decide whether a request is allowed.  It reports
capabilities and executes only a broker-approved request; authorization
stays in Helios policy instead of being duplicated across Agent-Reach,
SocialCrawl, browser code, and MCP servers.

Adapters accept an injectable `client` (anything with httpx's `get`
signature) so the test suite runs fully offline.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from helios.web.types import (
    AdapterUnavailable,
    HealthStatus,
    SourceCapabilities,
    SourceDocument,
    WebAccessRequest,
)


@runtime_checkable
class WebSourceAdapter(Protocol):
    name: str
    version: str
    trust_level: str
    capabilities: SourceCapabilities

    def health(self) -> HealthStatus: ...
    def search(self, request: WebAccessRequest) -> list[SourceDocument]: ...
    def read(self, request: WebAccessRequest) -> SourceDocument: ...
    def transcript(self, request: WebAccessRequest) -> SourceDocument: ...


class BaseSourceAdapter:
    """Shared plumbing: capability checks, default health, http client."""

    name = "base"
    version = "0.1.0"
    trust_level = "builtin"
    capabilities = SourceCapabilities()

    def __init__(self, client: Any = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(
                timeout=20.0,
                follow_redirects=True,
                headers={"User-Agent": "helios-web-access/0.1 (research; read-only)"},
            )
        return self._client

    def health(self) -> HealthStatus:
        return HealthStatus(adapter=self.name, status="healthy")

    def _unsupported(self, operation: str) -> AdapterUnavailable:
        return AdapterUnavailable(
            f"adapter '{self.name}' does not support operation '{operation}'"
        )

    def search(self, request: WebAccessRequest) -> list[SourceDocument]:
        raise self._unsupported("search")

    def read(self, request: WebAccessRequest) -> SourceDocument:
        raise self._unsupported("read")

    def transcript(self, request: WebAccessRequest) -> SourceDocument:
        raise self._unsupported("transcript")

    def _get_json(self, url: str, **kwargs: Any) -> Any:
        response = self.client.get(url, **kwargs)
        if getattr(response, "status_code", 200) == 429:
            from helios.web.types import AdapterRateLimited

            raise AdapterRateLimited(f"{self.name}: rate limited by upstream")
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return response.json()

    def _get_text(self, url: str, **kwargs: Any) -> str:
        response = self.client.get(url, **kwargs)
        if getattr(response, "status_code", 200) == 429:
            from helios.web.types import AdapterRateLimited

            raise AdapterRateLimited(f"{self.name}: rate limited by upstream")
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return response.text
