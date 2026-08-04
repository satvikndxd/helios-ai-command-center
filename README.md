# Project Helios — Gateway (Phase 1 Walking Skeleton)

Helios is an enterprise AI operating system: a governed, observable layer that
sits above models, data, and applications. This repository is the **Phase 1
walking skeleton** — the unified AI gateway with full decision-trace capture,
which every later subsystem (evaluation, retrieval, routing, simulation,
governance) hangs off of.

> Build order (from the architecture spec): **visibility first**, then
> evaluation, grounding, safety, optimization. This slice delivers visibility.

## What's in this slice

- `POST /v1/ai/complete` — unified AI endpoint
- API-key authentication + tenant/application model
- Request normalization into a common internal schema
- Provider adapters, **free-first**:
  - `mock` — zero-dependency default, runs with no config
  - `groq` — free tier, OpenAI-compatible
  - `openrouter` — free models, OpenAI-compatible
  - `gemini` — Google free tier
  - `openai`, `anthropic` — optional, paid, env-var ready
- Full `DecisionTrace` persistence (Postgres in prod, SQLite for tests)
- Cost + latency metering (free providers metered at $0.00)
- `GET /v1/traces` and `GET /v1/traces/{id}` with tenant isolation
- Failed AI calls are still recorded as error traces
- **Async evaluation (Phase 1.5)** — the gateway (hot path) enqueues an
  `EvaluationJob` and returns immediately; a background worker (cold path)
  claims jobs from a Postgres-backed queue (`FOR UPDATE SKIP LOCKED`) and
  writes `evaluation_scores` back onto the trace. Ships with three heuristic
  evaluators: empty-output, latency-SLA, and refusal-detection.

## Quick start (Docker)

```bash
docker compose up --build      # starts Postgres + the gateway
make seed                      # prints a new API key
KEY=<paste-key> make curl-complete
```

## Quick start (local, no Postgres)

The test suite and local dev can run on SQLite with zero external services:

```bash
export HELIOS_DATABASE_URL="sqlite:////tmp/helios.sqlite3"
export PYTHONPATH=src
python -m helios.cli create-api-key --tenant acme --app support   # prints a key
python -m uvicorn helios.main:app --reload
```

```bash
curl -s -X POST http://localhost:8000/v1/ai/complete \
  -H "X-Helios-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"input": "Hello Project Helios"}'
```

Interactive API docs: `http://localhost:8000/docs`

## Using a free LLM provider

The gateway defaults to `mock`. To get real completions for free, grab a key
from any provider below, put it in `.env` (copy from `.env.example`), and set
the default provider — or pass `"provider"` per request.

| Provider   | Free? | Get a key                              | Env var                     |
|------------|-------|----------------------------------------|-----------------------------|
| Groq       | yes   | https://console.groq.com/keys          | `HELIOS_GROQ_API_KEY`       |
| OpenRouter | yes   | https://openrouter.ai/keys             | `HELIOS_OPENROUTER_API_KEY` |
| Gemini     | yes   | https://aistudio.google.com/apikey     | `HELIOS_GEMINI_API_KEY`     |
| OpenAI     | paid  | https://platform.openai.com            | `HELIOS_OPENAI_API_KEY`     |
| Anthropic  | paid  | https://console.anthropic.com          | `HELIOS_ANTHROPIC_API_KEY`  |

Per-request override:

```json
{ "input": "What is Helios?", "provider": "groq" }
```

```bash
HELIOS_DEFAULT_PROVIDER=groq   # or openrouter | gemini
```

## Tests

```bash
python -m pip install -r requirements-dev.txt
PYTHONPATH=src python -m pytest -q
```

Runs entirely on SQLite + the mock provider — no network, no Postgres.

## Async evaluation (hot path vs cold path)

The gateway is latency-sensitive (a user is waiting), so it does the minimum:
authenticate → route → call model → persist trace → **enqueue an eval job** →
return. Evaluation is throughput-sensitive and runs out-of-band in a worker.

```
POST /v1/ai/complete ──hot path──► DecisionTrace + EvaluationJob(pending) ──► 200 OK
                                                │
        evaluation_jobs (Postgres queue)        │ FOR UPDATE SKIP LOCKED
                                                ▼
                         helios.worker ──► EvaluationPipeline ──► trace.evaluation_scores
```

Run a worker locally (against the same DB as the API):

```bash
PYTHONPATH=src python -m helios.worker      # or: make worker
```

Under Docker Compose the `worker` service starts automatically and scales:

```bash
docker compose up --build --scale worker=3   # SKIP LOCKED => no double-processing
```

`SKIP LOCKED` gives us a concurrent, at-least-once job queue on the database we
already run — no Kafka/Redis/Celery. It's the Outbox pattern we'll later back
with Kafka, without the infrastructure today.

## Layout

```
src/helios/
  main.py            FastAPI app + startup
  config.py          Settings (HELIOS_* env vars)
  db.py              SQLAlchemy engine/session
  models.py          Tenant, Application, ApiKey, DecisionTrace, EvaluationJob
  schemas.py         Request/response/trace Pydantic models
  security.py        API-key auth dependency
  normalization.py   Request -> internal schema
  cost.py            Token-usage -> USD (free tiers = $0)
  cli.py             `create-api-key`
  worker.py          Cold-path evaluation worker (SKIP LOCKED queue)
  routes/            health, completions, traces
  providers/         base, mock, openai_compatible, gemini, anthropic + router
  evaluators/        base, heuristics (empty/latency/refusal), pipeline
```

## Intentional Phase-1 tradeoffs

- **No migrations** — uses `create_all`; move to Alembic before production.
- **Placeholder pricing** — real pricing belongs in a model registry (Phase 6).
- **No rate limiting / policy engine / async eval yet** — the trace already
  carries `policy_result` and `evaluation_scores` hooks for those phases.

## Suggested next slice

**Phase 2 — Knowledge retrieval & grounding**: document ingestion, chunking,
embeddings, vector + keyword search, citations, and an LLM-as-judge
`GroundednessEvaluator` that plugs into the existing `EvaluationPipeline`
(no worker changes needed).
