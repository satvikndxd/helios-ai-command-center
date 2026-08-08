# Project Helios — Enterprise AI Command Center

Helios is an enterprise AI operating system: a governed, observable command
center that sits above models, data, and applications. This repository holds
the walking skeleton (Phases 1–2): the unified AI gateway with full
decision-trace capture, async evaluation, and tenant-isolated knowledge
retrieval — the base every later subsystem (routing, simulation, governance)
hangs off of.

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
- **Knowledge retrieval & grounding (Phase 2)** — tenant-isolated RAG on
  `pgvector`. Ingest documents (`POST /v1/knowledge/documents` → chunk →
  embed → store in one transaction), then pass `"use_knowledge_base": true`
  to `/v1/ai/complete`: the gateway embeds the query, retrieves the top-k
  nearest chunks **for that tenant only**, injects them as numbered context,
  and returns matching `citations`.
- **Sentinel + policy engine (safety)** — synchronous hot-path checks: PII
  detection/redaction, prompt-injection detection (including in retrieved
  documents, which are treated as untrusted and dropped when poisoned), and
  output leak scanning. Policy-as-code rules enforce preflight and output
  gates (block PII in high-risk requests, redact PII before external
  providers, require citations for high-risk answers, block leaked PII in
  outputs). Blocked decisions are persisted as `status=blocked` traces.
- **Router v2 (registry + fallback)** — model registry with quality/cost/
  privacy metadata; explainable routing (explicit > risk override > default),
  ordered fallback chains on provider failure, and a pre-call cost guardrail
  against `max_cost_usd`. Every decision's chain, reasons, and attempts are
  recorded on the trace.
- **Groundedness & hallucination risk** — claim-level evaluator: splits
  output into claims, scores content-word support against the retrieved
  context stored on the trace, and emits a `hallucination_risk` score. High
  risk or failed evaluators auto-escalate to the human review queue.
- **Human review queue + feedback** — `GET /v1/review/queue`,
  `POST /v1/review/{id}/resolve`; `POST /v1/traces/{id}/feedback` records
  user/business outcomes and thumbs-down escalates to review.
- **Simulator (traffic replay)** — `POST /v1/simulations/run` replays recent
  production traffic against a candidate provider/model, evaluates outputs
  with the same pipeline, and produces a deployment risk report
  (failure-rate delta, failure examples, canary/do-not-deploy
  recommendation).
- **Forge (dataset factory)** — `POST /v1/datasets/build` mines failures /
  negative feedback into versioned evaluation datasets with lineage;
  `GET /v1/datasets/{id}/export` emits JSONL.
- **Knowledge graph MVP** — heuristic entity extraction at ingestion
  (typed: Policy/Service/Product/Incident/Term) with `mentioned_in`
  provenance relationships, entity dedup across documents, and traversal
  endpoints (`/v1/knowledge/entities`, `/v1/knowledge/entities/{id}/documents`).

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

## Knowledge retrieval (RAG)

Vectors live in Postgres via **pgvector** — metadata and embeddings share one
ACID transaction (no ghost vectors), and tenant isolation is a database-level
`WHERE tenant_id = …` applied *before* the distance calculation. The Compose
`db` image is `pgvector/pgvector:pg16` (stock `postgres:16` lacks the
extension); `init_db()` runs `CREATE EXTENSION IF NOT EXISTS vector`.

```bash
# 1. Ingest a document (chunked ~500 chars with 50 overlap, embedded, stored)
curl -s -X POST http://localhost:8000/v1/knowledge/documents \
  -H "X-Helios-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"title": "Refund Policy", "content": "Enterprise customers may request refunds within 30 days..."}'

# 2. Grounded completion with citations
curl -s -X POST http://localhost:8000/v1/ai/complete \
  -H "X-Helios-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"input": "What is the refund window?", "use_knowledge_base": true}'
```

Embeddings are provider-abstracted like LLMs: `mock` (default, deterministic
hashed bag-of-words — no network), `gemini` (free tier, set
`HELIOS_EMBEDDING_DIM=768`), or `openai`. On non-Postgres databases (the test
suite's SQLite) retrieval transparently falls back to Python cosine similarity
over the tenant's chunks; Postgres + `<=>` is the production path.

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
  chunking.py        Overlapping character chunker
  retrieval.py       Tenant-isolated vector search (pgvector / Python fallback)
  routes/            health, completions, traces, knowledge
  providers/         base, mock, openai_compatible, gemini, anthropic + router
  evaluators/        base, heuristics (empty/latency/refusal), pipeline
  embeddings/        base, mock (hashed BoW), live (gemini/openai)
```

## Intentional Phase-1 tradeoffs

- **No migrations** — uses `create_all`; move to Alembic before production.
- **Placeholder pricing** — real pricing belongs in a model registry (Phase 6).
- **No rate limiting / policy engine / async eval yet** — the trace already
  carries `policy_result` and `evaluation_scores` hooks for those phases.

## Spec coverage & enterprise track

All seven pillars of the Helios spec now have working walking-skeleton slices:
gateway+traces, evaluation, retrieval+grounding, hallucination detection,
routing, simulation, and the dataset factory — plus policy-as-code, a human
review queue, and a knowledge-graph MVP.

Deliberately **not** built at this scale (the enterprise track, in spec order):
Kafka/ClickHouse event streaming, OPA policy engine, Neo4j graph store,
Kubernetes/Helm/multi-region deployment, SSO/OIDC + full RBAC, streaming
responses with eval hooks, semantic caching, LLM-as-judge evaluators, Label
Studio-grade annotation UI, dashboards (Grafana/OTel), SDKs, and the
fine-tuning platform. Each slots in behind an existing interface (TraceSink,
BaseEvaluator, policy engine, provider/embedding adapters) without rewrites.
