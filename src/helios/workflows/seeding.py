"""
Demo environment seeding — synthetic data only.

Reuses the EXISTING ingestion machinery: documents flow through the same
chunk -> embed -> Document/Chunk path as /v1/knowledge/documents (with the
workspace_id scope added), entities/relationships go through the existing
knowledge graph, and structured records become WorkspaceSource rows.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.chunking import chunk_text
from helios.config import settings
from helios.embeddings import get_embedding_provider
from helios.graph import link_entities, upsert_entities_for_document
from helios.models import Chunk, Document, WorkspaceSource
from helios.workflows.registry import all_packs
from helios.workflows.types import WorkspacePack


async def seed_workspace(db: Session, tenant_id: str, pack: WorkspacePack) -> dict:
    """Idempotent seed of one workspace's synthetic sources/documents/graph."""
    workspace_id = pack.config.id
    created = {"sources": 0, "documents": 0, "relationships": 0}

    # Structured sources.
    for spec in pack.seed_sources:
        exists = (
            db.query(WorkspaceSource)
            .filter(
                WorkspaceSource.tenant_id == tenant_id,
                WorkspaceSource.workspace_id == workspace_id,
                WorkspaceSource.name == spec["name"],
            )
            .first()
        )
        if exists:
            continue
        db.add(
            WorkspaceSource(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                name=spec["name"],
                type=spec["type"],
                record=spec.get("record", {}),
                content=spec.get("content"),
                trust=spec.get("trust", "internal"),
                provenance={"origin": "synthetic-demo", "pack": workspace_id},
            )
        )
        created["sources"] += 1

    # Text documents -> existing RAG path (chunk, embed, store) + graph.
    provider = get_embedding_provider(settings)
    for doc_spec in pack.seed_documents:
        exists = (
            db.query(Document)
            .filter(
                Document.tenant_id == tenant_id,
                Document.workspace_id == workspace_id,
                Document.title == doc_spec["title"],
            )
            .first()
        )
        if exists:
            continue
        pieces = chunk_text(
            doc_spec["content"], size=settings.chunk_size, overlap=settings.chunk_overlap
        )
        embeddings = await provider.embed_batch(pieces, settings)
        document = Document(
            tenant_id=tenant_id, title=doc_spec["title"], workspace_id=workspace_id
        )
        db.add(document)
        db.flush()
        for position, (content, embedding) in enumerate(zip(pieces, embeddings)):
            db.add(
                Chunk(
                    document_id=document.id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    position=position,
                    content=content,
                    embedding=embedding,
                )
            )
        upsert_entities_for_document(
            db, tenant_id=tenant_id, document_id=document.id, text=doc_spec["content"]
        )
        created["documents"] += 1

    # Domain knowledge-graph relationships (entity -> entity, with provenance).
    for rel in pack.seed_relationships:
        link_entities(
            db,
            tenant_id=tenant_id,
            source=tuple(rel["source"]),
            relationship_type=rel["relationship_type"],
            target=tuple(rel["target"]),
        )
        created["relationships"] += 1

    db.commit()
    return created


async def seed_all_workspaces(db: Session, tenant_id: str) -> dict[str, dict]:
    return {
        workspace_id: await seed_workspace(db, tenant_id, pack)
        for workspace_id, pack in all_packs().items()
    }
