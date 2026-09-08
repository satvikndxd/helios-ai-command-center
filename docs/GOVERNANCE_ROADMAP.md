# HELIOS V1.5 — Governance Roadmap & Implementation Plan

> Companion to `GOVERNANCE_ARCHITECTURE.md`. This is the incremental delivery
> plan: phase order, deliverables, migrations, compatibility risks, and the
> verification gate after every phase. Rule of the road: **no phase merges
> unless the full suite (138 V1 tests + new phase tests) is green.**
>
> **STATUS (2026-09-08): Phases 1–13 DELIVERED. Full suite 276+ tests green.
> Per-phase completion notes are appended at the bottom of this document.**

---

## Delivery principles

1. **Converge, don't rewrite.** Every phase reuses a V1 primitive (broker,
   policy DNA, TraceRecorder, replay, evaluators, approvals) and reorganizes it
   under a plane.
2. **Additive schema.** New tables + nullable columns only. Existing rows,
   routes, payloads, and the in-memory `ToolPolicy` registry keep working.
3. **Real evidence only.** Scores, lineage, drift, and explanations must cite
   stored events/records. Anything without backing data renders as
   `insufficient_evidence`.
4. **Test each plane aggressively** (see §Testing matrix) before moving on.
5. Local development stays zero-config: SQLite + mock/scripted providers; the
   flagship demo runs with no API keys.

---

## Phase plan

### PHASE 1 — Architecture audit ✅ (this document)
Deliverables: `docs/GOVERNANCE_ARCHITECTURE.md`, `docs/GOVERNANCE_ROADMAP.md`.
Baseline verified: `138 passed in ~5.4s` on commit `55f0cec`.

### PHASE 2 — AI System Registry (IDENTITY)
* Models: `AiSystem`, `AiSystemVersion` (immutable snapshot per mutation).
* Service `governance/systems.py`: create / update / archive / suspend /
  inspect / search / version history / owner assignment / risk classification /
  associate models·tools·policies; unique `(tenant, system_id)`.
* API `routes/systems.py`: `POST/GET /v1/systems`, `GET/PATCH /v1/systems/{id}`,
  `POST /v1/systems/{id}/archive`, `GET /v1/systems/{id}/versions`.
* Linkage: `AgentSession.system_id` (nullable) — a session may bind to a
  registered system; `TraceEvent.system_id` stamped from the session.
* Tests: registration, ownership, lifecycle, versioning, search, risk class,
  tenant isolation, duplicate system_id.

### PHASE 3 — Model Registry + model governance (IDENTITY/POLICY)
* Model: `ModelAsset` (provider, model_id, version, capabilities, region,
  pricing metadata, risk_class, approval_status, allowed_data_classes,
  allowed_environments, restrictions, evaluation summary).
* Service `governance/models_registry.py`: register/update/approve/deprecate;
  **unknown model ⇒ not_approved**; compatibility check
  `model × data_classes × environment ⇒ ALLOW / DENY(reason)`.
* API `routes/models.py`: `/v1/models` CRUD + `POST /v1/models/{id}/approve`.
* Runtime hook: `AgentRuntime._model_call` gains a recorded governance
  preflight (`policy_evaluation` event with `plane: model`); denied model call
  fails the run with an explained outcome, never silently.
* Model changes create governance events (feed Phase 9 change records).
* Tests: approved/unapproved model, provider restriction, data/model
  incompatibility (e.g. CONFIDENTIAL + unapproved provider ⇒ DENY),
  environment restriction, preflight evidence recorded, legacy sessions with no
  registered model keep working (mock/scripted exempt path documented).

### PHASE 4 — Governance policy engine + autonomy levels (POLICY)
* `governance/autonomy.py`: L0–L5 canonical levels, labels, legacy mapping
  (`supervised→L2`, `autonomous→L4`), helper `level_from_context`.
* `governance/policy.py`: `GovernancePolicySet` — ordered when/require/deny
  rules over subjects {system, model, data, autonomy, environment, provider,
  action}; outcomes `ALLOW / DENY / REQUIRE_APPROVAL / REQUIRE_HUMAN_REVIEW /
  BLOCK_DEPLOYMENT`; versioned, serializable, first-match-wins, default-deny,
  every decision explained with rule id + matched conditions (same DNA as
  `broker/policy.py`).
