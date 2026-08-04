import httpx

from helios.config import Settings
from helios.providers.base import BaseProvider, ProviderResult


class GeminiProvider(BaseProvider):
    """
    Google Gemini adapter (generativelanguage.googleapis.com).

    Uses the free-tier Gemini API. Get a key at https://aistudio.google.com/apikey
    and set HELIOS_GEMINI_API_KEY.
    """

    async def complete(
        self,
        request: dict,
        settings: Settings,
    ) -> ProviderResult:
        if not settings.gemini_api_key:
            raise RuntimeError(
                "HELIOS_GEMINI_API_KEY is not set. "
                "Get a free key at https://aistudio.google.com/apikey, or use "
                "HELIOS_DEFAULT_PROVIDER=mock for local development."
            )

        model = request.get("model") or settings.default_gemini_model
        input_text = request.get("input_text", "")
        parameters = request.get("parameters", {})

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": input_text}],
                }
            ],
            "generationConfig": {
                "maxOutputTokens": parameters.get(
                    "max_tokens", settings.default_max_tokens
                ),
            },
        }

        base = settings.gemini_base_url.rstrip("/")
        url = f"{base}/models/{model}:generateContent"

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": settings.gemini_api_key,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                raise RuntimeError(
                    f"Gemini request failed with status "
                    f"{response.status_code}: {response.text}"
                )

            data = response.json()

        output_parts: list[str] = []
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "text" in part:
                    output_parts.append(part["text"])

        output_text = "".join(output_parts)

        usage_raw = data.get("usageMetadata", {}) or {}
        prompt_tokens = usage_raw.get("promptTokenCount", 0)
        completion_tokens = usage_raw.get("candidatesTokenCount", 0)

        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": usage_raw.get(
                "totalTokenCount", prompt_tokens + completion_tokens
            ),
        }

        return ProviderResult(
            output_text=output_text,
            provider="gemini",
            model=model,
            usage=usage,
            raw=data,
            citations=[],
        )
