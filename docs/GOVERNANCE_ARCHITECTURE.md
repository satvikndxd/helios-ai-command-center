# HELIOS V1.5 — AI Governance Control Plane: Architecture Map

> Product thesis: **"HELIOS defines what AI systems are allowed to do, records
> what they actually did, evaluates whether they behaved correctly, and provides
> continuous assurance that they remain within policy."**
>
> This document maps the **existing V1 codebase** (audited 2026-09-08, commit
> `55f0cec`, 138 tests passing) onto the four governance planes, states what is
> reused verbatim, what is extended, and what must be built. Nothing here is
> speculative: every "exists" claim points at a real module.

---

## 0. The four planes

```
                AI SYSTEM  (agent · application · workflow)
                     │
        ┌────────────▼─────────────────────────────────────┐
        │                    HELIOS                        │
        │                                                  │
        │  IDENTITY    who/what is this AI system?         │
        │  POLICY      what is it allowed to do?           │
        │  EVIDENCE    what did it actually do?            │
        │  ASSURANCE   does it remain within policy?       │
        └────────────┬─────────────────────────────────────┘
                     ▼
             MODELS / DATA / TOOLS
```

Every module in HELIOS belongs to exactly one plane (execution surfaces belong
to the plane that governs them). The invariant from V1 stands and extends:

* **V1 invariant:** no tool executes outside `ToolBroker.invoke`.
* **V1.5 invariant:** no AI system executes, no model is called, and no data
  flows outside a governance decision that is recorded as evidence, explainable,
  and replayable.

---

## 1. What exists today (audit)

### 1.1 Execution & enforcement (KEEP — becomes "governed execution surfaces")

| Module | What it is | V1.5 role |
|---|---|---|
| `broker/core.py` — `ToolBroker` | The single execution boundary: manifest → arg validation → permissions → contextual risk → versioned policy → payload-hash-bound approval → idempotent execution → sanitization → trace. | **Unchanged enforcement point.** Policy Plane decisions flow into it; Evidence Plane records flow out of it. Extended only to carry `system_id` and data-classification into context. |
| `broker/manifest.py` | Declarative tool contracts (`ToolManifest`): capability, scopes, risk class, approval mode, idempotency, provenance; JSON-schema-subset validation. | Unchanged. Tools are governed objects referenced by AI Systems. |
| `broker/permissions.py` | Grants = scope + resource constraints + environment/org/project/agent/user pins; deny-by-default. | Unchanged. Becomes the *identity-bound* reach of an AI system (grants attach to a system, then to its sessions). |
| `broker/risk.py` | Deterministic contextual risk: capability × environment × protected branch × dangerous args × autonomy × data classes → `{risk, score, reasons}`. | Unchanged scoring; extended to consume L0–L5 autonomy levels (legacy `supervised/autonomous` strings keep working via a deterministic mapping). |
| `broker/registry.py`, `tools/*` | filesystem, safe shell, git, GitHub (real REST executors), HTTP, MCP; unknown tools deny-by-default. | Unchanged. |
| `agent/runtime.py` — `AgentRuntime` | Persistent sessions, explicit run state machine (`thinking → tool_pending → running → awaiting_approval → blocked → completed/failed/cancelled`), resume after approval, cancel, retry. The only path from proposal to effect is the broker. | Unchanged loop; gains a **model-call governance preflight** (model approved? data classes allowed for this model? autonomy ceiling?) and records `data_access` evidence. |
| `agent/planner.py`, `providers/scripted.py` | Prompt/proposal protocol; deterministic scripted provider powering demos and E2E tests with zero API keys. | Unchanged — the flagship demo uses it. |
| `tui/*`, `cli.py` | stdlib TUI (governed/direct modes), agent pane, approval UX, installer/launcher. | Extended with governance views (§7). |

### 1.2 Plane-by-plane status of V1

#### IDENTITY PLANE — *partially exists; the registry is missing*

Exists:
* `Tenant` / `Application` / `ApiKey` (`models.py`) — organizational identity + multi-tenant isolation, enforced in every route via `get_api_key`.
* `InvocationContext` (`broker/types.py`) — per-call actor identity: tenant, environment, org, project, `agent_id`, `user_id`, session, run, autonomy, data classes.
* `AgentSession` (`models.py`) — session-bound agent identity with grants, model, environment, autonomy, policy version.

