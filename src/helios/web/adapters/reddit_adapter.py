"""
Reddit adapter — public JSON API, read-only, no credentials.

Reddit's public `.json` endpoints are rate-limited for anonymous clients;
a 429 surfaces as AdapterRateLimited so the broker records the failure
honestly and falls back instead of fabricating results.
"""

from __future__ import annotations

from helios.web.adapters.base import BaseSourceAdapter
from helios.web.types import SourceCapabilities, SourceDocument, WebAccessRequest


class RedditAdapter(BaseSourceAdapter):
    name = "reddit"
    version = "0.1.0"
    trust_level = "builtin"
    capabilities = SourceCapabilities(search=True, read=True)

    BASE = "https://www.reddit.com"

    def _to_doc(self, post: dict, operation: str) -> SourceDocument:
        data = post.get("data", post)
        permalink = data.get("permalink")
        return SourceDocument(
            source=self.name,
            operation=operation,
            url=f"{self.BASE}{permalink}" if permalink else data.get("url"),
            title=data.get("title"),
            author=data.get("author"),
            published_at=str(data.get("created_utc") or ""),
            content=(data.get("selftext") or data.get("body") or "")[:4000],
            content_type="post",
            source_adapter=self.name,
            adapter_version=self.version,
            citations=[{"url": f"{self.BASE}{permalink}"}] if permalink else [],
        )

    def search(self, request: WebAccessRequest) -> list[SourceDocument]:
        data = self._get_json(
            f"{self.BASE}/search.json",
            params={"q": request.query, "limit": request.max_results, "sort": "new"},
        )
        children = (data.get("data") or {}).get("children", [])
        return [self._to_doc(c, "search") for c in children[: request.max_results]]

    def read(self, request: WebAccessRequest) -> SourceDocument:
        url = (request.url or "").split("?")[0].rstrip("/") + ".json"
        data = self._get_json(url)
        listing = data[0] if isinstance(data, list) and data else data
        children = (listing.get("data") or {}).get("children", [])
        if not children:
            return SourceDocument(
                source=self.name, operation="read", url=request.url,
                content="", content_type="post",
                source_adapter=self.name, adapter_version=self.version,
                warnings=["empty_thread"],
            )
        return self._to_doc(children[0], "read")
