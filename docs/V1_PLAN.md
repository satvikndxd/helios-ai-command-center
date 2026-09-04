# HELIOS V1 — Architecture Map & Implementation Plan

> Internal engineering plan. Product thesis: **"The control plane for AI agents."**
> Give agents access to your tools without giving them unrestricted access to your company.

## 1. What exists today (preserve)

| Subsystem | State | Verdict |
|---|---|---|
| `web/actions.py` — typed actions, payload-hash-bound approvals (`ApprovalRequest`), idempotent effect journal (`ActionEffect`) | Real, tested | **Foundation for the Tool Broker.** Reuse hash binding + idempotency + approval tables verbatim. |
| `sentinel.py` + `web/sanitize.py` — PII/injection detection, secret scrubbing, untrusted-content quarantine | Real, tested | Keep. Every tool result flows through it. |
| `policy.py` (completions preflight/output) | Real, deterministic | Keep for the completion hot path; the *tool* policy engine is new. |
| `DecisionTrace` + evaluation worker | Real | Keep for completions. Agent runs get a new hierarchical `TraceEvent` model. |
| Gateways/providers abstraction, router v2 fallback chains | Real | Keep as-is. No new providers. |
| MCP broker (trust gating, allowlists, budgets), vault, browser worker | Real, tested | Keep; MCP becomes a broker-governed tool. |
| Multi-tenant auth (`ApiKey` → tenant scoping) | Real | Keep. |
| TUI (stdlib, governed/direct modes), installer, `helios` launcher | Real | Extend with agent session mode + approval UX. |

## 2. Refactor / de-emphasize

- Domain workflow packs (engineering/finance/software), knowledge graph, self-evolution: **kept, but off the primary path** (route-layer add-ons; nothing in the core imports them). README no longer leads with them.
- Inconsistent risk vocabularies (`high/medium` vs `informational..critical`): all **new** code uses one enum: `low | medium | high | critical`.
- Web read adapters (reddit/youtube/socialcrawl) stay as-is; not part of the V1 story.

## 3. Genuinely missing primitives (build)

1. **Tool Broker** (`helios/broker/`) — the single execution boundary: manifest → validate args → permission scopes → contextual risk → policy decision → approval → execute → sanitize → trace.
2. **Tool manifests** — declarative: name, version, owner, capability, JSON input schema, scopes, resource extraction, network needs, approval level, idempotency, provenance.
3. **Permission scopes with resource constraints** — `github.write` constrained to `repo == x`, `branch != main`; `filesystem.*` rooted at a workspace path. Grants carry org/project/environment/agent/user dimensions.
4. **Contextual risk engine** — deterministic scoring over tool × action × args × target × environment × context → `{risk, score, reasons}`.
5. **Versioned tool policy** — ALLOW / DENY / REQUIRE_APPROVAL rules, serializable (replayable), every decision explained.
6. **Real P0 tools** — filesystem, safe shell, git, GitHub (REST, real executors), HTTP, MCP.
7. **Agent runtime** — persistent sessions (`AgentSession`), explicit run state machine (`AgentRun`: thinking/planning/tool_pending/awaiting_approval/running/blocked/completed/failed/cancelled), model-proposed tool calls flowing only through the broker, resume/cancel/retry.
8. **Hierarchical trace** — `TraceEvent` rows (model_call, tool_proposal, policy_evaluation, approval, tool_execution, outcome) under an agent run.
9. **Replay** — re-evaluate a recorded run's tool proposals against current/candidate policy; comparison report.
10. **External trace ingestion** — minimal OTLP/JSON-shaped endpoint mapping spans → TraceEvents.

## 4. Order of implementation

1. Broker core + manifests + permissions + risk + policy (+ tests)
2. P0 tools behind the broker (+ tests)
3. Agent models, runtime, routes, replay (+ tests)
4. Approval UX + TUI agent mode
5. OTel ingestion, flagship E2E test, README rewrite

## 5. Invariants (non-negotiable)

- No tool executes outside `ToolBroker.invoke`.
- Approvals bind to `sha256(tool + canonical args)`; payload mutation invalidates approval.
- Policy evaluation is deterministic and serialized into the trace.
- All tool output is treated as untrusted: secrets scrubbed, injection quarantined.
- A blocked/awaiting-approval agent is never shown as generic "working".
- Every decision answers: what, who, why, approved by whom, what executed, what resulted.