Missing (BUILD):
* **`AiSystem`** — the first-class AI system identity: `system_id`, name, owner, organization, purpose, environment, lifecycle state, autonomy level (L0–L5), risk class, models, tools, data classes, policies, oversight requirements, deployment/version.
* **`AiSystemVersion`** — versioned snapshots so "what was this system at time T?" is answerable and changes are auditable.
* Linkage: `AgentSession.system_id`, `TraceEvent.system_id` (nullable columns — existing rows unaffected).

#### POLICY PLANE — *strong at tool level; system-level governance is missing*

Exists:
* `broker/policy.py` — `ToolPolicy`: ordered rules, first-match-wins, **default deny**, JSON round-trippable (replayable), every decision carries `rule_id` + explanation trace. Outcomes: ALLOW / DENY / REQUIRE_APPROVAL. Match dimensions: tool glob, capability, min/max risk, environment, autonomy, manifest approval mode.
* `policy.py` — completions preflight/output policy (PII-in-high-risk, injection, provider restrictions) with `PolicyViolation` records on `DecisionTrace.policy_result`.
* `web/policy.py` — web access allowlist policy.
* `sentinel.py` — PII/injection/leak detection feeding policy inputs.

Missing (BUILD):
* **Autonomy levels L0–L5** as a first-class primitive (`governance/autonomy.py`), with a deterministic legacy mapping (`supervised→L2`, `autonomous→L4`) so existing sessions/policies/tests keep working.
* **Governance policies** (`governance/policy.py`): persisted, versioned rule sets over *system/model/data/autonomy/environment/provider* subjects with two new outcomes — `REQUIRE_HUMAN_REVIEW` and `BLOCK_DEPLOYMENT` — evaluated at registration/deployment gates and at model-call time. Same design DNA as `ToolPolicy`: ordered rules, first-match-wins, default-deny, serializable, explained.
* **Model governance**: model approval status, allowed data classes, allowed environments, provider restrictions — enforced before any model call and on system registration.

#### EVIDENCE PLANE — *strong; needs system-binding, data evidence, and decision records*

Exists:
* `broker/trace.py` — `TraceRecorder` + `TraceEvent`: hierarchical, ordered (`seq`), secret-scrubbed before persistence (`scrub_payload`). Event types today: `model_call`, `tool_proposal`, `permission_evaluation`, `risk_evaluation`, `policy_evaluation`, `approval`, `tool_execution`, `state_change`, `outcome`, `external_span`.
* `DecisionTrace` (`models.py`) — completion-level evidence: input/request/response payloads, citations (RAG provenance), tool calls, cost, latency, `policy_result`, `evaluation_scores`, feedback.
* `ApprovalRequest` — payload-hash-bound (`sha256(tool + canonical args)`); mutation invalidates approval. `ActionEffect` — idempotency journal linking executions to approvals.
* `routes/ingest.py` — OTLP/JSON ingestion → `external_span` events in the same store (governing agents HELIOS does not run).
* `web/sanitize.py` + `sentinel.py` — secrets scrubbed, injection quarantined on every tool result and every persisted payload.

Missing (BUILD):
* New event types: `retrieval`, `data_access`, `human_review` (hierarchy becomes: AI_SYSTEM → RUN → {MODEL_CALL, RETRIEVAL, DATA_ACCESS, TOOL_PROPOSAL → …, APPROVAL, TOOL_EXECUTION, HUMAN_REVIEW, OUTCOME}).
* `system_id` on evidence rows.
* **`DecisionRecord`** — the normalized, versioned AI Decision Record (system, decision, model+version, evidence refs, risk, policy, oversight required vs. actual, outcome, reviewer, trace id) *derived from real TraceEvents* — never hand-written.
* **Data lineage** derived on demand from recorded events (`governance/lineage.py`): every edge (source → retrieval → model → tool → output → destination) must cite the `TraceEvent.id` that produced it. No stored lineage table that could drift from events; no decorative graphs.

#### ASSURANCE PLANE — *replay + evaluators exist; score/changes/drift are missing*

