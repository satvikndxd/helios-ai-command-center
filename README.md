# Project Helios — Enterprise AI Command Center

<p align="center">
  <img src="assets/helios-banner.svg" alt="Helios — Enterprise AI Command Center" width="860">
</p>

<p align="center">
  <img alt="Release" src="https://img.shields.io/badge/release-MVP-4CC9F0?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-4CC9F0?style=flat-square">
  <img alt="Tests" src="https://img.shields.io/badge/tests-52_passing-FFD166?style=flat-square">
  <img alt="Status" src="https://img.shields.io/badge/status-stable-FFD166?style=flat-square">
</p>

<p align="center">
  <img src="assets/helios-logo.svg" alt="Helios logo" width="160">
</p>

**Helios** is an enterprise AI operating system: a governed, observable command
center that sits above models, data, and applications. This repository holds
the **complete Helios MVP** — all seven pillars of the architecture spec as
working, tested slices: the unified AI gateway with decision-trace capture,
async evaluation, tenant-isolated grounded RAG, safety guardrails (Sentinel +
policy-as-code), hallucination detection with human review, intelligent
routing with fallback, pre-deployment simulation, continuous dataset
generation (Forge), and a knowledge-graph MVP — the base every later
enterprise subsystem hangs off of.

> Build order (from the architecture spec): **visibility first**, then
> evaluation, grounding, safety, optimization, continuous improvement.
> This repo delivers all six, in that order, one commit per phase.

## Contents

