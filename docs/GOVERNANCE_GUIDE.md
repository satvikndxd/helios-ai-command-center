# HELIOS Governance Guide

> Operator's guide to running the AI Governance Control Plane: register →
> govern → run → assure → audit. Every step below is backed by an API
> endpoint, a TUI view, and tests.

---

## 0. Prerequisites

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m helios.governance.demo   # see the whole story first
```

HELIOS runs zero-config on SQLite with mock/scripted providers. Point
`HELIOS_DATABASE_URL` at Postgres for shared deployments; run
`python -m helios.migrate` after upgrades (idempotent, additive).

Create an API key (tenant-scoped; every endpoint requires
`X-Helios-API-Key`):

```bash
PYTHONPATH=src python -m helios.cli create-api-key --tenant acme --app platform
```

---

## 1. IDENTITY — register the AI system

The system is the unit of governance. Register it before it runs.

```bash
curl -X POST $HELIOS/v1/systems -H "X-Helios-API-Key: $KEY" -d '{
  "system_id": "claims-agent",
  "name": "Claims Triage Agent",
  "owner": "claims-platform",
  "organization": "acme",
  "purpose": "triage inbound claims and recommend resolutions",
  "environment": "production",
  "autonomy_level": 3,
  "risk_class": "HIGH",
  "models": [{"provider": "anthropic", "model_id": "claude-x", "version": "17"}],
  "tools": ["fs.read", "github.create_pr"],
  "data_classes": ["INTERNAL", "CONFIDENTIAL", "PII"],
  "policies": ["claims-governance@v1"],
  "oversight": {"high_risk": "approval", "writes": "approval"}
}'
```

* **Autonomy**: L0 informational · L1 recommendations · L2 supervised ·
  L3 conditional · L4 high · L5 unrestricted. Production ceiling (built-in):
  L0–L2 allowed, L3 needs an approved deployment, L4/L5 blocked.
* Every mutation snapshots a version (`GET /v1/systems/{id}/versions`).
* Lifecycle: `draft → active ⇄ suspended → archived`; `blocked` always carries
  the policy reason that caused it.
* Bind agent sessions to the system: `POST /v1/agent/sessions
  {"system_id": "claims-agent", ...}` — from then on every model call and
  tool call is attributed, classified, and gated.

TUI: `/systems`, `/system claims-agent`.

## 2. MODELS — register and approve

```bash
curl -X POST $HELIOS/v1/models -d '{
  "provider": "anthropic", "model_id": "claude-x", "version": "17",
  "risk_class": "MEDIUM",
  "allowed_data_classes": ["PUBLIC", "INTERNAL"],
  "allowed_environments": ["staging", "production"]}'
curl -X POST $HELIOS/v1/models/{id}/approve -d '{"by": "security-review"}'
```

* Unregistered model on a governed system → **deny** (deny-by-default).
* `allowed_data_classes` is a severity ceiling: CONFIDENTIAL data to a
  PUBLIC/INTERNAL model is denied at preflight, with the check recorded.
* Approval changes require an accountable identity and are stored as
  `model_governance` evidence events (who approved, when, from what state).
* Test any combination without running anything:
  `POST /v1/models/check {"provider", "model_id", "data_classes",
  "environment"}` → decision + every check.

## 3. POLICY — define what is allowed

Two layers, both deterministic and versioned:

1. **Tool policies** (broker, in-code registry): tool × capability × risk ×
   environment × autonomy → allow/deny/require_approval; unmatched → deny.
2. **Governance policy sets** (persisted, tenant-scoped):

```bash
curl -X POST $HELIOS/v1/policies -d '{
  "name": "claims-governance", "version": "v1", "status": "candidate",
  "scope": {"system_ids": ["claims-agent"]},
  "rules": [
    {"id": "confidential-model-review",
     "when": {"subject": ["model_call"], "data_class": {"min": "CONFIDENTIAL"}},
     "effect": "require_human_review",
     "reason": "confidential+ data reaching a model requires review"},
    {"id": "no-prod-shell",
     "when": {"subject": ["tool_action"], "tool": "shell.*",
              "environment": ["production"]},
     "effect": "deny", "reason": "no shell in production"}
  ]}'
```

* Outcomes: `allow · deny · require_approval · require_human_review ·
  block_deployment`. First match wins per set; strictest decision wins across
  sets; every decision carries the matched rule and the condition trace.
* **Test before activating**: `POST /v1/policies/dry-run` (subject +
  candidate set) and system replay (below). Activate with
  `POST /v1/policies/governance/{id}/activate` (archives the previous active
  same-name set — activation is itself a governed change).
* Governance policies are an escalation overlay on tool policy: they can
  tighten decisions, never relax them.

TUI: `/policies`.

## 4. RUN — governed execution

```bash
curl -X POST $HELIOS/v1/agent/sessions -d '{
  "system_id": "claims-agent", "environment": "production",
  "autonomy": "supervised", "model_provider": "anthropic",
  "model_id": "claude-x", "github_repo": "acme/claims"}'
curl -X POST $HELIOS/v1/agent/sessions/{id}/messages -d '{"content": "..."}'
```

Every model call passes a recorded preflight (model approval · data ceiling ·
environment · governance policies). Every tool call passes the broker:
manifest → args → permissions → classification → risk → tool policy →
governance overlay → oversight → idempotent execution → sanitized result →
evidence. A blocked or gated run is never shown as "working": states are
explicit (`awaiting_approval`, `blocked`) with recorded reasons.

## 5. OVERSIGHT — the human in the loop

```bash
curl $HELIOS/v1/agent/approvals?status=pending            # the queue
curl -X POST $HELIOS/v1/agent/approvals/{id}/decide -d '{
  "decision": "approved", "decided_by": "release-manager",
  "comment": "reviewed evidence bundle", "expires_in_s": 3600}'
