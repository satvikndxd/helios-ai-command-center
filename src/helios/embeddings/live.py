import httpx

from helios.config import Settings
from helios.embeddings.base import BaseEmbeddingProvider


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    """
    Google Gemini embeddings (free tier).

    Get a key at https://aistudio.google.com/apikey, set HELIOS_GEMINI_API_KEY,
    and set HELIOS_EMBEDDING_DIM=768 (text-embedding-004 outputs 768 dims).
    """

    name = "gemini"

    async def embed(self, text: str, settings: Settings) -> list[float]:
        if not settings.gemini_api_key:
            raise RuntimeError(
                "HELIOS_GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com/apikey, or use "
                "HELIOS_EMBEDDING_PROVIDER=mock for local development."
            )

        model = settings.default_gemini_embedding_model
        base = settings.gemini_base_url.rstrip("/")
        url = f"{base}/models/{model}:embedContent"

        payload = {"content": {"parts": [{"text": text}]}}
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": settings.gemini_api_key,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code != 200:
                raise RuntimeError(
                    f"Gemini embedding request failed with status "
                    f"{response.status_code}: {response.text}"
                )
            data = response.json()

        values = data.get("embedding", {}).get("values", [])
        if len(values) != settings.embedding_dim:
            raise RuntimeError(
                f"Embedding dimension mismatch: model returned {len(values)}, "
                f"HELIOS_EMBEDDING_DIM is {settings.embedding_dim}. "
                f"Set HELIOS_EMBEDDING_DIM={len(values)} (requires re-ingesting)."
            )
        return values


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    """OpenAI embeddings adapter (paid, optional)."""

    name = "openai"

    async def embed(self, text: str, settings: Settings) -> list[float]:
        if not settings.openai_api_key:
            raise RuntimeError(
                "HELIOS_OPENAI_API_KEY is not set. Use "
                "HELIOS_EMBEDDING_PROVIDER=mock or gemini instead."
            )

        url = f"{settings.openai_base_url.rstrip('/')}/embeddings"
        payload = {
            "model": settings.default_openai_embedding_model,
            "input": text,
        }
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code != 200:
                raise RuntimeError(
                    f"OpenAI embedding request failed with status "
                    f"{response.status_code}: {response.text}"
                )
            data = response.json()

        values = data["data"][0]["embedding"]
        if len(values) != settings.embedding_dim:
            raise RuntimeError(
                f"Embedding dimension mismatch: model returned {len(values)}, "
                f"HELIOS_EMBEDDING_DIM is {settings.embedding_dim}."
            )
        return values
