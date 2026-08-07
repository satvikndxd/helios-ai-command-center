from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from helios.chunking import chunk_text
from helios.config import settings
from helios.db import get_db
from helios.embeddings import get_embedding_provider
from helios.models import ApiKey, Chunk, Document
from helios.schemas import DocumentIn, DocumentOut
from helios.security import get_api_key


router = APIRouter(tags=["knowledge"])


@router.post("/v1/knowledge/documents", response_model=DocumentOut, status_code=201)
async def ingest_document(
    payload: DocumentIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """
    Ingest a document into the tenant's knowledge base.

    Splits content into overlapping chunks, embeds each chunk, and persists
    Document + Chunks in a single transaction — either the whole document is
    searchable or none of it is (no half-embedded documents).
    """

    pieces = chunk_text(
        payload.content,
        size=settings.chunk_size,
        overlap=settings.chunk_overlap,
    )
    if not pieces:
        raise HTTPException(status_code=422, detail="Document content is empty")

    provider = get_embedding_provider(settings)

    # Embed BEFORE opening writes so a provider failure leaves no partial state.
    embeddings = await provider.embed_batch(pieces, settings)

    document = Document(
        tenant_id=api_key.tenant_id,
        title=payload.title,
    )
    db.add(document)
    db.flush()  # assign document.id

    for position, (content, embedding) in enumerate(zip(pieces, embeddings)):
        db.add(
            Chunk(
                document_id=document.id,
                tenant_id=api_key.tenant_id,
                position=position,
                content=content,
                embedding=embedding,
            )
        )

    db.commit()

    return DocumentOut(
        id=document.id,
        tenant_id=document.tenant_id,
        title=document.title,
        chunk_count=len(pieces),
        created_at=document.created_at,
    )


@router.get("/v1/knowledge/documents", response_model=list[DocumentOut])
def list_documents(
    limit: int = Query(default=20, ge=1, le=100),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """List the tenant's knowledge-base documents with chunk counts."""

    rows = (
        db.query(Document, func.count(Chunk.id))
        .outerjoin(Chunk, Chunk.document_id == Document.id)
        .filter(Document.tenant_id == api_key.tenant_id)
        .group_by(Document.id)
        .order_by(Document.created_at.desc())
        .limit(limit)
        .all()
    )

    return [
        DocumentOut(
            id=doc.id,
            tenant_id=doc.tenant_id,
            title=doc.title,
            chunk_count=count,
            created_at=doc.created_at,
        )
        for doc, count in rows
    ]