Exists:
* `agent/replay.py` — deterministic replay: re-runs the pure decision pipeline (`ToolBroker.evaluate`) for every recorded proposal against the same/newer/**candidate policy document**; diffs original vs. candidate outcomes. Nothing executes during replay.
* `evaluators/` — `BaseEvaluator`/`EvalResult`/`EvaluationPipeline` (fault-isolated), deterministic heuristics (empty output, latency SLA, injection, PII leak) + groundedness (citation-backed) — LLM-as-judge slots behind the same interface.
* `worker.py` + `EvaluationJob` — async evaluation of completion traces.
* `Dataset`/`DatasetItem` (benchmark/regression data), `ReviewItem` (human review queue), `SimulationRun`.
* `evolution.py` + `EvolutionProposal` — an existing propose→approve→apply→rollback lifecycle (proto change-management; stays for the self-evolution loop).

Missing (BUILD):
* **Run/system-level evaluators** over `TraceEvent` evidence: policy compliance, human-oversight compliance, tool misuse, approval latency, PII leakage, plus reuse of groundedness for RAG.
* **`EvaluationRun`** — evaluations bound to an AI system (not just a completion trace).
* **Governance Score** (`governance/score.py`) — composite, transparent: every component (identity, model approval, data policy, oversight, policy compliance, evaluation, auditability, security, drift, documentation) returns `{score, weight, checks: [{check, passed, reason, evidence}]}`. No vanity numbers: any component without evidence is reported as `insufficient_evidence`, not 100.
* **`ChangeRecord`** — AI Change Management: model / model_version / prompt / policy / tool / dataset / system_instruction / workflow / agent_config, with lifecycle `candidate → replay → evaluation → risk_comparison → human_review → approved → deployed`, previous/candidate snapshots, evidence links, rollback target.
* **Drift detection** (`governance/drift.py`) — deterministic baselines (`DriftBaseline`: approval rate, deny rate, risk mix, tool mix, model mix, oversight-bypass count over a window) + documented threshold rules, minimum sample sizes, and severity. No fake anomaly detection; the detector interface allows richer detectors later.
* **Audit report** (`governance/audit.py`) — per-system governance evidence report (identity, owner, purpose, models, tools, data classes, autonomy, policies, evaluations, violations, approvals, oversight, changes, drift, score) exportable as JSON. Language: *governance evidence / policy status / audit record / control status* — never legal/regulatory certification claims.

---

## 2. Target module map (V1.5)

```
src/helios/
├── governance/                  # NEW — the V1.5 product core
│   ├── autonomy.py              # POLICY     L0–L5 vocabulary + legacy mapping
│   ├── systems.py               # IDENTITY   AiSystem registry service
│   ├── models_registry.py       # IDENTITY/POLICY  ModelAsset registry + model governance checks
│   ├── policy.py                # POLICY     GovernancePolicySet: system/model/data/autonomy rules
│   ├── engine.py                # POLICY     GovernanceEngine facade (identity→model→data→autonomy→tool)
│   ├── data.py                  # POLICY     data-class vocabulary + classifier (reuses sentinel)
│   ├── lineage.py               # EVIDENCE   event-backed data lineage derivation
│   ├── decisions.py             # EVIDENCE   DecisionRecord builder (from TraceEvents only)
│   ├── oversight.py             # EVIDENCE/POLICY  required-vs-actual human involvement
│   ├── evaluators.py            # ASSURANCE  run/system evaluators over evidence
│   ├── score.py                 # ASSURANCE  transparent governance score
│   ├── changes.py               # ASSURANCE  ChangeRecord lifecycle + deploy/rollback
│   ├── drift.py                 # ASSURANCE  deterministic baselines + drift rules
│   ├── audit.py                 # ASSURANCE  per-system governance report
│   └── demo.py                  # flagship end-to-end release-agent demo
├── broker/                      # KEEP — tool-level enforcement (Policy Plane → ToolBroker → tool)
├── agent/                       # KEEP — governed execution runtime (+ model preflight hook)
├── routes/
│   ├── systems.py  models.py  policies.py  decisions.py
│   ├── evaluations.py  changes.py  drift.py  audit.py  replay.py   # NEW /v1 boundaries
│   └── agents.py actions.py traces.py ingest.py …                  # KEEP (additive changes only)
├── models.py                    # EXTEND — AiSystem, AiSystemVersion, ModelAsset,
│                                #          GovernancePolicySet, DecisionRecord,
│                                #          EvaluationRun, ChangeRecord, DriftBaseline
│                                #          + nullable system_id on AgentSession/TraceEvent
│                                #          + oversight columns on ApprovalRequest
└── tui/                         # EXTEND — /systems /models /policies /evaluations
                                 #          /approvals /changes /drift /audit /replay
```

Decision flow (hot path):

```
AI SYSTEM (registered identity, autonomy Lx, data classes, models)
   │ session bound to system
   ▼
AgentRuntime ──model call──► GovernanceEngine.preflight_model_call
   │                          (model approved? data allowed? autonomy ceiling?)
   │                          → policy_evaluation evidence (ALLOW/DENY/REQUIRE_HUMAN_REVIEW)
   ▼
tool proposal ──► ToolBroker.invoke
                   manifest → args → identity/permissions → data classification
                   → contextual risk → ToolPolicy (+ governance overlays)
                   → human oversight (payload-hash-bound) → idempotent execution
                   → sanitized result → TraceEvents (+ DecisionRecord)
```

---

## 3. Data model additions (all additive; see ROADMAP §migrations)

| Table | Plane | Notes |
|---|---|---|
| `ai_systems` | IDENTITY | unique `(tenant_id, system_id)`; owner, purpose, environment, `autonomy_level` int, `risk_class`, lifecycle (`draft/active/suspended/archived/blocked`), JSON: models, tools, data_classes, policies, oversight requirements, deployment, version int. |
| `ai_system_versions` | IDENTITY | immutable snapshot on every mutation; supports "system at time T", change diffs, rollback target. |
| `model_assets` | IDENTITY/POLICY | provider, model_id, version, capabilities, region, pricing metadata, risk_class, `approval_status` (`approved/pending/not_approved/deprecated`), allowed_data_classes, allowed_environments, policy restrictions, latest evaluation summary. Unknown model ⇒ `not_approved` ⇒ deny. |
| `governance_policy_sets` | POLICY | name+version, rules JSON, `status` (`active/candidate/archived`), scope filters (system/env/model), author; activation is itself a governed change. |
| `decision_records` | EVIDENCE | normalized AI Decision Record, `schema_version`, derived-from `trace_event_ids`, unique per (run, action) to stay idempotent. |
| `evaluation_runs` | ASSURANCE | system-scoped evaluation: kind (`governance/benchmark/regression`), per-metric results JSON, dataset ref, pass/fail, evidence refs. |
| `change_records` | ASSURANCE | change_type, system_id, author, previous/candidate JSON snapshots, state machine, evidence (replay/eval reports), approval_id, deployed_at, rollback_target. |
| `drift_baselines` | ASSURANCE | system-scoped deterministic metric snapshot + window; comparison rules live in code (documented thresholds), not in opaque config. |
| `agent_sessions.system_id`, `trace_events.system_id` | IDENTITY/EVIDENCE | nullable FK — legacy rows keep working. |
| `approval_requests` + `expires_at`, `scope`, `comments`, `delegated_to`, `decision_kind`, `system_id` | EVIDENCE/POLICY | human-oversight expansion; nullable, default-safe. |

---

## 4. Non-negotiable invariants (V1.5)

1. No tool executes outside `ToolBroker.invoke`; no model call executes outside a
   recorded governance preflight decision.
2. Approvals bind to `sha256(tool + canonical args)`; payload mutation
   invalidates approval (V1 invariant, unchanged).
3. Every policy/governance decision is deterministic, serialized into evidence,
   and carries an explanation generated **from actual policy/evidence state**
   (never hard-coded UI text).
4. Every lineage edge cites a real `TraceEvent`; every score component cites its
   checks and evidence; missing evidence is reported as missing — never as 100.
5. Secrets are never persisted (scrub on every write path, V1 invariant).
6. Unknown tool / unknown model / unregistered system in a governed context ⇒
   deny-by-default.
7. Legacy V1 behavior is preserved: `supervised|autonomous` sessions, existing
   routes and payloads, in-memory `ToolPolicy` registry, and all 138 V1 tests
   keep passing at every phase boundary.
8. No legal/regulatory compliance claims — governance evidence language only.