* Persistence: `GovernancePolicySet` table (`active/candidate/archived`);
  candidate sets are replayable before activation (Phase 9).
* Enforcement points: (a) system registration/deployment gate — a policy can
  `BLOCK_DEPLOYMENT` (system lifecycle → `blocked`, with reason); (b) model-call
  preflight (Phase 3 hook) consumes governance policies; (c) broker decisions can
  be escalated by governance overlays (`REQUIRE_HUMAN_REVIEW`).
* `broker/types.py`: `InvocationContext.autonomy_level: int` (derived, legacy
  string preserved); `broker/risk.py` + `broker/policy.py` may match on level.
* API: extend `GET /v1/policies` (keep V1 shape for tool policies; add
  governance sets under `governance_policies`), `POST /v1/policies` (create
  version), `POST /v1/policies/{name}/activate`, `POST /v1/policies/dry-run`.
* Tests: allow/deny/require_approval/require_human_review/block_deployment,
  autonomy ceiling per environment (production: L0–L2 allow, L3 approval, L4/L5
  blocked), policy versioning + activation, default deny, explanation content,
  determinism (same input ⇒ same decision), V1 `ToolPolicy` tests untouched.

### PHASE 5 — Data classification + lineage (POLICY/EVIDENCE)
* `governance/data.py`: canonical classes `PUBLIC / INTERNAL / CONFIDENTIAL /
  SENSITIVE / PII` (ordered); classifier = declared classes (system/session)
  ∪ content detection reusing `sentinel.detect_pii` (PII ⇒ at least PII class);
  effective class = max severity. Never stores raw sensitive content — classes
  and counts only.
* Evidence: new `TraceEvent` types `data_access` (source, class, refs) and
  `retrieval` (RAG/knowledge queries record document/chunk ids + classes);
  broker stamps `context.data_classes` from classification at invoke time.
* `governance/lineage.py`: derives the flow graph
  `SOURCE → RETRIEVAL → MODEL → TOOL → OUTPUT → DESTINATION` **exclusively**
  from recorded TraceEvents of a run/system; every edge carries `event_id`.
  Edges checked against policy (which model/tool may receive which class) —
  violations are evidence, not speculation.
* API: `GET /v1/systems/{id}/lineage`, `GET /v1/agent/runs/{id}/lineage`.
* Tests: classification (declared + detected + max-severity), allowed flow,
  blocked flow (CONFIDENTIAL → unapproved model ⇒ denied at preflight),
  lineage completeness (every edge cites an event), secret protection
  (classifier never persists secret material).

### PHASE 6 — AI Decision Records + evidence normalization (EVIDENCE)
* Model: `DecisionRecord` (`schema_version`, system, decision/action, model +
  version, evidence refs [trace_event ids, policy versions, data refs, approval
  id], risk, policy, oversight_required vs oversight_actual, outcome, reviewer,
  run/trace id, tenant). Unique `(tenant, run_id, args_hash/action)` — builders
  are idempotent.
* `governance/decisions.py`: builder **derives records only from stored
  TraceEvents** (tool executions, denials, approvals, model outcomes); called at
  run terminal states and at approval decisions. No record without evidence.
* API: `GET /v1/decisions` (filters: system, outcome, risk, window),
  `GET /v1/decisions/{id}` (with explanation assembly — the "WHY" endpoint:
  `GET /v1/decisions/{id}/explanation` reconstructs allow/block reasoning from
  the recorded permission/risk/policy/approval events).
* Tests: record completeness (every executed/denied/approved action in a run
  yields exactly one record), evidence provenance (all refs resolve to real
  events), explanation correctness for allow & block paths, idempotency,
  secret non-persistence.

### PHASE 7 — Human oversight expansion (POLICY/EVIDENCE)
* `ApprovalRequest` extensions (nullable): `expires_at` (expired approvals
  invalid — broker checks), `scope` (bind approval to system/env/tool/args-hash
  window), `comments` (audit thread), `delegated_to`, `decision_kind`
  (`approval | denial | review | escalation`), `system_id`.
* `governance/oversight.py`: oversight requirements from system + policy
  (`REQUIRE_HUMAN_REVIEW`); compliance analysis over evidence:
  *Where was a human involved? Where SHOULD one have been? Did the AI bypass
  required oversight?* → `oversight` events (`human_review`) + violation flags
  feeding score/drift.
