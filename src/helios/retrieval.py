import math
from dataclasses import dataclass

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from helios.config import Settings
from helios.embeddings import get_embedding_provider
from helios.models import Chunk, Document


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_title: str
    content: str
    score: float  # cosine similarity, 1.0 = identical direction
    position: int


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def search(
    db: Session,
    tenant_id: str,
    query: str,
    settings: Settings,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    """
    Tenant-isolated nearest-neighbor search over knowledge chunks.

    SECURITY INVARIANT: every code path filters by tenant_id BEFORE any
    similarity computation. Cross-tenant retrieval is the #1 enterprise RAG
    risk; isolation is enforced at the database layer, not left to callers.

    Postgres: pgvector cosine distance (`<=>`) in SQL — scales with an index.
    Other dialects (SQLite tests): Python cosine over the tenant's chunks.
    """
    k = top_k or settings.retrieval_top_k

    provider = get_embedding_provider(settings)
    query_vec = await provider.embed(query, settings)

    dialect = db.bind.dialect.name

    if dialect == "postgresql":
        vec_literal = "[" + ",".join(f"{v:.8f}" for v in query_vec) + "]"
        rows = db.execute(
            sql_text(
                """
                SELECT c.id, c.document_id, d.title, c.content, c.position,
                       1 - (c.embedding <=> CAST(:qvec AS vector)) AS score
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.tenant_id = :tenant_id
                  AND c.embedding IS NOT NULL
                ORDER BY c.embedding <=> CAST(:qvec AS vector)
                LIMIT :k
                """
            ),
            {"qvec": vec_literal, "tenant_id": tenant_id, "k": k},
        ).fetchall()

        return [
            RetrievedChunk(
                chunk_id=r[0],
                document_id=r[1],
                document_title=r[2],
                content=r[3],
                position=r[4],
                score=float(r[5]),
            )
            for r in rows
        ]

    # Portable fallback (SQLite / others): brute-force cosine in Python.
    # Fine for tests and small local KBs; Postgres is the production path.
    rows = (
        db.query(Chunk, Document.title)
        .join(Document, Document.id == Chunk.document_id)
        .filter(Chunk.tenant_id == tenant_id)
        .filter(Chunk.embedding.isnot(None))
        .all()
    )

    scored = [
        RetrievedChunk(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            document_title=title,
            content=chunk.content,
            position=chunk.position,
            score=_cosine_similarity(query_vec, chunk.embedding),
        )
        for chunk, title in rows
    ]
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:k]


def build_context_prompt(chunks: list[RetrievedChunk], user_input: str) -> str:
    """
    Assemble the grounded prompt: numbered context blocks + the question.

    Numbering the sources ([1], [2], ...) lets the model reference them and
    lines the output up with the citations array we return to the caller.
    """
    context_blocks = []
    for i, chunk in enumerate(chunks, start=1):
        context_blocks.append(f"[{i}] (from \"{chunk.document_title}\")\n{chunk.content}")

    context = "\n\n".join(context_blocks)

    return (
        "Use ONLY the following context to answer. If the context is "
        "insufficient, say so. Reference sources by their [number].\n\n"
        f"Context:\n{context}\n\n"
        f"Question:\n{user_input}"
    )


def chunks_to_citations(chunks: list[RetrievedChunk]) -> list[dict]:
    return [
        {
            "index": i,
            "chunk_id": c.chunk_id,
            "document_id": c.document_id,
            "title": c.document_title,
            "position": c.position,
            "score": round(c.score, 4),
        }
        for i, c in enumerate(chunks, start=1)
    ]
