from contextlib import asynccontextmanager

from fastapi import FastAPI

from helios.config import settings
from helios.db import init_db
from helios.routes import (
    actions,
    agents,
    audit,
    browser,
    changes,
    completions,
    datasets,
    decisions,
    drift,
    evaluations,
    evolution,
    health,
    ingest,
    knowledge,
    mcp,
    models,
    policies,
    replay,
    review,
    simulations,
    systems,
    traces,
    web,
    workflows,
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
app.include_router(systems.router)
app.include_router(models.router)
app.include_router(policies.router)
app.include_router(decisions.router)
app.include_router(evaluations.router)
app.include_router(changes.router)
app.include_router(replay.router)
app.include_router(drift.router)
app.include_router(audit.router)
app.include_router(agents.router)
app.include_router(ingest.router)
app.include_router(completions.router)
app.include_router(traces.router)
app.include_router(knowledge.router)
app.include_router(review.router)
app.include_router(datasets.router)
app.include_router(simulations.router)
app.include_router(web.router)
app.include_router(mcp.router)
app.include_router(browser.router)
app.include_router(actions.router)
app.include_router(evolution.router)
app.include_router(workflows.router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "health": "/health",
    }