* Session approvals (`approve_session`) gain expiration + scope binding;
  payload tampering remains fatal (V1 hash binding unchanged).
* API: extend `/v1/agent/approvals/{id}/decide` (comment, delegation);
  `GET /v1/approvals` unified queue (already exists in actions.py — extend with
  system filter); `GET /v1/systems/{id}/oversight`.
* Tests: approval, denial, expiration (pending → expired → broker refuses),
  scope binding (approval for env A not valid in env B), delegation, comments in
  audit trail, payload tampering invalidates, oversight-compliance detection
  (required review missing ⇒ violation recorded).

### PHASE 8 — Evaluation + governance score (ASSURANCE)
* `governance/evaluators.py`: evidence-based evaluators over runs/systems —
  `policy_compliance`, `human_oversight_compliance`, `tool_misuse`
  (denied-attempt rate, permission-violation attempts), `pii_leakage` (reuse
  sentinel on recorded outputs), `data_flow_violations`, `task_success`
  (run outcomes), `cost`, `latency`; deterministic first, LLM-judge behind the
  existing `BaseEvaluator` interface (never the only signal).
* Model: `EvaluationRun` (system-scoped; kind `governance | benchmark |
  regression`; per-metric results + evidence refs + pass/fail); datasets reuse
  `Dataset`/`DatasetItem`.
* `governance/score.py`: transparent composite —
  components {identity, model_approval, data_policy, human_oversight,
  policy_compliance, evaluation, auditability, security, drift, documentation};
  each returns checks with pass/fail + reason + evidence ref; weighted total;
  `insufficient_evidence` states surfaced, not hidden; weights + rules
  documented in code and in the API response itself.
* API: `POST /v1/systems/{id}/evaluate`, `GET /v1/evaluations`,
  `GET /v1/systems/{id}/score` (full breakdown).
* Tests: each evaluator against seeded evidence, score explainability (every
  component has checks; failing component lowers score with cited reason),
  regression suite over `Dataset`, no-evidence ⇒ insufficient, not 100.

### PHASE 9 — Replay expansion + AI Change Management (ASSURANCE)
* `governance/changes.py` + `ChangeRecord`: types {model, model_version,
  prompt, policy, tool, dataset, system_instruction, workflow, agent_config};
  lifecycle `current → candidate → replay → evaluation → risk_comparison →
  human_review → approved → deployed` (+ `rejected`, `rolled_back`); every
  record: author, timestamps, previous/candidate snapshots, evidence (replay +
  evaluation reports), approval link, deployment state, rollback target.
* Deploy gate: transitioning to `deployed` applies the candidate (activate
  policy set, swap system model/prompt/config) **only** from `approved`;
  rollback re-applies `previous` snapshot and opens a new `rollback` record.
  No invisible production governance changes.
* Replay expansion: existing `replay_run` (single run vs candidate ToolPolicy)
  stays; add `governance/replay.py` — **system-level replay**: all runs of a
  system re-evaluated against candidate policy (tool + governance), aggregated
  comparison: allowed/denied/approvals, violations, data-flow violations,
  evaluation deltas, cost/latency summary. Risk comparison report feeds the
  change record.
* API: `POST /v1/changes`, `GET /v1/changes`, `POST /v1/changes/{id}/replay`,
  `POST /v1/changes/{id}/evaluate`, `POST /v1/changes/{id}/approve`,
  `POST /v1/changes/{id}/deploy`, `POST /v1/changes/{id}/rollback`,
  `POST /v1/replay/systems/{id}`.
* Tests: full lifecycle happy path, deploy blocked without approval, rollback
  restores previous snapshot + audit trail, candidate policy replay comparison
  (counts + changed decisions), change record completeness (author/versions/
  evidence/approval), policy activation is itself a change record.

### PHASE 10 — Governance drift (ASSURANCE)
* Model: `DriftBaseline` — deterministic metric snapshot per system over a
  window: approval_rate, deny_rate, risk_mix, tool_mix, model_mix,
  oversight_bypass_count, violation_count, avg_steps/cost/latency, eval pass
  rate. Baseline creation is explicit (API/TUI) and stored.
