from helios.config import Settings
from helios.embeddings.base import BaseEmbeddingProvider
from helios.embeddings.live import GeminiEmbeddingProvider, OpenAIEmbeddingProvider
from helios.embeddings.mock import MockEmbeddingProvider


def get_embedding_provider(settings: Settings) -> BaseEmbeddingProvider:
    name = settings.embedding_provider.lower()

    if name == "mock":
        return MockEmbeddingProvider()
    if name == "gemini":
        return GeminiEmbeddingProvider()
    if name == "openai":
        return OpenAIEmbeddingProvider()

    raise ValueError(f"Unsupported embedding provider: {name}")


__all__ = [
    "BaseEmbeddingProvider",
    "MockEmbeddingProvider",
    "GeminiEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "get_embedding_provider",
]