```

* Approvals bind to `sha256(tool + canonical args)` — payload tampering
  invalidates them and re-gates the action.
* `expires_in_s` sets a lapse time; expired approvals never authorize (the
  sweep is lazy and deterministic).
* `approve_session` grants a standing approval **scoped** to environment +
  system, optionally with `max_uses` and expiry.
* `delegate` reassigns (only the delegate may decide); `escalate` reassigns
  upward with a mandatory reason; `comments` build the audit thread.
* Every human decision is recorded as a `human_review` trace event under the
  gated proposal.

TUI: `/approvals`, `/approve <id>`, `/deny <id>`.

## 6. EVIDENCE — what actually happened

```bash
curl "$HELIOS/v1/decisions?system_id=claims-agent&decision=DENIED"
curl $HELIOS/v1/decisions/{id}/explanation     # the WHY, generated from evidence
curl $HELIOS/v1/agent/runs/{id}/events         # full hierarchical trace
curl $HELIOS/v1/systems/claims-agent/lineage   # event-backed data lineage
```

* Decision Records are projections of immutable trace events — rebuild any
  time (`POST /v1/decisions/rebuild`), they converge, never duplicate.
* Lineage edges cite event ids; gated flows show as `gated` (oversight
  working), denied flows as violations.

TUI: `/traces`, `/trace <run>`.

## 7. ASSURANCE — prove it stays within policy

**Evaluate + score**

```bash
curl -X POST $HELIOS/v1/systems/claims-agent/evaluate -d '{}'
curl $HELIOS/v1/systems/claims-agent/score
```

The score is 10 weighted components, each a checklist with reasons and
evidence refs. `insufficient_evidence` is a real state — components without
backing data never score 100.

**Replay before changing anything**

```bash
curl -X POST $HELIOS/v1/replay/systems/claims-agent -d '{
  "candidate_governance_set_id": "<candidate-set-id>"}'
# or candidate_tool_policy / candidate_system / candidate_model
```

Outputs original-vs-candidate counts, every changed decision with its rule,
and summary deltas (`+N newly gated`, `+N newly blocked`, `+N newly allowed`).
Nothing executes.

**Change management**

```bash
curl -X POST $HELIOS/v1/changes -d '{
  "change_type": "policy", "author": "governance-office",
  "target": {"family": "policy_set", "name": "claims-governance",
             "system_id": "claims-agent"},
  "candidate": {"name": "claims-governance", "version": "v2", "rules": [...]}}'
# then, in order (each step enforced server-side):
curl -X POST $HELIOS/v1/changes/{id}/replay
curl -X POST $HELIOS/v1/changes/{id}/evaluate
curl -X POST $HELIOS/v1/changes/{id}/risk-comparison
curl -X POST $HELIOS/v1/changes/{id}/submit-review     # creates bound approval
curl -X POST $HELIOS/v1/changes/{id}/approve -d '{"by": "...", "approval_id": "..."}'
curl -X POST $HELIOS/v1/changes/{id}/deploy -d '{"by": "..."}'
# regret?
curl -X POST $HELIOS/v1/changes/{id}/rollback -d '{"by": "...", "note": "..."}'
```

The review approval binds to the exact change digest — editing the candidate
after review invalidates it. Rollback restores the previous snapshot and
opens its own governed rollback record.

**Drift**

```bash
curl -X POST $HELIOS/v1/systems/claims-agent/drift/baseline -d '{"by": "sre"}'
curl -X POST $HELIOS/v1/systems/claims-agent/drift/check
curl $HELIOS/v1/drift                                    # open signals
curl -X POST $HELIOS/v1/drift/{signal_id}/acknowledge -d '{"by": "sre"}'
```

Rules are deterministic with documented thresholds and minimum samples
(see `governance/drift.py` docstring): approval-rate drops, denial spikes,
oversight bypasses (zero tolerance), new/unapproved models, tool-mix shifts,
raised autonomy, production moves, policy version changes, evaluation
regressions, cost blowups. Acknowledging is not muting: fresh detections open
new signals.

**Audit**

```bash
curl $HELIOS/v1/audit/claims-agent > claims-agent-governance-report.json
```

Identity · models · applied policies · activity · violations · oversight
compliance (including declared-requirement gaps) · data flows · evaluation ·
score · drift · changes — one JSON document, machine-readable by
construction. It is governance evidence: policy status, audit record, control
status. It is **not** a legal or regulatory compliance certification.

TUI: `/evaluations`, `/changes`, `/drift`, `/audit <system>`,
`/replay system <id> [set-id]`.

---

## Governance patterns that matter

* **Register before you run.** Unbound (legacy) sessions still work with V1
  semantics, but they are flagged in evidence and fail the identity component
  of the score. Governed means registered.
* **Declare data classes; let detection backstop you.** Declared classes set
  the floor; content detection (PII/secret markers) raises the effective class
  automatically — a "PUBLIC" system that ingests an SSN is treated as PII at
  the next model call.
* **Write policies as candidates, replay, then activate.** The dry-run and
  replay endpoints use the exact production evaluation code — what you test is
  what enforces.
* **Treat requirement gaps as findings.** If oversight analysis reports a gap
  between what you declared (`oversight` on the system) and what policies
  enforce, fix the policy — the gap is HELIOS telling you your rules do not
  implement your intent.
* **Baseline after every intentional change.** Drift compares against the
  latest baseline; re-baseline when a change lands, so future signals mean
  something.
