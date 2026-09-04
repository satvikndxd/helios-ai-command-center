<div align="center">

<img src="assets/helios-banner.svg" alt="HELIOS" width="880">

<br/><br/>

# HELIOS
## The control plane for AI agents.

**Give agents access to your tools without giving them unrestricted access to your company.**

<br/>

<img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-00E676?style=flat-square&labelColor=0A1A0F">
<img alt="Tests" src="https://img.shields.io/badge/tests-138_passing_in_~5s-34D399?style=flat-square&labelColor=0A1A0F">
<img alt="Runtime deps" src="https://img.shields.io/badge/runtime_deps-8-34D399?style=flat-square&labelColor=0A1A0F">
<img alt="License" src="https://img.shields.io/badge/license-MIT-00E676?style=flat-square&labelColor=0A1A0F">

</div>

---

AI agents are useful exactly when they can touch real things — your repos, your
shell, your filesystem, your APIs. That is also exactly when they are dangerous.
HELIOS sits between the agent and the world:

```
                      AGENT
                        │  proposes a tool call
                        ▼
       ┌────────────── HELIOS ──────────────┐
       │  identity / context                │   who is acting, where, for whom
       │  permission evaluation             │   scoped grants + resource constraints
       │  action risk evaluation            │   contextual: same tool ≠ same risk
       │  policy decision                   │   ALLOW · DENY · REQUIRE_APPROVAL
       │  human approval (when required)    │   bound to the exact payload hash
       └────────────────┬───────────────────┘
                        ▼
                      TOOLS                     filesystem · shell · git · GitHub · HTTP · MCP
                        │
                        ▼
              immutable decision trace  →  replay / evaluation
```

**No model-generated action executes directly.** Every tool invocation flows
through the Tool Broker — the single execution boundary — and leaves a
hierarchical decision trace that can answer, later: *what happened, who
initiated it, why was it allowed, who approved it, what actually executed,
what did it cost.*

---

## Sixty seconds of HELIOS

```console
$ helios                       # starts the control plane, opens the governed agent

you › fix the flaky timeout test and merge the fix

  [THINKING]
    → github.get_repo {"repo": "acme/api"}
      risk [LOW] read operation
      policy [ALLOW] low/medium-risk action within granted permissions
      [OK]
    → fs.write {"path": "tests/test_timeout.py", ...}
    → shell.run {"command": "pytest tests/test_timeout.py"}
    → git.branch {"name": "agent/fix-timeout"}   → git.commit → github.create_pr
  [AWAITING APPROVAL] github.merge_pr requires human approval

┌─[ APPROVAL REQUIRED ]───────────────────────────────────────────┐
│  agent        helios-agent                                      │
│  tool         github.merge_pr                                   │
│  github.repo  acme/api                                          │
│  github.base  main                                              │
│  environment  production                                        │
│  risk         [CRITICAL] score 0.95                             │
│               – write operation                                 │
│               – production environment                          │
│               – protected branch 'main'                         │
│  policy       helios-default-v1 · rule approval_for_high_risk   │
│  why          high/critical-risk actions require human approval │
│  trace        run c24b6cd8…                                     │
└─────────────────────────────────────────────────────────────────┘
  [a]pprove  [d]eny  [s]ession-approve  [i]nspect  [l]ater
```

A `github.read_file` sails through as **LOW** risk. A `github.merge_pr` into a
protected branch in production is **CRITICAL** and stops for a human. Same
agent, same tools — the *context* decides. Approve it and HELIOS executes
exactly the payload you approved (approvals are bound to a SHA-256 of the
action + arguments; if the agent mutates the payload, the approval is void).

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/satvikndxd/helios-ai-command-center/main/install.sh | bash
helios
```

No sudo, everything under `~/.helios`, SQLite by default, works over SSH.
Under two minutes on a clean machine. Zero API keys required to try it — the
built-in `scripted`/`mock` providers run the entire governed loop offline.

Connect the real world when ready:

```bash
export HELIOS_GROQ_API_KEY=...            # or OPENAI / ANTHROPIC / GEMINI / OPENROUTER
export HELIOS_AGENT_PROVIDER=groq
export HELIOS_GITHUB_TOKEN=ghp_...        # for the github.* tools
export HELIOS_GITHUB_REPO=you/yourrepo    # the ONE repo this agent may touch
helios
```

---

## The five primitives

### 1 · Agent runtime

Persistent sessions that survive terminal closure; resume or fork them.
Every run is an explicit state machine — the TUI never shows a blocked agent
as a generic spinner:

```
thinking · planning · tool_pending · running · awaiting_approval · blocked
completed · failed · cancelled
```

`/sessions` `/session <id>` `/trace <run>` `/replay <run>` `/resume` `/cancel`

### 2 · Tool Broker — the execution boundary

Every tool publishes a declarative manifest: name, version, owner, capability,
input/output schema, base risk class, permission scopes, resource fields,
network requirements, approval level, idempotency, provenance. No manifest, no
execution. The broker validates arguments, evaluates permissions → risk →
policy, gates on approval, journals effects under idempotency keys (safe
retry, no duplicate merges), and sanitizes every result.

P0 tools: `fs.*` (workspace-jailed) · `shell.run` (no shell expansion,
secret-stripped env, hard timeout) · `git.*` · `github.*` (real REST) ·
`http.get` (domain allowlist) · `mcp.call` (trust-gated MCP).

### 3 · Permissions — scopes with resource constraints

Not a boolean allow/deny matrix:

```json
{"scope": "github.merge",   "constraints": [{"field": "github.repo",   "op": "eq", "value": "acme/api"}]}
{"scope": "git.write",      "constraints": [{"field": "git.branch",    "op": "ne", "value": "main"}]}
{"scope": "filesystem.write","constraints": [{"field": "filesystem.path","op": "prefix", "value": "/workspace"}]}
```

Grants understand organization, project, environment, agent identity, user
identity, tool, resource, and data class. Deny by default; path traversal is
normalized before the prefix check and re-checked in the executor.

### 4 · Contextual risk + versioned policy

Risk is computed from tool × arguments × target × environment × actor, not
from the tool name:

```json
{"risk": "critical", "score": 0.95, "reasons": [
  "write operation", "production environment", "protected branch 'main'"]}
