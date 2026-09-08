<div align="center">

<img src="assets/helios-banner.svg" alt="HELIOS" width="880">

<br/><br/>

# HELIOS
## The AI Governance Control Plane

**HELIOS defines what AI systems are allowed to do, records what they actually
did, evaluates their behavior, and continuously verifies that they remain
within policy.**

<br/>

<img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-00E676?style=flat-square&labelColor=0A1A0F">
<img alt="Tests" src="https://img.shields.io/badge/tests-176_passing_in_~6s-34D399?style=flat-square&labelColor=0A1A0F">
<img alt="Runtime deps" src="https://img.shields.io/badge/runtime_deps-8-34D399?style=flat-square&labelColor=0A1A0F">
<img alt="License" src="https://img.shields.io/badge/license-MIT-00E676?style=flat-square&labelColor=0A1A0F">

</div>

---

Organizations are deploying AI systems — agents, copilots, autonomous
workflows — faster than they can answer four questions about them:

```
                       AI SYSTEM
                           │
                           ▼
                 ┌───────────────────┐
                 │      HELIOS       │
                 ├───────────────────┤
                 │ IDENTITY   ─ who is this AI system?
                 │ POLICY     ─ what is it allowed to do?
                 │ EVIDENCE   ─ what did it actually do?
                 │ ASSURANCE  ─ is it still within policy?
                 └─────────┬─────────┘
                           │
                           ▼
                 MODELS / DATA / TOOLS
```

HELIOS governs the whole lifecycle: it knows **what** each AI system is, **what**
it may do, **what** it actually did, **why** it was allowed, **who** supervised
it, and **whether** it still behaves within policy — every answer backed by
recorded evidence, never decorative UI.

---

## The four planes

### 1 · Identity — *what AI systems do we have?*

A first-class **AI System Registry**. Every system has an owner, purpose,
environment, lifecycle, risk class, an autonomy level (L0–L5), and the
models / tools / data classes / policies it is bound to. Every change is
versioned and audited.

```console
$ helios                 # opens the governance control center
› /systems
   SYSTEM        OWNER            ENV      AUTONOMY  RISK    LIFECYCLE
 ● claims-agent  claims-platform  staging  L3        HIGH    active
```

```
L0 informational · L1 recommendations · L2 supervised · L3 conditional
L4 high autonomy · L5 unrestricted     — policies can cap autonomy per environment
```

### 2 · Policy — *what is it allowed to do?*

The policy engine reasons over **actions, tools, models, data classes,
environments, autonomy levels, and system risk** — deterministic, versioned,
serializable, replayable. Outcomes: `ALLOW · DENY · REQUIRE_APPROVAL ·
REQUIRE_HUMAN_REVIEW · BLOCK_DEPLOYMENT`.

- **Model governance** — a **Model Registry** governs models independently of
  any agent. `Claude-X + PUBLIC → ALLOWED`, `Claude-X + CONFIDENTIAL financial
  → DENIED`, `unknown model → NOT APPROVED`. A model change is a governance event.
- **Data governance** — every action is classified (`PUBLIC < INTERNAL <
  CONFIDENTIAL < SENSITIVE < PII`) from Sentinel signals + declared context;
  confidential data flowing to a tool routes into human review.

```
DENY · rule deny_autonomous_production_writes
reason: production write forbidden for autonomous agents
```

### 3 · Evidence — *what did it actually do?*

Every meaningful step is a node in a hierarchical trace:

```
AI_SYSTEM → RUN
   ├── MODEL_CALL / MODEL_GOVERNANCE
   ├── DATA_ACCESS
   ├── TOOL_PROPOSAL
   │     ├── PERMISSION_EVALUATION
   │     ├── RISK_EVALUATION
   │     ├── POLICY_EVALUATION
   │     ├── APPROVAL
   │     └── TOOL_EXECUTION
   └── OUTCOME
```

Each governed decision also becomes a normalized, versioned **AI Decision
Record**. And every decision answers **WHY**, from real recorded state:

```console
› /why 3c3dfa
┌─[ WHY ]──────────────────────────────────────────────────┐
│  Why did 'github.merge_pr' require human oversight?      │
│                                                          │
│  ✓ AI system registered: release-agent (release-platform)│
│  ✓ Actor identified: helios-agent                        │
│  ✓ Model governance: scripted/release-model — approved   │
│  ✓ Data classification: INTERNAL                         │
│  ✗ Risk assessment: risk = CRITICAL (score 0.95)         │
│  ✗ Policy matched: helios-default-v1 · approval_for_high │
│  ✗ Human oversight: required (pending approval)          │
│                                                          │
│  verdict  APPROVAL REQUIRED                              │
└──────────────────────────────────────────────────────────┘
```

Secrets are never persisted. Tool output is untrusted input — injection is
flagged and cannot override policy.

### 4 · Assurance — *is it still trustworthy?*

- **Governance score** — a transparent weighted status; every check reports
  pass/warn/fail and the fact behind it. No vanity number.

```console
› /governance release-agent
  ✓ Identity            owner release-platform, purpose set, lifecycle active
  ✓ Model approval      all 1 declared models approved
  ✓ Data policy         model-use compliance 100% over 2 model uses
  ✓ Human oversight     oversight compliance 100%; no bypasses
  ✓ Policy compliance   100% of 8 decisions within policy (0 denied)
  ✓ Evaluation          task success 100%; PII protection 100%
  ✓ Auditability        8/8 decisions carry a trace lineage
  ✓ Security            tool clean-rate 100% over 5 tool calls
  ⚠ Drift               within baseline thresholds
  ▰▰▰▰▰▰▰▰▰▱  94 / 100
```

