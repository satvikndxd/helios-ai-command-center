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
