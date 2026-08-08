from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from helios.chunking import chunk_text
from helios.config import settings
from helios.db import get_db
from helios.embeddings import get_embedding_provider
from helios.graph import upsert_entities_for_document
from helios.models import ApiKey, Chunk, Document, Entity, Relationship
from helios.schemas import DocumentIn, DocumentOut, EntityOut
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

    # Knowledge graph MVP: extract entities with provenance (same transaction —
    # a document is either fully ingested, graph included, or not at all).
    upsert_entities_for_document(
        db,
        tenant_id=api_key.tenant_id,
        document_id=document.id,
        text=f"{payload.title}. {payload.content}",
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


@router.get("/v1/knowledge/entities", response_model=list[EntityOut])
def list_entities(
    limit: int = Query(default=50, ge=1, le=500),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Knowledge-graph entities extracted from this tenant's documents."""
    return (
        db.query(Entity)
        .filter(Entity.tenant_id == api_key.tenant_id)
        .order_by(Entity.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/v1/knowledge/entities/{entity_id}/documents")
def entity_documents(
    entity_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Graph traversal (MVP): which documents mention this entity?"""
    entity = (
        db.query(Entity)
        .filter(Entity.id == entity_id, Entity.tenant_id == api_key.tenant_id)
        .first()
    )
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    rows = (
        db.query(Relationship, Document)
        .join(Document, Document.id == Relationship.document_id)
        .filter(
            Relationship.tenant_id == api_key.tenant_id,
            Relationship.source_entity_id == entity_id,
        )
        .all()
    )
    return {
        "entity": {"id": entity.id, "name": entity.name, "type": entity.type},
        "documents": [
            {
                "document_id": doc.id,
                "title": doc.title,
                "relationship": rel.relationship_type,
                "confidence": rel.confidence,
            }
            for rel, doc in rows
        ],
    }
