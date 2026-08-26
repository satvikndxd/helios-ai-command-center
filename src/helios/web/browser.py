"""
Browser worker (Phase W3) — read-only authenticated browsing.

MVP shape: an HTTP-based worker that models the full browser-worker
contract — fresh context by default, per-session domain allowlists,
event emission, cookie isolation — without shipping a headless browser in
the API image.  The enterprise track swaps `_fetch` for a real isolated
browser container behind the same interface.

Hard rules (tested):

* Cookies are decrypted ONLY inside the worker and attached to the
  outbound request; they never appear in events, results, traces, or the
  returned document.
* Every navigation is checked against the session's domain allowlist.
* First release is read-only: no click/type/submit actions exist.
* Every action emits an event (`navigate`, `read`, `blocked`) for the
  audit trail.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from helios.models import BrowserSession
from helios.web.sanitize import sanitize_document
from helios.web.types import SourceDocument
from helios.web.vault import decrypt_profile


class BrowserDenied(Exception):
    pass


class BrowserWorker:
    def __init__(self, client: Any = None) -> None:
        self._client = client
        self.events: list[dict] = []

    @property
    def client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=20.0, follow_redirects=True)
        return self._client

    def _emit(self, event: str, **fields: Any) -> None:
        # Events are audit data: never include cookie material.
        self.events.append({"event": event, **fields})

    def _check_domain(self, url: str, allowlist: list[str]) -> None:
        host = (urlparse(url).hostname or "").lower()
        allowed = any(
            host == d or host.endswith("." + d) for d in (allowlist or [])
        )
        if not allowed:
            self._emit("blocked", url=url, reason="domain_not_allowlisted")
            raise BrowserDenied(
                f"domain '{host}' is not on this session's allowlist"
            )

    def read_page(
        self,
        url: str,
        session: BrowserSession | None = None,
    ) -> SourceDocument:
        """
        Read one page.  With a session, cookies are decrypted in-worker and
        attached to the request only; the fresh-context default is no
        session at all.
        """
        cookies = None
        session_scope = "fresh_context"
        if session is not None:
            self._check_domain(url, session.domain_allowlist)
            profile = decrypt_profile(session.encrypted_profile)
            cookies = profile.get("cookies") or {}
            session_scope = f"session:{session.id}"

        self._emit("navigate", url=url, context=session_scope)
        response = self.client.get(url, cookies=cookies)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()

        from helios.web.adapters.http_reader import html_to_text

        text, title = html_to_text(response.text)
        self._emit("read", url=url, bytes=len(response.text))

        doc = SourceDocument(
            source="browser",
            operation="read",
            url=url,
            title=title,
            content=text,
            content_type="page",
            source_adapter="browser-worker",
            adapter_version="0.1.0",
            citations=[{"url": url}],
            warnings=[f"browser_context={session_scope}"],
        )
        # Sanitization also scrubs any cookie/secret-shaped strings that a
        # page echoed back into its own content.
        return sanitize_document(doc)
