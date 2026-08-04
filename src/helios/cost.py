from typing import Any


"""
Phase 1 cost tracking is intentionally simple.

Important:
- Pricing values here are placeholders.
- Free-tier providers (mock, groq, openrouter :free models, gemini flash) are
  priced at 0.0 so cost metering reflects "no spend" during development.
- Later, pricing should come from model registry metadata.
"""

DEFAULT_RATES = {
    "input_per_token": 0.0000005,
    "output_per_token": 0.0000015,
}

# Free-tier / local models cost nothing to call.
FREE_RATES = {
    "input_per_token": 0.0,
    "output_per_token": 0.0,
}

MODEL_RATES = {
    "mock-model-1": FREE_RATES,
}

# Whole providers whose free tiers we treat as zero-cost by default.
FREE_PROVIDERS = {"mock", "groq", "openrouter", "gemini"}


def compute_cost(model_id: str, usage: dict[str, Any], provider: str | None = None) -> float:
    """
    Compute a USD cost estimate from token usage.

    Supports both OpenAI-style and Anthropic-style usage keys.
    """

    if provider and provider.lower() in FREE_PROVIDERS:
        return 0.0

    prompt_tokens = int(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or 0
    )
    completion_tokens = int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )

    rates = MODEL_RATES.get(model_id, DEFAULT_RATES)

    cost = (
        prompt_tokens * rates["input_per_token"]
        + completion_tokens * rates["output_per_token"]
    )

    return round(cost, 6)
