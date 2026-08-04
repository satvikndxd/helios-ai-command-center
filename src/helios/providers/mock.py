import asyncio

from helios.config import Settings
from helios.providers.base import BaseProvider, ProviderResult


class MockProvider(BaseProvider):
    """
    Zero-dependency provider for local development and tests.
    """

    async def complete(
        self,
        request: dict,
        settings: Settings,
    ) -> ProviderResult:
        await asyncio.sleep(0.05)

        input_text = request.get("input_text", "")
        model = request.get("model", settings.default_model)

        output_text = f"[mock:{model}] Echo: {input_text[:1000]}"

        prompt_tokens = max(1, len(input_text) // 4)
        completion_tokens = max(1, len(output_text) // 4)

        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

        raw = {
            "provider": "mock",
            "model": model,
            "output": output_text,
        }

        return ProviderResult(
            output_text=output_text,
            provider="mock",
            model=model,
            usage=usage,
            raw=raw,
            citations=[],
        )
