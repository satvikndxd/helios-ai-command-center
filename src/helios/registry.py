"""
Helios model registry + router v2 (FR-RT-001/002/003/005/007).

The registry holds model metadata (quality, cost, privacy tier); the router
turns a request into an ordered, explainable fallback chain of candidates.

Routing inputs: explicit request > risk level > cost budget > defaults.
Every decision carries human-readable reasons, recorded on the trace.
"""

from dataclasses import dataclass, field

from helios.config import Settings


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    quality: float  # 0..1 relative quality prior
    privacy: str  # "local" | "external"
    # USD per 1M tokens (input, output); free tiers are 0.
    cost_in_per_m: float
    cost_out_per_m: float


REGISTRY: dict[str, ModelInfo] = {
    "mock": ModelInfo("mock", quality=0.10, privacy="local", cost_in_per_m=0.0, cost_out_per_m=0.0),
    "groq": ModelInfo("groq", quality=0.80, privacy="external", cost_in_per_m=0.0, cost_out_per_m=0.0),
    "openrouter": ModelInfo("openrouter", quality=0.80, privacy="external", cost_in_per_m=0.0, cost_out_per_m=0.0),
    "gemini": ModelInfo("gemini", quality=0.85, privacy="external", cost_in_per_m=0.0, cost_out_per_m=0.0),
    "openai": ModelInfo("openai", quality=0.90, privacy="external", cost_in_per_m=0.15, cost_out_per_m=0.60),
    "anthropic": ModelInfo("anthropic", quality=0.95, privacy="external", cost_in_per_m=3.00, cost_out_per_m=15.00),
}


def default_model_for(provider: str, settings: Settings) -> str:
    return {
        "mock": settings.default_model,
        "groq": settings.default_groq_model,
        "openrouter": settings.default_openrouter_model,
        "gemini": settings.default_gemini_model,
        "openai": settings.default_openai_model,
        "anthropic": settings.default_anthropic_model,
    }[provider]


def estimate_cost_usd(provider: str, input_text: str, max_tokens: int) -> float:
    """Pre-call cost ceiling estimate (chars/4 ≈ tokens; worst-case output)."""
    info = REGISTRY.get(provider)
    if info is None:
        return 0.0
    in_tokens = max(1, len(input_text) // 4)
    return round(
        in_tokens * info.cost_in_per_m / 1_000_000
        + max_tokens * info.cost_out_per_m / 1_000_000,
        6,
    )


@dataclass
class RoutingDecision:
    chain: list[str]  # ordered provider candidates
    reasons: list[str] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)  # filled by gateway

    def to_dict(self) -> dict:
        return {"chain": self.chain, "reasons": self.reasons, "attempts": self.attempts}


def _fallbacks(settings: Settings) -> list[str]:
    raw = getattr(settings, "fallback_providers", "") or ""
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def select_route(
    *,
    requested_provider: str | None,
    risk_level: str,
    input_text: str,
    max_cost_usd: float | None,
    settings: Settings,
) -> RoutingDecision:
    reasons: list[str] = []

    # 1. Explicit request always wins (with fallbacks appended).
    if requested_provider:
        chain = [requested_provider.lower()]
        reasons.append(f"explicit provider '{requested_provider}' requested")
    else:
        primary = settings.default_provider.lower()
        # 2. Risk-based override: strongest configured provider for high risk.
        high_risk_provider = (getattr(settings, "high_risk_provider", "") or "").lower()
        if risk_level in {"high", "critical"} and high_risk_provider:
            primary = high_risk_provider
            reasons.append(
                f"risk_level={risk_level} -> high-risk provider '{primary}'"
            )
        else:
            reasons.append(f"default provider '{primary}'")
        chain = [primary]

    # 3. Append configured fallbacks (dedup, preserve order).
    for fb in _fallbacks(settings):
        if fb not in chain:
            chain.append(fb)
    if len(chain) > 1:
        reasons.append(f"fallback chain: {chain[1:]}")

    # 4. Cost guardrail: drop candidates whose estimated ceiling exceeds the
    # budget — but never drop the last remaining candidate silently.
    if max_cost_usd is not None:
        kept: list[str] = []
        for p in chain:
            est = estimate_cost_usd(p, input_text, settings.default_max_tokens)
            if est <= max_cost_usd:
                kept.append(p)
            else:
                reasons.append(
                    f"dropped '{p}': estimated cost ${est} > budget ${max_cost_usd}"
                )
        if kept:
            chain = kept
        else:
            reasons.append("all candidates over budget; keeping cheapest anyway")
            chain = sorted(
                chain,
                key=lambda p: estimate_cost_usd(p, input_text, settings.default_max_tokens),
            )[:1]

    return RoutingDecision(chain=chain, reasons=reasons)
