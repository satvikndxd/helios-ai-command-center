"""
GitHub adapter — typed public read operations via api.github.com.

No model-generated shell commands, no `gh` invocation from the API process:
the adapter exposes exactly two typed operations (search issues/repos, read
repository metadata) against the public REST API.
"""

from __future__ import annotations

import re

from helios.web.adapters.base import BaseSourceAdapter
from helios.web.types import (
    AdapterUnavailable,
    SourceCapabilities,
    SourceDocument,
    WebAccessRequest,
)

_REPO_URL = re.compile(r"github\.com/([\w.\-]+)/([\w.\-]+)")


class GitHubAdapter(BaseSourceAdapter):
    name = "github"
    version = "0.1.0"
    trust_level = "builtin"
    capabilities = SourceCapabilities(search=True, read=True)

    API = "https://api.github.com"

    def search(self, request: WebAccessRequest) -> list[SourceDocument]:
        data = self._get_json(
            f"{self.API}/search/issues",
            params={"q": request.query, "per_page": request.max_results},
        )
        docs: list[SourceDocument] = []
        for item in data.get("items", [])[: request.max_results]:
            docs.append(
                SourceDocument(
                    source=self.name,
                    operation="search",
                    url=item.get("html_url"),
                    title=item.get("title"),
                    author=(item.get("user") or {}).get("login"),
                    published_at=item.get("created_at"),
                    content=(item.get("body") or "")[:4000],
                    content_type="issue",
                    source_adapter=self.name,
                    adapter_version=self.version,
                    citations=[{"url": item.get("html_url")}],
                )
            )
        return docs

    def read(self, request: WebAccessRequest) -> SourceDocument:
        match = _REPO_URL.search(request.url or "")
        if not match:
            raise AdapterUnavailable(
                "github adapter reads repositories; expected a github.com/{owner}/{repo} URL"
            )
        owner, repo = match.group(1), match.group(2).removesuffix(".git")
        data = self._get_json(f"{self.API}/repos/{owner}/{repo}")
        content = (
            f"{data.get('full_name')}\n{data.get('description') or ''}\n"
            f"stars={data.get('stargazers_count')} forks={data.get('forks_count')} "
            f"open_issues={data.get('open_issues_count')} "
            f"license={(data.get('license') or {}).get('spdx_id')} "
            f"updated={data.get('updated_at')}"
        )
        return SourceDocument(
            source=self.name,
            operation="read",
            url=data.get("html_url") or request.url,
            title=data.get("full_name"),
            published_at=data.get("updated_at"),
            content=content,
            content_type="repository",
            source_adapter=self.name,
            adapter_version=self.version,
            citations=[{"url": data.get("html_url") or request.url}],
        )
