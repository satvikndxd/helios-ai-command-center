"""
The Web Access Broker.

Sits between the Helios control plane and the untrusted execution plane:
normalizes requests, runs the policy preflight, chooses adapters, applies
budgets, dispatches, sanitizes every result, and produces a per-source
status record (**failure honesty** — a rate-limited source is reported as
unavailable and the fallback is visible; we never claim to have searched a
source that failed).

The broker does not bypass policy because a source is "free" or
"open source".
"""

from __future__ import annotations

from typing import Iterable

from helios.web.policy import WebAccessPolicy
from helios.web.sanitize import sanitize_documents
from helios.web.types import (
    AdapterRateLimited,
    AdapterUnavailable,
    HealthStatus,
    PolicyDecision,
    SourceDocument,
    SourceStatus,
    WebAccessRequest,
)


class WebAccessBroker:
    def __init__(
        self,
        adapters: Iterable,  # WebSourceAdapter instances
        policy: WebAccessPolicy | None = None,
    ) -> None:
        self.adapters = {a.name: a for a in adapters}
        self.policy = policy or WebAccessPolicy()

    # -- registry ---------------------------------------------------------

    def sources(self) -> list[dict]:
        """Registry view: adapter, version, trust, capabilities, health."""
        out = []
        for adapter in self.adapters.values():
            try:
                health = adapter.health()
            except Exception as exc:  # noqa: BLE001 - health must not crash the API
                health = HealthStatus(
                    adapter=adapter.name, status="unavailable", detail=str(exc)
                )
            out.append(
                {
                    "name": adapter.name,
                    "version": adapter.version,
                    "trust_level": adapter.trust_level,
                    "capabilities": adapter.capabilities.model_dump(),
                    "health": health.model_dump(mode="json"),
                }
            )
        return out

    # -- candidate selection ----------------------------------------------

    def _candidates(self, request: WebAccessRequest) -> list:
        capability = request.operation
        capable = [
            a
            for a in self.adapters.values()
            if getattr(a.capabilities, capability, False)
        ]
        if not request.sources:
            return capable
        by_name = {a.name: a for a in capable}
        ordered = [by_name[s] for s in request.sources if s in by_name]
        return ordered

    # -- dispatch ---------------------------------------------------------

    def dispatch(
        self, request: WebAccessRequest
    ) -> tuple[PolicyDecision, list[SourceDocument], list[SourceStatus]]:
        """
        Returns (policy_decision, sanitized_documents, per_source_status).

        Policy runs immediately before execution.  For `search`, every
        requested (or capable) source is tried and reported individually.
        For `read`/`transcript`, candidates form an ordered fallback chain
        and the chain stops at the first success — but every failed attempt
        stays in the status list.
        """
        decision = self.policy.evaluate(request)
        if not decision.allowed:
            return decision, [], []

        candidates = self._candidates(request)
        statuses: list[SourceStatus] = []
        documents: list[SourceDocument] = []

        if not candidates:
            statuses.append(
                SourceStatus(
                    source="*",
                    status="unavailable",
                    detail=f"no adapter supports operation '{request.operation}'"
                    + (f" for sources {request.sources}" if request.sources else ""),
                )
            )
            return decision, [], statuses

        fan_out = request.operation == "search"

        for adapter in candidates:
            try:
                if request.operation == "search":
                    results = adapter.search(request)
                elif request.operation == "read":
                    results = [adapter.read(request)]
                else:
                    results = [adapter.transcript(request)]
            except AdapterRateLimited as exc:
                statuses.append(
                    SourceStatus(source=adapter.name, status="rate_limited", detail=str(exc))
                )
                continue
            except AdapterUnavailable as exc:
                statuses.append(
                    SourceStatus(source=adapter.name, status="unavailable", detail=str(exc))
                )
                continue
            except Exception as exc:  # noqa: BLE001 - report, never fabricate
                statuses.append(
                    SourceStatus(source=adapter.name, status="error", detail=str(exc)[:300])
                )
                continue

            clean = sanitize_documents(results)
            documents.extend(clean)
            statuses.append(
                SourceStatus(source=adapter.name, status="ok", results=len(clean))
            )
            if not fan_out:
                # Fallback chain satisfied — remaining candidates are skipped
                # (and say so, instead of silently disappearing).
                for remaining in candidates[candidates.index(adapter) + 1 :]:
                    statuses.append(
                        SourceStatus(
                            source=remaining.name,
                            status="skipped",
                            detail="earlier adapter in the fallback chain succeeded",
                        )
                    )
                break

        documents = documents[: request.max_results]
        return decision, documents, statuses


def default_broker() -> WebAccessBroker:
    """The Phase 1 registry: public read path + optional connectors."""
    from helios.web.adapters.agent_reach import AgentReachAdapter
    from helios.web.adapters.github_adapter import GitHubAdapter
    from helios.web.adapters.http_reader import HttpReaderAdapter
    from helios.web.adapters.reddit_adapter import RedditAdapter
    from helios.web.adapters.socialcrawl import SocialCrawlAdapter
    from helios.web.adapters.youtube_adapter import YouTubeTranscriptAdapter

    return WebAccessBroker(
        adapters=[
            HttpReaderAdapter(),
            GitHubAdapter(),
            RedditAdapter(),
            YouTubeTranscriptAdapter(),
            AgentReachAdapter(),
            SocialCrawlAdapter(),
        ]
    )
