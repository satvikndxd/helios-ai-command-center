import hashlib
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from helios.config import settings


# Use JSONB on Postgres, plain JSON elsewhere (e.g. SQLite in tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")

# Real pgvector VECTOR(dim) on Postgres; JSON list-of-floats elsewhere so the
# same model works on SQLite (tests) with a Python cosine fallback in retrieval.
EmbeddingType = JSON().with_variant(Vector(settings.embedding_dim), "postgresql")


class Base(DeclarativeBase):
    pass


def generate_uuid() -> str:
    return str(uuid.uuid4())


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    applications: Mapped[list["Application"]] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
    )


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    tenant: Mapped[Tenant] = relationship(back_populates="applications")
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    key_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    application_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("applications.id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="default",
    )
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    application: Mapped[Application] = relationship(back_populates="api_keys")


class DecisionTrace(Base):
    """
    Phase 1 DecisionTrace.

    This is the core Helios audit object. Every AI decision that passes through
    the gateway should produce exactly one trace row.
    """

    __tablename__ = "decision_traces"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    application_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("applications.id"),
        nullable=False,
        index=True,
    )

    session_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    user_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    task_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="completion",
    )
    risk_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="low",
    )

    # Request-side data
    input_payload: Mapped[dict] = mapped_column(
        JSONType,
        nullable=False,
        default=dict,
    )
    request_payload: Mapped[dict] = mapped_column(
        JSONType,
        nullable=False,
        default=dict,
    )

    # Routing/model data
    model_provider: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="unknown",
    )
    model_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="unknown",
    )

    # Response-side data
    output_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    response_payload: Mapped[dict | None] = mapped_column(
        JSONType,
        nullable=True,
    )

    citations: Mapped[list] = mapped_column(
        JSONType,
        nullable=False,
        default=list,
    )
    tool_calls: Mapped[list] = mapped_column(
        JSONType,
        nullable=False,
        default=list,
    )

    # Metering
    cost_usd: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
    )
    latency_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Future Phase hooks
    policy_result: Mapped[dict | None] = mapped_column(
        JSONType,
        nullable=True,
    )
    evaluation_scores: Mapped[dict | None] = mapped_column(
        JSONType,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="success",
    )
    error: Mapped[dict | None] = mapped_column(
        JSONType,
        nullable=True,
    )
    feedback: Mapped[dict | None] = mapped_column(
        JSONType,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class EvaluationJob(Base):
    """
    Postgres-backed job queue for the async (cold-path) evaluation worker.

    The gateway (hot path) enqueues one job per trace, then returns immediately.
    Worker(s) claim jobs with `FOR UPDATE SKIP LOCKED`, so many workers can run
    concurrently without processing the same job twice — a production-grade queue
    on the database we already have, no Kafka/Redis/Celery required.

    Status lifecycle: pending -> processing -> completed (or failed).
    """

    __tablename__ = "evaluation_jobs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    trace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("decision_traces.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Document(Base):
    """A knowledge-base document owned by a tenant (Phase 2)."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class Chunk(Base):
    """
    A retrievable slice of a Document with its embedding.

    tenant_id is denormalized from Document ON PURPOSE: it lets the vector
    search apply `WHERE tenant_id = :tenant_id` directly on this table before
    the distance calculation — database-level tenant isolation with no join.
    """

    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    embedding: Mapped[list | None] = mapped_column(
        EmbeddingType,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    document: Mapped[Document] = relationship(back_populates="chunks")


class ReviewItem(Base):
    """Human review queue (FR-EV-005): low-quality/high-risk decisions."""

    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=False, index=True
    )
    trace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("decision_traces.id"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", index=True
    )  # open | resolved
    resolution: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Dataset(Base):
    """Versioned evaluation dataset mined from production traces (Forge)."""

    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(String(50), nullable=False, default="evaluation")
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="production")
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    items: Mapped[list["DatasetItem"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class DatasetItem(Base):
    __tablename__ = "dataset_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("datasets.id"), nullable=False, index=True
    )
    trace_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("decision_traces.id"), nullable=True
    )
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    reference_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    labels: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    dataset: Mapped[Dataset] = relationship(back_populates="items")


class SimulationRun(Base):
    """Traffic-replay simulation of a candidate provider/model (FR-SIM-002/005)."""

    __tablename__ = "simulation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=False, index=True
    )
    params: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    report: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Entity(Base):
    """Knowledge-graph entity (Phase 4 MVP) with provenance & confidence."""

    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False, default="Term")
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Relationship(Base):
    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=False, index=True
    )
    source_entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entities.id"), nullable=False, index=True
    )
    relationship_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="mentioned_in"
    )
    # Target document (MVP relationships are entity -> document provenance).
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id"), nullable=False, index=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WebAccessJob(Base):
    """
    One broker dispatch through the web access plane (Phase W1).

    Persists the sanitized request, the adapter fallback chain with
    per-source status (failure honesty), the policy decision, and metadata
    of the returned documents (URL, title, content hash, warnings) — never
    raw cookies, credentials, or oversized payloads.  Large artifacts
    belong in object storage (enterprise track).
    """

    __tablename__ = "web_access_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=False, index=True
    )
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    request: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    policy_decision: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    source_status: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    # Document metadata only: url/title/hash/adapter/warnings — not raw bodies.
    documents_meta: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