```

Policies are ordered rule sets — versioned, serializable, deterministic,
first-match-wins, default-deny. Every decision carries the rule that fired and
a full explanation, and is written to the trace:

```
DENY · rule deny_autonomous_production_writes
reason: production write forbidden for autonomous agents
```

### 5 · Approval + audit

The approval binds to the exact payload hash — *approve action A, mutate
payload, execute action B* is structurally impossible. Approvers can deny,
approve once, approve for the session, or (for tools that allow it) edit the
arguments — which re-binds the approval to the edited payload. Every run is
one hierarchical trace:

```
agent_run
 ├── model_call
 ├── tool_proposal
 │    ├── permission_evaluation
 │    ├── risk_evaluation
 │    ├── policy_evaluation
 │    ├── approval
 │    └── tool_execution
 ├── state_change
 └── outcome
```

Secrets are scrubbed before anything is persisted. Tool output is treated as
untrusted input: prompt-injection patterns are flagged (and withheld entirely
for network tools) — a tool result can never override policy.

---

## Replay — govern the past against tomorrow's policy

Any recorded run can be re-evaluated against the same policy, a newer one, or
a candidate document — nothing executes, everything is compared:

```console
you › /replay c079de31 candidate-strict-v2

┌─[ REPLAY ]──────────────────────────────────────┐
│  policy      candidate-strict-v2                │
│  proposals   7                                  │
│  original    {"executed": 6, "approval": 1}     │
│  candidate   {"executed": 4, "denied": 1,       │
│               "approval_required": 2}           │
└─────────────────────────────────────────────────┘
  • fs.write  executed → approval_required  (candidate: all writes need approval)
```

Test a policy change against last month's real agent traffic before deploying it.

## External agents — observe what you didn't build

HELIOS also ingests OpenTelemetry-shaped traces from agents that were *not*
built on HELIOS:

```bash
curl -X POST localhost:8000/v1/ingest/otel -H "X-Helios-API-Key: $KEY" \
  -d '{"resourceSpans": [...]}'    # spans land in the same trace store
```

## API surface

```
POST /v1/agent/sessions                GET  /v1/agent/sessions/{id}
POST /v1/agent/sessions/{id}/messages  POST /v1/agent/sessions/{id}/fork
GET  /v1/agent/runs/{id}/events        POST /v1/agent/runs/{id}/cancel|resume|retry|replay
POST /v1/agent/approvals/{id}/decide   GET  /v1/approvals?status=pending
GET  /v1/tools                         POST /v1/tools/invoke
GET  /v1/policies                      POST /v1/ingest/otel
```

Everything the TUI does goes through this API — build your own surface on it.

---

## Security model

- **Single execution boundary** — no code path executes a tool outside the broker; unknown tools are denied at the manifest gate.
- **Deny by default** — no grant, no rule, no execution.
- **Payload-bound approvals** — SHA-256 over `{action, args}`; tampering invalidates.
- **Idempotency journal** — retries replay the recorded effect instead of re-executing.
- **Workspace jail** — filesystem/shell/git operate under one root; `../` and symlink escapes blocked at two layers.
- **Secret hygiene** — subprocess env stripped of `*KEY*/*TOKEN*/*SECRET*`; secrets scrubbed from tool output *and* from every trace payload.
- **Untrusted tool output** — injection patterns flagged; external content quarantined; instructions in tool results are data, never commands.
- **Tenant isolation** — every query is tenant-scoped; sessions, runs, traces, approvals never cross tenants.

Each of these boundaries has tests (`tests/test_tool_broker.py`,
`tests/test_agent_runtime.py`), including a full end-to-end flagship test:
read → edit → test → branch → PR → merge request → CRITICAL → approval →
execution → complete trace.

## Model & gateway abstraction

Bring any model: OpenAI-compatible endpoints (Groq, OpenRouter, Together,
local Ollama/vLLM/LM Studio, …), Anthropic, Gemini, or custom gateway
profiles with dynamic model discovery (`/refresh`) and router fallback
chains. Credentials are referenced by env-var name and never stored. The
abstraction is the point — provider count is not.

## Also in the box (supporting capabilities)

Kept deliberately off the critical path: governed completions with PII/injection
sentinel + RAG grounding, an evaluation worker (groundedness, refusal, latency)
with a human review queue, governed web research adapters, MCP server registry
with trust gating and budgets, encrypted browser sessions, domain workflow
packs (Engineering/Software/Finance demos), and a human-gated self-improvement
proposal loop. See [docs/](docs/).

## Development

```bash
git clone https://github.com/satvikndxd/helios-ai-command-center && cd helios-ai-command-center
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=src .venv/bin/pytest tests -q        # 138 tests, ~5s, no network, no Postgres
PYTHONPATH=src .venv/bin/uvicorn helios.main:app  # SQLite by default
```

Architecture map and V1 plan: [docs/V1_PLAN.md](docs/V1_PLAN.md).

---

<div align="center">

*An AI agent wants to do something dangerous. HELIOS understands what it wants
to do, who is doing it, decides whether it is allowed, asks a human when
necessary, executes safely — and leaves an exact audit trail.*

**That is HELIOS.**

</div>
