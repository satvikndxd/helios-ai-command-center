import hashlib
import math
import re

from helios.config import Settings
from helios.embeddings.base import BaseEmbeddingProvider


_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Without stopword removal, short documents that merely share "the"/"is" with
# a query can outrank genuinely relevant documents (short-doc norm dilution).
_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have i if in into is it its
    may must not of on or our so that the their this to was we what when which
    who will with you your""".split()
)


class MockEmbeddingProvider(BaseEmbeddingProvider):
    """
    Deterministic, zero-dependency embeddings for dev and tests.

    Implementation note (deliberate deviation from "hash the whole text"):
    a single scalar repeated across all dimensions makes every vector a
    multiple of the ones-vector, so cosine similarity is 1.0 between ALL
    texts and ranking is meaningless. Instead we build a hashed bag-of-words:
    each token is hashed (hashlib, stable across processes — unlike Python's
    salted `hash()`) into a dimension bucket, with punctuation stripped and
    stopwords dropped. Texts sharing meaningful words therefore share vector
    mass, so nearest-neighbor ranking behaves sensibly in tests.
    """

    name = "mock"

    async def embed(self, text: str, settings: Settings) -> list[float]:
        dim = settings.embedding_dim
        vec = [0.0] * dim

        tokens = _TOKEN_RE.findall(text.lower())
        meaningful = [t for t in tokens if t not in _STOPWORDS]
        if not meaningful:
            meaningful = tokens  # all-stopword text still gets a vector
        if not meaningful:
            vec[0] = 1.0
            return vec

        for token in meaningful:
            digest = hashlib.md5(token.encode("utf-8")).hexdigest()
            bucket = int(digest, 16) % dim
            vec[bucket] += 1.0

        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec]
