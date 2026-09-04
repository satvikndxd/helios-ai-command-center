# HELIOS Governance Architecture (V1.5)

> **HELIOS defines what AI systems are allowed to do, records what they
> actually did, evaluates whether they behaved correctly, and provides
> continuous assurance that they remain within policy.**

HELIOS is organized around four planes. Every module belongs to exactly one
(execution surfaces are *governed by* the planes, not a plane themselves).

```
                AI SYSTEM
                    │
                    ▼
             ┌──────────────┐
             │    HELIOS    │
             ├──────────────┤
             │ IDENTITY     │  who/what is this AI system?
             │ POLICY       │  what is it allowed to do?
             │ EVIDENCE     │  what did it actually do?
             │ ASSURANCE    │  is it still trustworthy?
             └──────┬───────┘
                    │
                    ▼
           MODELS / DATA / TOOLS
```

## Module → plane map

### IDENTITY PLANE — "who/what is this AI system?"

| Module | Role | Status |
|---|---|---|
| `governance/systems.py` | **AI System Registry**: system_id, owner, purpose, environment, lifecycle, autonomy level (L0–L5), risk class, associated models/tools/data classes/policies, versioned revisions | V1.5 |
| `models.py::AISystem` | Registry storage | V1.5 |
| `models.py::AgentSession` (`system_id`) | Binds execution sessions to a registered system; sessions inherit environment, autonomy, grants | V1.5 (extends V1) |
| `models.py::Tenant/Application/ApiKey`, `security.py` | Tenant identity + authentication; every governance query is tenant-scoped | V1 (reused) |
| `broker/types.py::InvocationContext` | Carries system_id, agent, user, environment, autonomy level, data classes into every decision | V1 (extended) |

### POLICY PLANE — "what is it allowed to do?"

| Module | Role | Status |
|---|---|---|
| `broker/policy.py` | Versioned, deterministic, serializable rule engine. V1.5 adds matching on **autonomy level, data classes, model/provider, system risk class** and effects **REQUIRE_HUMAN_REVIEW / BLOCK_DEPLOYMENT** alongside ALLOW/DENY/REQUIRE_APPROVAL | V1 (extended) |
| `governance/model_registry.py` + `models.py::AIModel` | **Model Registry**: provider, model_id, version, region, risk class, approved status, allowed data classes/environments. `evaluate_model_use()` gates every model call; unknown model → not approved. Status changes emit governance events | V1.5 |
| `broker/permissions.py` | Scoped grants + resource constraints (repo pinning, branch ≠ main, path prefix) | V1 (reused) |
| `broker/risk.py` | Deterministic contextual risk (`{risk, score, reasons}`) — now autonomy- and data-class-aware | V1 (extended) |
| `governance/classification.py` | Deterministic data classification (PUBLIC / INTERNAL / CONFIDENTIAL / SENSITIVE / PII) built on Sentinel detection + declared context | V1.5 |
| `policy.py` (completions preflight/output) | Legacy hot-path policy for governed completions | V1 (kept) |
| `web/policy.py`, `web/mcp.py` | Web/MCP surface policies (domain allowlists, trust gating, budgets) | V1 (kept) |

### EVIDENCE PLANE — "what did it actually do?"

| Module | Role | Status |
|---|---|---|
| `models.py::TraceEvent` + `broker/trace.py` | Hierarchical trace: RUN → MODEL_CALL / DATA_ACCESS / TOOL_PROPOSAL → PERMISSION / RISK / POLICY / APPROVAL / TOOL_EXECUTION / HUMAN_REVIEW / OUTCOME. Secrets scrubbed before persistence | V1 (extended with `data_access`, `model_governance`, `governance_event`) |
| `models.py::DecisionRecord` + `governance/decisions.py` | **Normalized AI Decision Record** (stable, versioned schema): system, actor, action, model+version, data classes, risk, policy+rule, decision, approver, trace lineage. One per governed decision | V1.5 |
| `governance/explain.py` | **WHY?** — explanation assembled from the actual recorded permission/risk/policy/approval/identity state, never hard-coded text | V1.5 |
| `governance/lineage.py` | Data-flow view (source → retrieval → model → tool → destination) derived exclusively from recorded TraceEvents — no decorative edges | V1.5 |
| `models.py::DecisionTrace` | Per-completion trace for the legacy governed-completion path | V1 (kept) |
| `routes/ingest.py` | OTel-compatible external span ingestion into the same evidence store | V1 (kept) |

