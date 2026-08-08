from helios.config import Settings
from helios.providers.anthropic_provider import AnthropicProvider
from helios.providers.base import BaseProvider, ProviderResult
from helios.providers.gemini_provider import GeminiProvider
from helios.providers.mock import MockProvider
from helios.providers.openai_compatible import OpenAICompatibleProvider


def get_provider(provider_name: str, settings: Settings) -> BaseProvider:
    """Instantiate a provider adapter by name (router v2 entry point)."""
    name = provider_name.lower()
    if name == "mock":
        return MockProvider()
    if name == "groq":
        return OpenAICompatibleProvider(
            base_url=settings.groq_base_url,
            api_key=settings.groq_api_key,
            provider_label="groq",
        )
    if name == "openrouter":
        return OpenAICompatibleProvider(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
            provider_label="openrouter",
        )
    if name == "gemini":
        return GeminiProvider()
    if name in {"openai", "openai-compatible"}:
        return OpenAICompatibleProvider(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            provider_label="openai",
        )
    if name == "anthropic":
        return AnthropicProvider()
    raise ValueError(f"Unsupported provider: {provider_name}")


def choose_provider_model(
    normalized: dict,
    settings: Settings,
) -> tuple[BaseProvider, str, str]:
    """
    Phase 1 routing is deliberately simple:

    - explicit request provider/model wins
    - otherwise fall back to environment defaults

    Returns (provider_instance, model_id, provider_label).

    Later phases replace this with the full Helios Router.
    """

    provider_name = (
        normalized.get("requested_provider") or settings.default_provider
    ).lower()
    requested_model = normalized.get("requested_model")

    if provider_name == "mock":
        return (
            MockProvider(),
            requested_model or settings.default_model,
            "mock",
        )

    # --- Free, OpenAI-compatible providers -----------------------------------
    if provider_name == "groq":
        return (
            OpenAICompatibleProvider(
                base_url=settings.groq_base_url,
                api_key=settings.groq_api_key,
                provider_label="groq",
            ),
            requested_model or settings.default_groq_model,
            "groq",
        )

    if provider_name == "openrouter":
        return (
            OpenAICompatibleProvider(
                base_url=settings.openrouter_base_url,
                api_key=settings.openrouter_api_key,
                provider_label="openrouter",
            ),
            requested_model or settings.default_openrouter_model,
            "openrouter",
        )

    # --- Free, native protocol ----------------------------------------------
    if provider_name == "gemini":
        return (
            GeminiProvider(),
            requested_model or settings.default_gemini_model,
            "gemini",
        )

    # --- Paid providers ------------------------------------------------------
    if provider_name in {"openai", "openai-compatible"}:
        return (
            OpenAICompatibleProvider(
                base_url=settings.openai_base_url,
                api_key=settings.openai_api_key,
                provider_label="openai",
            ),
            requested_model or settings.default_openai_model,
            "openai",
        )

    if provider_name == "anthropic":
        return (
            AnthropicProvider(),
            requested_model or settings.default_anthropic_model,
            "anthropic",
        )

    raise ValueError(f"Unsupported provider: {provider_name}")


__all__ = [
    "BaseProvider",
    "ProviderResult",
    "MockProvider",
    "OpenAICompatibleProvider",
    "GeminiProvider",
    "AnthropicProvider",
    "choose_provider_model",
    "get_provider",
]
