import httpx

from helios.config import Settings
from helios.providers.base import BaseProvider, ProviderResult


class AnthropicProvider(BaseProvider):
    """
    Anthropic Messages API adapter (optional, paid).
    """

    async def complete(
        self,
        request: dict,
        settings: Settings,
    ) -> ProviderResult:
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "HELIOS_ANTHROPIC_API_KEY is not set. "
                "Use a free provider (groq/openrouter/gemini) or "
                "HELIOS_DEFAULT_PROVIDER=mock for local development."
            )

        model = request.get("model") or settings.default_anthropic_model
        input_text = request.get("input_text", "")
        parameters = request.get("parameters", {})

        payload = {
            "model": model,
            "max_tokens": parameters.get("max_tokens", settings.default_max_tokens),
            "messages": [
                {
                    "role": "user",
                    "content": input_text,
                }
            ],
        }

        headers = {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": settings.anthropic_version,
            "content-type": "application/json",
        }

        url = f"{settings.anthropic_base_url.rstrip('/')}/messages"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                raise RuntimeError(
                    f"Anthropic request failed with status "
                    f"{response.status_code}: {response.text}"
                )

            data = response.json()

        output_parts = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                output_parts.append(block.get("text", ""))

        output_text = "".join(output_parts)

        usage_raw = data.get("usage", {})

        usage = {
            "prompt_tokens": usage_raw.get("input_tokens", 0),
            "completion_tokens": usage_raw.get("output_tokens", 0),
            "input_tokens": usage_raw.get("input_tokens", 0),
            "output_tokens": usage_raw.get("output_tokens", 0),
        }

        return ProviderResult(
            output_text=output_text,
            provider="anthropic",
            model=model,
            usage=usage,
            raw=data,
            citations=[],
        )