### ASSURANCE PLANE — "is it still trustworthy?"

| Module | Role | Status |
|---|---|---|
| `governance/evaluation.py` + `models.py::GovernanceEvaluation` | Deterministic governance metrics computed from trace evidence: policy compliance, oversight compliance, tool misuse, PII leakage, task success, cost, latency. Extensible; LLM judges can slot in later, never as the only signal | V1.5 |
| `governance/score.py` | **Governance score**: transparent weighted checks (identity, model approval, data policy, oversight, compliance, evaluation, auditability, security, drift) — every check carries a reason; no vanity number | V1.5 |
| `agent/replay.py` | Replay recorded runs against same/newer/candidate policy; comparison of allowed/denied/approval/violations | V1 (extended) |
| `governance/changes.py` + `models.py::ChangeRecord` | **AI Change Management**: CURRENT → CANDIDATE → REPLAY → EVALUATION → HUMAN REVIEW → APPROVED → DEPLOYED (→ ROLLBACK), with evidence attached | V1.5 |
| `governance/drift.py` + `models.py::GovernanceBaseline` | Deterministic drift detection vs stored baselines (documented thresholds): approval-rate, denial-rate, tool set, model set, autonomy, policy version | V1.5 |
| `governance/audit.py` | Machine-readable governance audit report per system (identity, models, tools, policies, evaluations, violations, oversight, changes, drift, score). *Governance evidence*, never legal-compliance claims | V1.5 |
| `evaluators/`, `worker.py` | Completion-quality evaluation loop (groundedness, refusal, latency) + review queue | V1 (kept) |

### Governed execution surfaces (not a plane — enforced BY the planes)

| Module | Role |
|---|---|
| `broker/core.py::ToolBroker` | **The execution enforcement point.** manifest → argument validation → identity → permissions → data classification → contextual risk → policy → human oversight → idempotency → execution → evidence. Unknown tools deny-by-default |
| `agent/runtime.py` | Agent runtime: persistent sessions, explicit state machine, cancellation, resume; every proposal passes through the broker; model calls pass model governance |
| `tools/` (fs, shell, git, GitHub, HTTP, MCP) | The governed tool surface |
| `providers/`, `gateways.py`, `registry.py` | Model/gateway abstraction + routing (execution plumbing; *governance* of models lives in the Model Registry) |
| `sentinel.py`, `web/sanitize.py` | Security controls: PII/injection detection, secret scrubbing, output sanitization — governance controls, not the product |
| `tui/` | Governance-aware control center: /systems /models /policies /decisions /approvals /changes /drift /audit + the governed agent |

### Deliberately de-emphasized (kept, off the roadmap)

`workflows/` domain packs, `graph.py` knowledge graph, `evolution.py`
self-improvement proposals, web research adapters, browser sessions — all
remain functional route-layer add-ons; none drive the architecture.

## Invariants

1. Every governance status derives from recorded state — no decorative UI.
2. Every policy decision is explainable (rule id + reasons on the trace).
3. Every AI action is traceable to a system, actor, policy, and outcome.
4. Approvals bind to exact payload hashes; expiry and scope are enforced.
5. Candidate changes are testable (replay + evaluation) before deployment.
6. Default deny wherever uncertainty affects safety.
7. Secrets never reach traces, decision records, or logs.
8. No legal/regulatory compliance claims — only governance evidence.