* `governance/drift.py`: comparison rules in code with **documented
  thresholds + minimum sample sizes** (e.g. approval-rate drop > 20pp with n ≥
  20 decisions ⇒ `governance_drift`; new unapproved model in mix ⇒
  `model_drift`; tool-mix shift > 30pp ⇒ `tool_drift`; eval pass-rate regression
  > 10pp ⇒ `evaluation_regression`; oversight bypass > 0 ⇒ `oversight_drift`,
  zero-tolerance). False-positive safeguards: min samples, severity tiers,
  per-signal cooldown. Detector interface allows advanced detectors later —
  none shipped until earned.
* API: `POST /v1/systems/{id}/drift/baseline`, `POST /v1/systems/{id}/drift/check`,
  `GET /v1/drift` (open signals).
* Tests: baseline creation, each drift rule at/under/over threshold, min-sample
  safeguard (small n ⇒ no signal), false-positive guard (identical window ⇒
  clean), signal severity + explanation content.

### PHASE 11 — Governance dashboard + TUI views
* TUI evolves from agent interface into governance control center. New views:
  `/systems`, `/system <id>` (overview panel: owner, risk, autonomy Lx, model +
  approval ✓, data classes, active policies, evaluation %, governance score,
  drift ⚠, recent violations, active approvals — **every field backed by API
  data**), `/models`, `/policies`, `/traces`, `/evaluations`, `/approvals`,
  `/changes`, `/drift`, `/audit <system>`, `/replay`. Existing agent commands
  (`/sessions`, `/trace`, `/replay`, `/approve`, `/deny`) unchanged.
* Web/API boundaries from phases 2–10 are the dashboard's only data source —
  no private TUI state.
* Tests: API-backed view renderers (pure functions over API payloads) —
  snapshot-style assertions on the overview panel.

### PHASE 12 — Flagship end-to-end demo
* `governance/demo.py` + `helios demo --governance` (and `make governance-demo`):
  1. Register AI system `release-agent` (owner, purpose, env=production,
     autonomy L3, risk HIGH, model-x, GitHub tools, policy set).
  2. Register + approve `model-x`; bind policy `production-autonomy`
     (L3 in production ⇒ approval).
  3. Agent (scripted provider — deterministic, zero keys) investigates repo,
     reads code, modifies code, runs tests, creates PR — each step brokered,
     evidenced, explained.
  4. Agent attempts **production merge** → risk CRITICAL → policy requires human
     oversight → approval request with evidence bundle.
  5. Human inspects (action, risk, evidence, policy, proposed changes) and
     approves → HELIOS executes exactly the approved payload → DecisionRecord
     stored → full trace queryable.
  6. Replay the run against a **candidate policy** → side-by-side comparison
     (actions/approvals/violations deltas).
  7. Print the system governance dashboard + audit report (score with
     component breakdown, drift check, oversight record).
* Mirrored headless in `tests/test_flagship_demo.py` so CI proves the story:
  "HELIOS knows WHAT this system is, WHAT it may do, WHAT it did, WHY it was
  allowed, WHO supervised it, and WHETHER it remains within policy."

### PHASE 13 — README, docs, tests, hardening
* README rewritten around **HELIOS — The AI Governance Control Plane**: thesis
  statement first, four-plane diagram second, quick start, system overview,
  governance concepts; gateway counts/integrations/infra move down. No legal
  compliance claims — governance-evidence language throughout.
* `docs/GOVERNANCE_GUIDE.md` (operator guide: register → policy → run → audit),
  ADR-style notes per phase appended to this roadmap.
* Security test sweep: prompt injection through tool results, privilege
  escalation via grants, cross-resource access, cross-tenant isolation on every
  new route, secret leakage on every new persistence path.
* Full-suite gate + demo dry run on a clean checkout (SQLite, no keys).

---

## Migrations

The repo uses `create_all` (no Alembic — documented V1 stance). V1.5 keeps that
for fresh installs and adds **`helios/migrate.py`**: idempotent
`ALTER TABLE … ADD COLUMN` for the nullable additions (works on SQLite +
Postgres), invoked at startup after `create_all` and runnable manually
(`python -m helios.migrate`). New tables arrive via `create_all` automatically.

| Migration | Risk | Mitigation |
|---|---|---|
| New tables (`ai_systems`, `model_assets`, …) | None (additive) | `create_all`. |
| `agent_sessions.system_id`, `trace_events.system_id` | Low | Nullable; legacy rows = ungoverned-by-system, still fully traced. |
| `approval_requests` oversight columns | Low | Nullable/default-safe; hash binding untouched. |
| `InvocationContext.autonomy_level` | Low | Derived from legacy string when absent; serialized into traces (replay of old traces maps `supervised/autonomous` deterministically). |