- [What's inside](#whats-inside)
- [Quick start (Docker)](#quick-start-docker)
- [Quick start (local, no Postgres)](#quick-start-local-no-postgres)
- [Using a free LLM provider](#using-a-free-llm-provider)
- [Tests](#tests)
- [Async evaluation](#async-evaluation-hot-path-vs-cold-path)
- [Knowledge retrieval (RAG)](#knowledge-retrieval-rag)
- [Layout](#layout)
- [Measured results](#measured-results)
- [Intentional MVP tradeoffs](#intentional-mvp-tradeoffs)
- [Spec coverage & enterprise track](#spec-coverage--enterprise-track)
- [Terminal-first agent interface](#terminal-first-agent-interface)
- [Built-in and custom gateways](#built-in-and-custom-gateways)
- [YC-scale product direction](#yc-scale-product-direction)
- [YC27 technical checklist](docs/YC27_TECHNICAL_CHECKLIST.md)

## What's inside

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
- **Terminal agent interface (TUI)** — `python -m helios.tui`: a
  terminal-first REPL over the governed Helios path (marked **GOVERNED**) or
  any OpenAI-compatible gateway (marked **DIRECT**), with a data-driven
  gateway catalog, custom gateway profiles (`gateway-add`/`gateway-list`,
  no secrets stored), and dynamic `/models` discovery.

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
  cli.py             `create-api-key`, `gateway-add`, `gateway-list`
  worker.py          Cold-path evaluation worker (SKIP LOCKED queue)
  chunking.py        Overlapping character chunker
  retrieval.py       Tenant-isolated vector search (pgvector / Python fallback)
  gateways.py        Gateway catalog (built-in + custom profiles, /models discovery)
  tui/               Terminal agent interface (GOVERNED / DIRECT modes)
  routes/            health, completions, traces, knowledge
  providers/         base, mock, openai_compatible, gemini, anthropic + router
  evaluators/        base, heuristics (empty/latency/refusal), pipeline
  embeddings/        base, mock (hashed BoW), live (gemini/openai)
```

## Measured results

Every number below was observed on this codebase — from the test suite or a
live end-to-end run of the spec's §24 scenario (ingest refund policy → ask a
grounded question → block a PII request → evaluate → review → dataset →
simulate). No projections.

| Metric | Measured |
|---|---|
| Test suite | **52/52 passing in ~2s**, zero external services (SQLite + mock providers) |
| Gateway hot-path overhead | **~2 ms** (49–52 ms end-to-end incl. the mock's simulated 50 ms inference; spec target: <100 ms p50) |
| Groundedness on the §24 refund query | **0.857** (6/7 claims supported), hallucination risk **0.143**, 1 citation |
| Retrieval ranking | relevant doc scored **0.344** vs **0.000** for the irrelevant doc (cosine, top-k=3) |
| Cross-tenant isolation | **0 leaks** — tenant B retrieving tenant A's "secret" doc: blocked at the query layer, covered by a dedicated test |
| Policy enforcement | high-risk PII request → **HTTP 403** with persisted `status=blocked` trace; low-risk PII → allowed + flagged + redacted before external providers |
| Poisoned-document defense | retrieved chunks containing injection patterns are dropped before prompt assembly (tested) |
| Knowledge graph | **5 typed entities** extracted from one policy doc (Policy/Term), deduplicated across documents, each with `mentioned_in` provenance |
| Feedback loop | thumbs-down → review-queue item **in the same request**; blocked/failed traces → versioned dataset (`failure-cases:v1 → v2` lineage verified) |
| Simulator | replayed production traces through a candidate, same eval pipeline; report: baseline vs candidate failure rate + `canary_1_percent` / `do_not_deploy` recommendation |
| Fallback routing | provider failure → next candidate in chain; every attempt recorded on the trace (tested via unsupported-provider path) |
| Footprint | **~4.5k LOC** src + **~1.1k LOC** tests, **16 API endpoints**, 2 deployable processes (api + worker) + a terminal UI, 1 database |

## Intentional MVP tradeoffs

- **No Kafka/ClickHouse** — the async eval queue is Postgres
  `FOR UPDATE SKIP LOCKED`. Migrate to Kafka only when event throughput
  exceeds single-node Postgres limits; the `TraceSink`-shaped worker boundary
  makes that a swap, not a rewrite.
- **No Alembic migrations** — uses SQLAlchemy `create_all`. Move to Alembic
  before any production schema change.
- **Placeholder pricing** — real dynamic pricing belongs in a centralized
  control-plane model registry (enterprise track); free tiers are metered $0.
- **Heuristic extraction & evaluation** — the knowledge-graph extractor,
  groundedness scorer, and PII/injection detectors are deterministic
  regex/overlap heuristics. The enterprise track swaps in NER/LLM-as-judge
  behind the same `BaseEvaluator`/`extract_entities` interfaces.
- **No rate limiting yet** — quotas/budgets per tenant exist only as the
  per-request `max_cost_usd` guardrail in the router.

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

## Terminal-first agent interface

Helios includes a terminal UI designed for fast, agent-centric work rather
than API administration. It keeps the governed Helios path as the default —
prompts still produce traces and pass through policy, routing, and
evaluation — and it is always labeled: the governed route shows **GOVERNED**,
direct custom-gateway mode shows **DIRECT** so you know when Helios
governance is bypassed. Local and custom OpenAI-compatible gateways can also
be selected for development and model experimentation.

Install dependencies and launch the TUI:

```bash
python -m pip install -r requirements-dev.txt
export HELIOS_API_KEY="<key created with make seed>"
PYTHONPATH=src python -m helios.tui --gateway helios
# or: make tui GATEWAY=helios
```

The TUI supports `/help`, `/gateway`, `/connect`, `/model`, `/models`,
`/refresh`, `/status`, `/clear`, and `/quit`. Use `Ctrl+K` to focus the
prompt, `Ctrl+L` to clear the screen, and `Ctrl+C` to exit. Every governed
turn prints its `trace_id`, model, cost, latency, and citation count.

## Built-in and custom gateways

Helios ships a data-driven catalog covering major hosted providers,
aggregators, enterprise gateways, and local OpenAI-compatible servers. The
seed catalog includes OpenAI, OpenRouter, Groq, Together, Fireworks,
DeepInfra, Hyperbolic, NVIDIA, Cerebras, SambaNova, DeepSeek, Mistral, xAI,
Cohere, Perplexity, Hugging Face, Cloudflare, Ollama, LM Studio, vLLM,
llama.cpp, SGLang, LocalAI, LiteLLM, and Portkey. The catalog is
deliberately extensible: when a gateway exposes `GET /models`, `/refresh`
discovers its current models instead of depending on a hard-coded model
list.

Credentials are never written to the gateway profile. Save only the
environment-variable name:

```bash
PYTHONPATH=src python -m helios.cli gateway-add my-gateway \
  --base-url https://gateway.example.com/v1 \
  --provider custom \
  --api-key-env MY_GATEWAY_API_KEY \
  --model my-model

export MY_GATEWAY_API_KEY="<secret>"
PYTHONPATH=src python -m helios.cli gateway-list
PYTHONPATH=src python -m helios.tui --gateway my-gateway
```

For a local server that does not require authentication:

```bash
PYTHONPATH=src python -m helios.cli gateway-add ollama-local \
  --base-url http://localhost:11434/v1 \
  --provider ollama \
  --model llama3.2
```

The direct gateway mode uses the standard OpenAI Chat Completions contract.
The governed `helios` mode uses `/v1/ai/complete` and preserves Helios
traces, policy checks, and routing. This split makes the TUI useful for both
production governance and local provider exploration while keeping the
enterprise path explicit. Custom profiles are stored as JSON
(`~/.helios/gateways.json`, override with `HELIOS_GATEWAYS_PATH`) and
`gateway-add` refuses anything that looks like a raw credential.

## YC-scale product direction

The terminal UI is the first product surface for a YC-scale Helios: fast
like a coding agent, portable across models and gateways, and differentiated
by governed execution. The next product milestones are streaming responses,
tool calls, durable sessions, agent manifests, approvals, MCP connectivity,
OpenTelemetry ingestion, and a release gate that combines policy and
evaluation results before an agent can be promoted.

The full technical roadmap — P0→P2 priorities, acceptance tests, build
order, and the minimum genuinely competitive feature set — lives in
[docs/YC27_TECHNICAL_CHECKLIST.md](docs/YC27_TECHNICAL_CHECKLIST.md).
