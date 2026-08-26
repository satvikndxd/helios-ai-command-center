from contextlib import asynccontextmanager

from fastapi import FastAPI

from helios.config import settings
from helios.db import init_db
from helios.routes import (
    completions,
    datasets,
    health,
    knowledge,
    review,
    simulations,
    traces,
    web,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Phase 1/2 use create_all (+ pgvector extension on Postgres).

    Production-grade Helios should move to Alembic migrations.
    """
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(completions.router)
app.include_router(traces.router)
app.include_router(knowledge.router)
app.include_router(review.router)
app.include_router(datasets.router)
app.include_router(simulations.router)
app.include_router(web.router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "health": "/health",
    }
