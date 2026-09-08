<div align="center">

<img src="assets/helios-banner.svg" alt="HELIOS — Governed AI Command Center" width="880">

<br/><br/>

# HELIOS
## The AI Governance Control Plane

**GOVERNED AI COMMAND CENTER — OBSERVE / CONSTRAIN / ENABLE**

**HELIOS defines what AI systems are allowed to do, records what they actually
did, evaluates whether they behaved correctly, and provides continuous
assurance that they remain within policy.**

```text
AI SYSTEMS
IN CONTEXT
UNDER CONTROL
```

<br/>

<img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-00E676?style=flat-square&labelColor=0A1A0F">
<img alt="Tests" src="https://img.shields.io/badge/tests-307_passing_in_~13s-34D399?style=flat-square&labelColor=0A1A0F">
<img alt="Runtime deps" src="https://img.shields.io/badge/runtime_deps-8-34D399?style=flat-square&labelColor=0A1A0F">
<img alt="License" src="https://img.shields.io/badge/license-MIT-00E676?style=flat-square&labelColor=0A1A0F">

<br/><br/>

[The Four Questions](#the-four-questions) • [Quick Start](#quick-start) •
[The Flagship Demo](#the-flagship-demo) • [Core Concepts](#core-concepts) •
[API](#api-surface) • [Architecture](#architecture) •
[Security](#security-as-a-governance-control) • [Development](#development)

</div>

---

```
            AI SYSTEM   (agent · application · workflow)
                │
                ▼
         ┌──────────────┐
         │    HELIOS    │
         ├──────────────┤
         │ IDENTITY     │   who / what is this AI system?
         │ POLICY       │   what is it allowed to do?
         │ EVIDENCE     │   what did it actually do?
         │ ASSURANCE    │   does it remain within policy?
         └──────┬───────┘
                │
                ▼
       MODELS / DATA / TOOLS
```

HELIOS is not primarily an agent firewall, an AI gateway, or an observability
dashboard. Security, agent execution, observability, evaluation and approvals
are **components of one governance system** — every action an AI system takes
flows through a decision that is *explainable, evidenced, replayable, and
auditable*.

## The Four Questions

Everything in HELIOS exists to make four questions easy to answer — with
evidence, never vibes:

| Question | Plane | HELIOS answers it with |
|---|---|---|
| **What AI systems do we have?** | IDENTITY | The AI System Registry: owner, purpose, environment, autonomy level (L0–L5), risk class, models, tools, data classes, policies, oversight requirements — versioned on every change |
| **What are they allowed to do?** | POLICY | Versioned policy sets (tool, model, data, autonomy, environment), a governed Model Registry (approval + data ceilings + environment bounds), permission grants with resource constraints, contextual risk, and a built-in autonomy ceiling |
| **What did they actually do?** | EVIDENCE | A hierarchical trace per run (model calls, retrievals, data accesses, proposals, policy decisions, approvals, executions, human reviews, outcomes), normalized **AI Decision Records**, event-backed **data lineage**, and a generated **WHY** explanation for every decision |
| **Can we prove they stay within policy?** | ASSURANCE | Evidence-based evaluations, a transparent **governance score**, deterministic **replay** of history against candidate policies/models, first-class **AI Change Management** (no invisible production changes), and **drift detection** against stored baselines |

## Quick Start

Zero configuration, zero API keys, no network:

```bash
git clone https://github.com/satvikndxd/helios-ai-command-center
cd helios-ai-command-center
pip install -r requirements.txt

# the flagship governance demo (SQLite + scripted models, fully deterministic)
PYTHONPATH=src python -m helios.governance.demo      # or: make governance-demo

# the governed agent terminal
PYTHONPATH=src python -m helios.tui                  # or: make tui
```

Connect real models and tools when ready:

```bash
export HELIOS_GROQ_API_KEY=...          # or OPENAI / ANTHROPIC / GEMINI / OPENROUTER
export HELIOS_GITHUB_TOKEN=ghp_...      # enables real github.* tools
export HELIOS_GITHUB_REPO=you/yourrepo  # the ONE repo the agent may touch
```

## The Flagship Demo

One canonical story — `release-agent` — exercises the whole control plane
(the demo above runs it end-to-end; CI proves it headless in
`tests/test_flagship_demo.py`):

```
1  REGISTER     AI system "release-agent" — owner release-engineering,
                environment production, autonomy L3, risk HIGH,
                model-x v17, GitHub tools, data class INTERNAL
2  MODEL        model-x v17 registered → APPROVED by security-review
                (data ceiling: PUBLIC/INTERNAL)
3  POLICY       "release-agent-governance" activated:
                merges toward protected branches → REQUIRE_HUMAN_REVIEW
4  DEPLOY GATE  L3 in production → activation REQUIRES APPROVAL
                (built-in autonomy ceiling) → CTO approves → ACTIVE
5  RUN          agent investigates repo → reads code → modifies code →
                runs tests [HIGH risk → human approves] → branch → commit →
                opens PR #142 → attempts production merge
6  CRITICAL     merge to protected 'main' scored CRITICAL;
                policy + governance demand a human; approval binds to the
                EXACT payload hash (mutation invalidates it)
7  REVIEW       human inspects action · risk reasons · policy rule ·
                governance rule · data classification · args → APPROVES
8  EXECUTE      HELIOS executes precisely the approved payload and records
                an AI Decision Record + full hierarchical trace
9  WHY          every check that led to the decision, generated from
                recorded evidence:
                  ✓ ai_system: attributed to registered 'release-agent'
                  ✓ permissions: github.merge scoped to acme/release-service
                  ✓ tool_policy: approval_for_high_risk — OBTAINED
                  ✓ governance: production-merge-review — oversight obtained
                  ✓ data_classification: INTERNAL within model ceiling
                  ✓ human_oversight: reviewer release-manager
10 REPLAY       same run vs CANDIDATE policy v2 →
                "+1 newly gated (create_pr)" — tested BEFORE deployment
11 ASSURE       evaluation 1.0 passed · governance score 100/100 with
                component breakdown · drift baseline stored, check clean ·
                audit report exported (JSON)
```

> *"HELIOS knows WHAT this AI system is, WHAT it is allowed to do, WHAT it
> actually did, WHY it was allowed to do it, WHO supervised it, and WHETHER it
> remains within policy."*

## Core Concepts

### AI System Registry (IDENTITY)

The system — not the session, not the model — is the unit of governance.

```json
{
  "system_id": "claims-agent",
  "owner": "claims-platform",
  "purpose": "claims triage",
  "environment": "production",
  "autonomy_level": 3,
  "risk_class": "HIGH"
}
```

Every mutation snapshots an immutable version (who changed what, when, why).
Sessions bind to systems; every trace event, decision record, approval, and
evaluation is attributed to one.

### Autonomy Levels

```
L0 informational · L1 recommendations · L2 supervised actions
L3 conditional autonomy · L4 high autonomy · L5 unrestricted
```

The built-in ceiling for production deployments: **L0–L2 allow · L3 requires
an approved deployment · L4/L5 blocked** — enforced at registration,
activation, and on posture changes while active (a change that would breach
the ceiling is refused with the matching rule). Legacy
`supervised`/`autonomous` sessions map deterministically to L2/L4.

### Model Governance

Models are governed independently of agents: provider · version ·
capabilities · region · pricing metadata · risk class · **approval status** ·
**allowed data classes** (severity ceiling) · **allowed environments**.
Unknown model on a governed system → deny. Every model call passes a recorded
preflight: *is this model approved, may it receive this data class, in this
environment?* — classified from declared classes **and** content detection
(counts only; raw sensitive content is never persisted).

### Data Governance & Lineage

```
PUBLIC · INTERNAL · CONFIDENTIAL · SENSITIVE · PII
```

```
DATA SOURCE → RETRIEVAL → MODEL → TOOL → OUTPUT → DESTINATION
```

Every read/network tool result and every write destination is recorded as a
classified data-flow event. Lineage graphs are derived **exclusively** from
recorded events — every edge cites the event ids that prove it. Blocked flows
appear as violations with the recorded denial reason. There is no decorative
lineage: if it wasn't recorded, it isn't drawn.

### Policy (POLICY)

Two deterministic layers, both versioned, serializable, replayable, and
explained rule-by-rule:

* **Tool policies** (broker): ordered rules over tool · capability · risk ·
  environment · autonomy → `ALLOW / DENY / REQUIRE_APPROVAL`; unmatched →
  **deny by default**.
* **Governance policies**: ordered rules over system · model · data ·
  autonomy · environment · lifecycle → the above plus
  `REQUIRE_HUMAN_REVIEW / BLOCK_DEPLOYMENT`; persisted as candidate →
  active → archived sets, testable via dry-run and replay **before**
  activation.

```yaml
policy: production-autonomy
when:   {subject: [deployment], environment: [production], autonomy_level: {gte: 3}}
effect: require_approval     # L4/L5: block_deployment
```

### Evidence & AI Decision Records (EVIDENCE)

Every run produces a hierarchical trace —
`model_call · retrieval · data_access · tool_proposal → (permission · risk ·
policy · governance) · approval · tool_execution · human_review · outcome` —
and normalized **Decision Records** projected from it (never hand-written):

```json
{
  "system": "release-agent", "action": "github.merge_pr",
  "decision": "EXECUTED", "model": "scripted/model-x", "model_version": "17",
  "risk": "critical", "data_class": "INTERNAL",
  "policy": {"tool_policy": {"rule_id": "approval_for_high_risk"},
             "governance": {"matched_rules": ["production-merge-review"]}},
  "oversight": {"required": true, "actual": "payload_bound",
                "reviewer": "release-manager"},
  "evidence": {"trace_event_ids": ["..."], "approval_id": "..."}
}
```

`GET /v1/decisions/{id}/explanation` answers **WHY** — assembled from the
recorded state, never canned UI text. Secrets are scrubbed on every write
path; approvals bind to `sha256(tool + canonical args)` so payload tampering
invalidates them.

### Human Oversight

Approvals, denials, reviews, escalations, delegated decisions, comments,
expiration, and scope binding (environment / system / max-uses) — all part of
the governance record. HELIOS answers: *where was a human involved? where
SHOULD one have been? did the AI bypass required oversight?* — including gaps
between **declared** oversight requirements and what active policies actually
enforce.

### Assurance: Score · Replay · Changes · Drift

* **Governance score** — 10 weighted components (identity, model approval,
  data policy, oversight, compliance, evaluation, auditability, security,
  drift, documentation), each a list of checks with reasons and evidence
  refs. Components without evidence report `insufficient_evidence` — never a
  free 100.
* **Replay** — re-evaluate recorded runs against candidate tool policies,
  governance sets, system posture, or model registry state. Nothing executes;
  every diff cites evidence. Policy changes are testable **before**
  deployment.
* **AI Change Management** — model · prompt · policy · tool · dataset ·
  config changes follow `candidate → replay → evaluation → risk comparison →
  human review → approved → deployed`, with previous/candidate snapshots, a
  digest-bound review approval, and one-call rollback that is itself a
  governed change. No production governance change happens invisibly.
* **Drift** — deterministic baselines (approval rate, denial rate, risk/tool/
  model mixes, bypasses, eval pass rate, posture, active policies) compared
  under documented thresholds with minimum-sample safeguards. Example:
  *baseline 100% of writes approved → observed 61% → `governance_drift`
  (critical)*.

### The operator console

The TUI is an **equipment console**, not a dashboard: an equipment
identification plate on boot (serial, revision, environment, state), 1px
rules, rectangular panels, instrument glyphs, dense monospace metadata —
quiet, severe, technical. The green is a signal (`● ENFORCING`), never
decoration. It degrades gracefully: `NO_COLOR`, legacy encodings
(`HELIOS_ASCII=1`), and 80-column terminals all get a complete,
non-overflowing interface.

```text
┌─ HX-001 ──────────── PROPERTY OF HELIOS ──────────── FIELD UNIT ─┐
│   H E L I O S                              │      SERIAL ACME-CORP│
│   GOVERNED AI COMMAND CENTER              ─◯─      REV   V1.5.0  │
│   OBSERVE / CONSTRAIN / ENABLE             ●       STATE GOVERNED│
└──────────────────────────────────────────────────────────────────┘
helios@control:~$ /systems
```

Keyboard-first: `1-9/a` switch planes' views, `j/k` + arrows move, `Enter`
opens the selected record, `Esc` back, `Tab` cycle, `w` the WHY view,
`A/D/E/C` decide an approval, `?` reference, `q` quit. The full slash-command
surface is preserved:

```
/systems  /system <id>  /models  /policies  /traces  /evaluations
/approvals  /changes  /drift  /audit <system>  /score <id>  /why <record>
/replay system <id> [set-id]
```

plus the governed agent shell (`/sessions`, `/trace`, `/resume`, `/cancel`,
`/approve`, `/deny`) with its state machine drawn as an instrument column
(`THINKING ↓ TOOL_PENDING ↓ RUNNING ↓ AWAITING_APPROVAL ↓ EXECUTED`).
Every field on screen is backed by an API response — no invented metrics;
failures render as system diagnostics with status, detail, and evidence ids.
The system operator plate:

```
╭─ HELIOS · AI GOVERNANCE CONTROL PLANE ─────────────────────╮
  SYSTEM     release-agent  (Release Agent)
  OWNER      release-engineering · acme
  RISK       HIGH
  AUTONOMY   L3
  LIFECYCLE  active
  MODEL      scripted/model-x v17 ✓ APPROVED
  DATA       INTERNAL
  POLICIES   2 active
  EVALUATION 100.0% (passed)
  GOVERNANCE 100.0 / 100  ok
  DRIFT      ✓ none open
  VIOLATIONS 0
  APPROVALS  0 active · 3 total
╰────────────────────────────────────────────────────────────╯
```

## API Surface

Multi-tenant (`X-Helios-API-Key`), additive over the V1 gateway API.

| Boundary | Endpoints |
|---|---|
| `/v1/systems` | register · list/search · inspect · patch · lifecycle (gated) · archive · versions · lineage · oversight · drift · audit |
| `/v1/models` | register · list · lookup · **check** (compatibility WHY) · patch · approve · deny · deprecate |
| `/v1/policies` | tool-policy versions + governance sets: create · list · activate · archive · **dry-run** |
| `/v1/agent` | sessions · runs · events · lineage · cancel/resume/retry · replay · approvals (decide/delegate/escalate/comment) · tools invoke |
| `/v1/decisions` | query records · inspect · **explanation** · rebuild |
| `/v1/evaluations` | run governance/benchmark evaluations · list · inspect |
| `/v1/changes` | create · replay · evaluate · risk-comparison · submit-review · approve · reject · deploy · rollback |
| `/v1/replay` | system-level replay against candidates |
| `/v1/drift` | open signals · acknowledge |
| `/v1/audit` | per-system governance evidence report (JSON export) |
| V1 (kept) | `/v1/ai/complete` · `/v1/traces` · `/v1/ingest` (OTLP) · `/v1/knowledge` · `/v1/mcp` · `/v1/actions` · `/v1/workflows` · … |

## Architecture

```
src/helios/
├── governance/     # the control plane
│   ├── systems.py           IDENTITY   AI System Registry
│   ├── models_registry.py   IDENTITY   Model Registry + compatibility checks
│   ├── autonomy.py          POLICY     L0–L5 vocabulary (+ legacy mapping)
│   ├── data.py              POLICY     data classes + classifier (sentinel-backed)
│   ├── policy.py            POLICY     governance policy sets + built-in ceiling
│   ├── engine.py            POLICY     deployment / model-call / tool-action gates
│   ├── lineage.py           EVIDENCE   event-derived data lineage
│   ├── decisions.py         EVIDENCE   AI Decision Records + WHY explanations
│   ├── oversight.py         EVIDENCE   expiry · scope · delegation · compliance
│   ├── evaluators.py        ASSURANCE  deterministic governance metrics
│   ├── judge.py             ASSURANCE  opt-in LLM judge (never the only signal)
│   ├── score.py             ASSURANCE  transparent composite score
│   ├── replay.py            ASSURANCE  system-level candidate replay
│   ├── changes.py           ASSURANCE  change lifecycle + deploy/rollback
│   ├── drift.py             ASSURANCE  baselines + deterministic detectors
│   ├── audit.py             ASSURANCE  governance evidence reports
│   └── demo.py              the flagship end-to-end scenario
├── broker/         # ToolBroker: the single execution boundary (kept from V1)
│                   # manifest → args → identity/permissions → data classification
│                   # → contextual risk → policy → governance overlay → oversight
│                   # → idempotent execution → sanitized result → evidence
├── agent/          # governed runtime: sessions, state machine, replay
├── tools/          # filesystem · shell · git · GitHub · HTTP · MCP
├── routes/         # FastAPI boundaries above
├── tui/            # governance control center + agent terminal
└── models.py       # all persistence (SQLAlchemy; SQLite/Postgres)
```

Invariants (non-negotiable, enforced by tests):

1. No tool executes outside `ToolBroker.invoke`; no model call happens outside
   a recorded governance preflight.
2. Approvals bind to the exact payload hash; mutation invalidates them.
3. Every decision is deterministic, serialized into evidence, and explained
   from actual policy state.
4. Every lineage edge, score point, and drift signal cites stored evidence —
   missing evidence is reported as missing.
5. Secrets never persist; sensitive content is classified by counts, not copies.
6. Unknown tools, unknown models (on governed systems), and
   blocked/suspended/archived systems are deny-by-default.
7. No production governance change happens invisibly.
8. HELIOS reports **governance evidence** — policy status, audit records,
   control status. It never claims legal or regulatory compliance.

## Security as a Governance Control

PII detection & redaction, secret scrubbing, prompt-injection defense
(quarantine + withholding for external content), tenant isolation on every
route, tool-result sanitization, filesystem jailing, shell restrictions,
resource-constrained grants, and network allowlists are all enforced **inside**
the planes above — and themselves evidenced: injection detections, redaction
counts, and denials appear in traces, decision records, evaluations, and the
security score component. They are controls of the governance system, not the
product itself.

## The Governed Agent Runtime (kept from V1)

The agent runtime remains a first-class, governed execution surface:
persistent sessions, an explicit run state machine (`thinking → tool_pending →
running → awaiting_approval → blocked → completed/failed/cancelled`),
cancellation, resume-after-approval, retry, forking, real GitHub/git/shell/
filesystem/HTTP/MCP tools, OTLP trace ingestion for agents HELIOS does not run,
and deterministic replay. The narrative is now:

```
AI SYSTEM → HELIOS GOVERNANCE → AGENT EXECUTION
```

## Development

```bash
pip install -r requirements-dev.txt
PYTHONPATH=src python -m pytest -q      # 276 tests, ~13s, zero external deps
make governance-demo                    # the flagship story, end to end
make tui                                # governance control center + agent
```

* Docs: [`docs/GOVERNANCE_ARCHITECTURE.md`](docs/GOVERNANCE_ARCHITECTURE.md)
  (plane-by-plane module map), [`docs/GOVERNANCE_ROADMAP.md`](docs/GOVERNANCE_ROADMAP.md)
  (delivery plan + migrations + compatibility risks),
  [`docs/GOVERNANCE_GUIDE.md`](docs/GOVERNANCE_GUIDE.md) (operator guide).
* Database: SQLite by default; Postgres (+pgvector) via `HELIOS_DATABASE_URL`.
  Schema is additive; `python -m helios.migrate` applies nullable-column
  migrations idempotently.

## Also in the box (V1 capabilities, de-emphasized)

Multi-provider gateway with fallback chains (Groq · OpenRouter · Gemini ·
OpenAI · Anthropic · any OpenAI-compatible endpoint), RAG with citation
provenance, the completions policy/evaluation worker loop, MCP server
governance (trust gating, allowlists, budgets), a web-research broker with
source audit, domain workflow packs, and self-evolution proposals. They keep
working; they are no longer the story.

---

<div align="center">

**HELIOS governs AI.**

IDENTITY → what AI systems exist? · POLICY → what may they do? ·
EVIDENCE → what did they do? · ASSURANCE → can we prove they stay within policy?

</div>
