from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from helios.config import settings


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)


def init_db() -> None:
    """
    Create schema. On Postgres, ensure the pgvector extension exists first
    (required by the chunks.embedding VECTOR column).

    Phase 1/2 use create_all; production should move to Alembic migrations.
    """
    # Imported here to avoid a circular import (models imports nothing from db,
    # but keeping db importable without the model graph is convenient).
    from helios.models import Base

    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()

    Base.metadata.create_all(bind=engine)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_db():
    """
    FastAPI dependency for database sessions.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
