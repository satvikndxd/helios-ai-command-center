from abc import ABC, abstractmethod

from helios.config import Settings


class BaseEmbeddingProvider(ABC):
    """
    Strategy interface for embedding providers.

    Mirrors the LLM provider abstraction: mock by default (zero dependencies),
    free-tier live providers behind env vars.
    """

    name: str

    @abstractmethod
    async def embed(self, text: str, settings: Settings) -> list[float]:
        """Return an embedding vector of settings.embedding_dim floats."""
        raise NotImplementedError

    async def embed_batch(self, texts: list[str], settings: Settings) -> list[list[float]]:
        """Naive default; live providers can override with true batch calls."""
        return [await self.embed(t, settings) for t in texts]
