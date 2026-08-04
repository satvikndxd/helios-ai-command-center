import httpx

from helios.config import Settings
from helios.providers.base import BaseProvider, ProviderResult


class OpenAICompatibleProvider(BaseProvider):
    """
    Adapter for any service exposing the OpenAI /chat/completions API.

    This single adapter powers several providers by varying base_url + key:
      - OpenAI          (paid)
      - Groq            (free tier)
      - OpenRouter      (free models)
      - Together, etc.  (any OpenAI-compatible endpoint)

    The router constructs it with the resolved endpoint and credential so the
    adapter itself stays provider-agnostic.
    """

    def __init__(self, base_url: str, api_key: str | None, provider_label: str):
        self.base_url = base_url
        self.api_key = api_key
        self.provider_label = provider_label

    async def complete(
        self,
        request: dict,
        settings: Settings,
    ) -> ProviderResult:
        if not self.api_key:
            raise RuntimeError(
                f"No API key configured for provider '{self.provider_label}'. "
                f"Set the matching HELIOS_*_API_KEY, or use "
                f"HELIOS_DEFAULT_PROVIDER=mock for local development."
            )

        model = request.get("model")
        input_text = request.get("input_text", "")
        parameters = request.get("parameters", {})

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": input_text,
                }
            ],
            "max_tokens": parameters.get("max_tokens", settings.default_max_tokens),
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # OpenRouter recommends (but does not require) these identification headers.
        if self.provider_label == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/helios"
            headers["X-Title"] = "Helios Gateway"

        url = f"{self.base_url.rstrip('/')}/chat/completions"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                raise RuntimeError(
                    f"{self.provider_label} request failed with status "
                    f"{response.status_code}: {response.text}"
                )

            data = response.json()

        output_text = (data["choices"][0]["message"].get("content") or "")
        usage_raw = data.get("usage", {}) or {}

        usage = {
            "prompt_tokens": usage_raw.get("prompt_tokens", 0),
            "completion_tokens": usage_raw.get("completion_tokens", 0),
            "total_tokens": usage_raw.get("total_tokens", 0),
        }

        return ProviderResult(
            output_text=output_text,
            provider=self.provider_label,
            model=model,
            usage=usage,
            raw=data,
            citations=[],
        )