## Compatibility risks (tracked)

1. **`GET /v1/policies` shape** — V1 returns registered ToolPolicy versions;
   V1.5 must not break it. Plan: keep the existing response keys, add
   `governance_policies` alongside. (No V1 test asserts exact top-level shape —
   verified — but the TUI `/replay` policy listing consumes it; keep `policies`
   key intact.)
2. **Replay of pre-V1.5 traces** — old `tool_proposal` payloads lack
   `system_id`/`autonomy_level`; replay must treat missing fields via the legacy
   mapping and never crash (`InvocationContext.from_dict` defaults).
3. **Risk vocabulary** — canonical `low|medium|high|critical` everywhere new;
   legacy `DecisionTrace.risk_level` ("informational" default) untouched on the
   completions path.
4. **Sessions without a registered system** — remain fully functional (V1
   behavior); governance views mark them `unregistered` and the score's identity
   component fails them honestly. Governed deployments should bind systems.
5. **Default-deny scope creep** — governance preflight denies only when a
   registered system/model/policy says so, or when a model is explicitly
   `not_approved` *and* the system is registered. Mock/scripted providers on
   unregistered sessions keep working so local dev stays zero-config.

## Testing matrix (minimum bar per phase)

| Area | Tests |
|---|---|
| IDENTITY | registration, ownership, lifecycle, versioning, risk class, tenant isolation |
| MODEL | approved/unapproved, provider restriction, data×model incompatibility, environment restriction |
| POLICY | allow/deny/require_approval/require_human_review/block_deployment, versioning, default deny, explanation, determinism |
| DATA | classification, allowed/blocked flow, lineage completeness (edge ⇒ event), secret protection |
| OVERSIGHT | approval, denial, expiration, scope binding, delegation, payload tampering, bypass detection |
| ASSURANCE | evaluators, score explainability, replay comparison, change lifecycle, deploy gate, rollback |
| DRIFT | baseline, threshold rules at boundaries, min-sample safeguard, false-positive guard |
| AUDIT | trace completeness, decision-record completeness, evidence provenance, report export |
| SECURITY | injection, privilege escalation, cross-resource, cross-tenant, secret leakage |
| E2E | flagship demo headless: register → run → critical approval → execute → record → replay → score → report |

## Definition of Done (V1.5)

An organization can: register an AI system (owner/purpose/risk/autonomy);
register its models and tools; define data classes; define versioned governance
policies; execute the system under governance; observe every meaningful
decision as evidence; see what data was used and by which model; evaluate
behavior; require and verify human oversight; audit decisions with generated
"WHY" explanations; replay history against candidate policies/models; test
changes before deployment and roll them back; detect governance drift against
stored baselines; and export an auditable governance report — with the full V1
toolset (broker, agent runtime, TUI, OTel ingestion, MCP, GitHub) intact and
all pre-existing tests green.

---

## Delivery record (appended per phase)

* **Phase 1 ✅** Audit + these two documents. Baseline: 138 tests green on
  `55f0cec`.
* **Phase 2 ✅** AI System Registry (`AiSystem`/`AiSystemVersion`,
  `governance/systems.py`, `/v1/systems`, autonomy + data-class vocabularies,
  `helios/migrate.py`, session/evidence `system_id` binding). 159 green.
* **Phase 3 ✅** Model Registry (`ModelAsset`, `governance/models_registry.py`,
  `/v1/models` incl. the `/check` WHY endpoint, runtime model-call preflight
  with conversation classification — counts only, never raw content; governed
  systems deny unknown models, legacy sessions flagged-not-broken;
  `model_governance` evidence events). 173 green.
  *Deviation noted:* preflight classification scans the CONVERSATION, not the
  harness's own prompt scaffolding (tool manifests contain words like
  "token"/"secret" — classifying them produced false positives).
* **Phase 4 ✅** Governance policy engine (`governance/policy.py` +
  `engine.py`, `GovernancePolicySet` table, five outcomes, built-in production
  autonomy ceiling, persisted candidate→active→archived sets, dry-run),
  enforcement at: deployment gate (register/activate/patch, payload-hash-bound
  deploy approvals), model preflight (deny/review with posture-bound
  approvals), broker tool overlay (escalation-only, `governance_evaluation`
  events), lifecycle gate (blocked/suspended/archived never execute).
  `GET /v1/policies` kept V1 shape + `governance_policies`. 203 green.
