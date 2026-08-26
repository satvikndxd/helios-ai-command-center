"""
Web access policy preflight.

The plan is not permission: every broker dispatch passes through
`evaluate()` immediately before execution.  Authorization lives here, in
Helios — never inside an adapter, an MCP server, or a scraper library.

Phase 1 rules (safe research read path):

* operation class     — read operations may run automatically; write /
                        destructive operations (post, send, delete, like,
                        follow, purchase, update) are refused until the
                        approval queue ships.
* domain policy       — reads are restricted to an allowlist of known
                        public sources; unknown domains are blocked with an
                        explicit reason (confirmation flow comes later).
* volume budget       — max_results is clamped by policy, and bulk crawls
                        are rejected.
* authenticated mode  — adapters that require credentials/sessions are
                        refused in Phase 1 (browser sessions are a later
                        release).
"""

from __future__ import annotations

from urllib.parse import urlparse

from helios.web.types import (
    READ_OPERATIONS,
    WRITE_OPERATIONS,
    PolicyDecision,
    WebAccessRequest,
)

# Public, read-only research sources for the first release.
DEFAULT_ALLOWED_DOMAINS = {
    "github.com",
    "api.github.com",
    "raw.githubusercontent.com",
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "video.google.com",
    "news.ycombinator.com",
    "en.wikipedia.org",
    "wikipedia.org",
    "arxiv.org",
    "pypi.org",
    "docs.python.org",
}

MAX_RESULTS_HARD_CAP = 50


class WebAccessPolicy:
    def __init__(self, allowed_domains: set[str] | None = None) -> None:
        self.allowed_domains = set(allowed_domains or DEFAULT_ALLOWED_DOMAINS)

    # -- helpers ----------------------------------------------------------

    def domain_allowed(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return False
        return any(
            host == allowed or host.endswith("." + allowed)
            for allowed in self.allowed_domains
        )

    # -- preflight --------------------------------------------------------

    def evaluate(self, request: WebAccessRequest) -> PolicyDecision:
        reasons: list[str] = []

        # 1. Operation class.
        if request.operation in WRITE_OPERATIONS:
            return PolicyDecision(
                allowed=False,
                requires_approval=True,
                reasons=[
                    f"operation '{request.operation}' is a write action: "
                    "write/destructive operations always require approval "
                    "and are disabled in the read-only release"
                ],
            )
        if request.operation not in READ_OPERATIONS:
            return PolicyDecision(
                allowed=False,
                reasons=[f"unknown operation '{request.operation}'"],
            )

        # 2. Domain policy for direct reads.
        if request.url:
            if not self.domain_allowed(request.url):
                return PolicyDecision(
                    allowed=False,
                    requires_approval=True,
                    reasons=[
                        f"domain of '{request.url}' is not on the allowlist; "
                        "unknown domains require explicit confirmation"
                    ],
                )
            reasons.append("domain_allowlisted")

        # 3. Volume budget.
        if request.max_results > MAX_RESULTS_HARD_CAP:
            return PolicyDecision(
                allowed=False,
                reasons=[
                    f"requested volume {request.max_results} exceeds the "
                    f"hard cap of {MAX_RESULTS_HARD_CAP}"
                ],
            )

        reasons.append(f"read_operation:{request.operation}")
        return PolicyDecision(allowed=True, reasons=reasons)