- **Evaluation** — deterministic governance metrics from recorded evidence
  (policy/oversight compliance, tool misuse, PII protection, task success,
  cost, latency). LLM judges and human review layer on top — never the only signal.
- **Replay** — re-evaluate a historical run against a candidate policy *before*
  deploying it: `14 actions / 2 approvals` → `11 allowed / 3 blocked / 3 approvals`.
- **Change management** — model / policy / prompt / tool changes move through
  `CANDIDATE → REPLAY → EVALUATION → REVIEW → APPROVED → DEPLOYED → (ROLLBACK)`.
  Nothing changes invisibly; every change carries evidence and a rollback target.
- **Drift** — deterministic, threshold-documented detection against a captured
  baseline (approval-rate drop, denial-rate rise, new tools/models, autonomy
  raise). `GOVERNANCE DRIFT DETECTED` — not fake anomaly detection.

---

## The single most important question: WHY?

> Why was this AI system allowed to do this? Why was this model allowed to
> receive this data? Why was this action blocked? Why was human approval
> required? Why is this system high risk? Why did governance status change?

Every one of those is answered from recorded evidence via `/why`,
`/v1/decisions/{id}/why`, `/systems/{id}/governance`, and `/systems/{id}/audit`.

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/satvikndxd/helios-ai-command-center/main/install.sh | bash
helios
```

No sudo, everything under `~/.helios`, SQLite by default, works over SSH,
under two minutes. Zero API keys required — the built-in `scripted`/`mock`
providers run the whole governed loop offline. Add a real provider and a
GitHub token when ready.

## Flagship: a governed release agent

The end-to-end demo (`tests/test_flagship_governance.py`) tells the whole story:

```
register release-agent (owner, purpose, L3, model, policy, data classes)
  → agent reads the repo, edits code, runs tests, branches, commits, opens a PR
  → agent requests a merge into the production branch `main`
  → HELIOS classifies it CRITICAL (write · protected branch)
  → policy requires human oversight; the run enters AWAITING APPROVAL
  → a human inspects action, risk, evidence, policy, and WHY
  → human approves; HELIOS executes the exact approved payload
  → every decision is recorded as evidence
  → replay the run against a candidate policy; see the diff before deploying
  → read the system governance dashboard + audit record
```

> HELIOS knows **what** this AI system is, **what** it's allowed to do, **what**
> it actually did, **why** it was allowed, **who** supervised it, and **whether**
> it remains within policy.

## Governed execution (the enforcement point)

The **Tool Broker** is the authoritative boundary — no model-proposed action
executes outside it. Every invocation flows:

```
manifest → argument validation → identity → permissions → data classification
→ contextual risk → policy → human oversight → idempotency → execution → evidence
```

Unknown tools are deny-by-default. Approvals bind to a SHA-256 of the exact
action + arguments (payload tampering invalidates them) and can carry expiry,
comments, and session scope. The persistent agent runtime — sessions, explicit
state machine (`thinking · tool_pending · awaiting_approval · blocked · …`),
cancellation, resume, replay — is now a *governed execution surface*, not the
product itself. Tools: filesystem · shell · git · GitHub · HTTP · MCP.

## Security controls (governance controls, not the product)

PII/secret detection, prompt-injection defense, tenant isolation, tool-result
sanitization, workspace/filesystem jails, shell restrictions, subprocess
timeouts. Each has tests. They are controls *within* HELIOS governance.

## API surface

```
Identity   /v1/systems  · /v1/systems/{id}/{events,lineage,oversight,governance,
                            evaluation,drift,baseline,audit}
Policy     /v1/models   · /v1/models/evaluate
Evidence   /v1/decisions · /v1/decisions/{id}/why · /v1/traces · /v1/ingest/otel
Assurance  /v1/changes  · /v1/agent/runs/{id}/replay
Execution  /v1/agent/* · /v1/tools · /v1/approvals/{id}/decide
```

## Model & gateway abstraction

Bring any model: OpenAI-compatible endpoints, Anthropic, Gemini, local
Ollama/vLLM, or custom gateway profiles. Credentials are referenced by env-var
name, never stored. Provider count is not a product goal; the abstraction is.

## Also in the box (supporting capabilities)

Off the critical path, kept working: governed completions with RAG grounding,
a completion-quality evaluation worker + review queue, governed web-research
adapters, encrypted browser sessions, domain workflow packs, and a human-gated
self-improvement proposal loop. Governance drives the architecture; these do not.

## Development

```bash
git clone https://github.com/satvikndxd/helios-ai-command-center && cd helios-ai-command-center
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=src .venv/bin/pytest tests -q          # 176 tests, ~6s, no network, no Postgres
PYTHONPATH=src .venv/bin/uvicorn helios.main:app  # SQLite by default
```

Architecture & roadmap: [docs/GOVERNANCE_ARCHITECTURE.md](docs/GOVERNANCE_ARCHITECTURE.md)
· [docs/GOVERNANCE_ROADMAP.md](docs/GOVERNANCE_ROADMAP.md).

> **Governance evidence, not legal certification.** HELIOS produces audit
> records and control status. It does not certify regulatory or legal compliance.

---

<div align="center">

**HELIOS does not merely observe AI. HELIOS governs AI.**

*Identity → what AI systems exist. Policy → what they may do.
Evidence → what they did. Assurance → proof they stay within policy.*

</div>