* **Phase 5 ✅** Data classification + lineage: broker records inbound
  `data_access`/`retrieval` (network tools) and outbound `data_access`
  evidence with detected classes; `governance/lineage.py` derives
  SOURCE→MODEL→TOOL→DESTINATION graphs exclusively from events (every edge
  cites event ids); detected content drives enforcement end-to-end (PII in a
  tool result can deny the next model call; PII in write args escalates risk
  to approval). V1 trace-chain test updated for the new (documented) event
  type. 211 green.
* **Phase 6 ✅** AI Decision Records (`DecisionRecord`,
  `governance/decisions.py`, `/v1/decisions` + `/explanation` + `/rebuild`):
  idempotent upserts keyed by (tenant, run, source_event); gated records
  converge with the human's actual decision; explanations generated from
  recorded state. 219 green.
* **Phase 7 ✅** Human oversight expansion (`governance/oversight.py`):
  expiry sweep, session-approval scope binding (environment/system/max_uses/
  expiry), comments, delegation (only the delegate decides), escalation with
  mandatory reason, `human_review` trace events, compliance analysis
  (involved/required/bypassed/declared-requirement gaps). **Latent V1 safety
  bug fixed:** resume after re-gating no longer continues the loop. 228 green.
* **Phase 8 ✅** Evaluation + governance score (`EvaluationRun`,
  `governance/evaluators.py` — 8 deterministic metrics with documented
  thresholds and insufficient-evidence honesty; `governance/judge.py` opt-in
  LLM judge with strict fail-closed contract; `governance/score.py` — 10
  weighted components, checks-with-reasons, formula in the payload;
  `/v1/systems/{id}/evaluate`, `/v1/evaluations`, `/v1/systems/{id}/score`).
  238 green.
* **Phase 9 ✅** Replay + AI Change Management (`governance/replay.py`
  system-level candidate replay across tool policy / governance sets /
  posture / model registry state; `ChangeRecord` + `governance/changes.py`
  full lifecycle with digest-bound review approvals, deploy via the same
  services manual paths use, rollback-as-governed-change; `/v1/changes`,
  `/v1/replay/systems/{id}`). 248 green.
* **Phase 10 ✅** Drift (`DriftBaseline`/`DriftSignal`,
  `governance/drift.py` — documented thresholds, min-sample safeguards,
  severity tiers, zero-tolerance bypass detection, upsert signals, ack-is-
  not-mute; drift endpoints; score component live). 259 green.
* **Phase 11 ✅** Dashboard/TUI (`governance/audit.py` + `/v1/audit/{id}` +
  `/v1/systems/{id}/audit`; `tui/governance.py` pure renderers +
  GovernancePane; `/systems /system /models /policies /traces /evaluations
  /approvals /changes /drift /audit /replay system`). 267 green.
* **Phase 12 ✅** Flagship demo (`governance/demo.py`, `make
  governance-demo`, `helios governance-demo`, headless mirror in
  `tests/test_flagship_demo.py` on a dedicated tenant). Lineage semantics
  refined: gates ≠ violations; satisfied gates render as "REQUIRED and
  OBTAINED" in WHY explanations. 269 green.
* **Phase 13 ✅** Security sweep (`tests/test_governance_security.py`:
  secret non-persistence across every governance surface, injection
  flagged-not-obeyed + external quarantine, cross-resource/privilege/
  cross-tenant/ deny-by-default sweeps), README rewrite, this guide,
  metadata polish. 276 green.

### Known limitations (documented, deliberate)

* LLM-judge is interface-complete but ships unconfigured (no live keys in
  tests); wire `complete_fn` to a provider to enable.
* Drift detectors are deterministic-threshold only by design; the module
  boundary allows richer detectors when earned.
* `execute`-capability tools (shell) record inbound output classification but
  no outbound destination edge (side effects are not statically knowable) —
  lineage shows the gate/execution instead.
* Direct `/v1/tools/invoke` without a session runs legacy (unbound): evidence
  is recorded, governance overlay skipped. Bind a session to govern it.
* Decision Records project agent runs; the completions path keeps its V1
  `DecisionTrace` + evaluation worker (both feed the audit report).
