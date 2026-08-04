from contextlib import asynccontextmanager

from fastapi import FastAPI

from helios.config import settings
from helios.db import engine
from helios.models import Base
from helios.routes import completions, health, traces


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Phase 1 uses create_all for simplicity.

    Production-grade Helios should move to Alembic migrations.
    """
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(completions.router)
app.include_router(traces.router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "health": "/health",
    }
