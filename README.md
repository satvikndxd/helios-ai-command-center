<div align="center">

<img src="assets/helios-banner.svg" alt="HELIOS" width="880">

<br/><br/>

# HELIOS
## The Control Plane for AI Agents

**Give agents access to your tools without giving them unrestricted access to your company.**

<br/>

<img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-00E676?style=flat-square&labelColor=0A1A0F">
<img alt="Tests" src="https://img.shields.io/badge/tests-138_passing_in_~5s-34D399?style=flat-square&labelColor=0A1A0F">
<img alt="Runtime deps" src="https://img.shields.io/badge/runtime_deps-8-34D399?style=flat-square&labelColor=0A1A0F">
<img alt="License" src="https://img.shields.io/badge/license-MIT-00E676?style=flat-square&labelColor=0A1A0F">
<img alt="GitHub Repo" src="https://img.shields.io/github/repo-size/satvikndxd/helios-ai-command-center?color=00E676&label=repo%20size&style=flat-square&labelColor=0A1A0F">

<br/><br/>

[How It Works](#how-it-works) • [Quick Start](#quick-start) • [Core Concepts](#core-concepts) • [Advanced Features](#advanced-features) • [API Reference](#api-reference) • [Security](#security-model) • [Development](#development)

</div>

---

## Quick Start

### 1. Run HELIOS with mock providers (no API keys)

```bash
helios
```

This starts the control plane and opens the governed agent interface. Try the demo workflow:

```
you › fix the flaky timeout test and merge the fix

  [THINKING]
    → github.get_repo {"repo": "acme/api"}
      risk [LOW] read operation
      policy [ALLOW] low/medium-risk action within granted permissions
      [OK]
```

### 2. Connect real providers

When ready to work with real tools and models:

```bash
# Set your model provider (Groq, OpenAI, Anthropic, Gemini, or OpenRouter)
export HELIOS_GROQ_API_KEY=...            # or HELIOS_OPENAI_API_KEY, etc.
export HELIOS_AGENT_PROVIDER=groq

# Configure GitHub access (optional — for github.* tools)
export HELIOS_GITHUB_TOKEN=ghp_...        # GitHub personal access token
export HELIOS_GITHUB_REPO=you/yourrepo    # The ONE repo this agent may touch

helios
```

### 3. Explore the TUI commands

| Command | Description |
|---------|-------------|
| `/sessions` | List all agent sessions |
| `/session <id>` | View a specific session |
| `/trace <run>` | Inspect a run's decision trace |
| `/replay <run>` | Re-evaluate a run against a different policy |
| `/resume` | Resume a paused session |
| `/cancel` | Cancel a running agent |

---

## How It Works

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

## Example: Risk-Based Approval Flow

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

## Core Concepts

HELIOS is built on five foundational primitives that work together to provide safe, governed agent execution:

### 1. Agent Runtime

Persistent sessions that survive terminal closure; resume or fork them.
Every run is an explicit state machine — the TUI never shows a blocked agent
as a generic spinner:

```
thinking · planning · tool_pending · running · awaiting_approval · blocked
completed · failed · cancelled
```

**TUI Commands:** `/sessions` · `/session <id>` · `/trace <run>` · `/replay <run>` · `/resume` · `/cancel`

### 2. Tool Broker — The Execution Boundary

Every tool publishes a declarative manifest: name, version, owner, capability,
input/output schema, base risk class, permission scopes, resource fields,
network requirements, approval level, idempotency, provenance. No manifest, no
execution. The broker validates arguments, evaluates permissions → risk →
policy, gates on approval, journals effects under idempotency keys (safe
retry, no duplicate merges), and sanitizes every result.

**P0 Tools:** `fs.*` (workspace-jailed) · `shell.run` (no shell expansion, secret-stripped env, hard timeout) · `git.*` · `github.*` (real REST) · `http.get` (domain allowlist) · `mcp.call` (trust-gated MCP)

### 3. Permissions — Scopes with Resource Constraints

Not a boolean allow/deny matrix:

```json
{"scope": "github.merge",   "constraints": [{"field": "github.repo",   "op": "eq", "value": "acme/api"}]}
{"scope": "git.write",      "constraints": [{"field": "git.branch",    "op": "ne", "value": "main"}]}
{"scope": "filesystem.write","constraints": [{"field": "filesystem.path","op": "prefix", "value": "/workspace"}]}
```

Grants understand organization, project, environment, agent identity, user
identity, tool, resource, and data class. Deny by default; path traversal is
normalized before the prefix check and re-checked in the executor.

### 4. Contextual Risk + Versioned Policy

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

### 5. Approval + Audit

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

## Advanced Features

### Replay — Govern the Past Against Tomorrow's Policy

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

### External Agents — Observe What You Didn't Build

HELIOS also ingests OpenTelemetry-shaped traces from agents that were *not*
built on HELIOS:

```bash
curl -X POST localhost:8000/v1/ingest/otel -H "X-Helios-API-Key: $KEY" \
  -d '{"resourceSpans": [...]}'    # spans land in the same trace store
```

---

## API Reference

Everything the TUI does goes through this API — build your own surface on it.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/agent/sessions` | Create a new agent session |
| `GET` | `/v1/agent/sessions/{id}` | Get session details |
| `POST` | `/v1/agent/sessions/{id}/messages` | Send a message to a session |
| `POST` | `/v1/agent/sessions/{id}/fork` | Fork an existing session |
| `GET` | `/v1/agent/runs/{id}/events` | Get run events |
| `POST` | `/v1/agent/runs/{id}/cancel` | Cancel a run |
| `POST` | `/v1/agent/runs/{id}/resume` | Resume a run |
| `POST` | `/v1/agent/runs/{id}/retry` | Retry a failed run |
| `POST` | `/v1/agent/runs/{id}/replay` | Replay a run with different policy |
| `POST` | `/v1/agent/approvals/{id}/decide` | Submit an approval decision |
| `GET` | `/v1/approvals?status=pending` | List pending approvals |
| `GET` | `/v1/tools` | List available tools |
| `POST` | `/v1/tools/invoke` | Invoke a tool directly |
| `GET` | `/v1/policies` | List policies |
| `POST` | `/v1/ingest/otel` | Ingest OpenTelemetry traces |

---

## Security Model

| Boundary | Description |
|----------|-------------|
| **Single execution boundary** | No code path executes a tool outside the broker; unknown tools are denied at the manifest gate |
| **Deny by default** | No grant, no rule, no execution |
| **Payload-bound approvals** | SHA-256 over `{action, args}`; tampering invalidates |
| **Idempotency journal** | Retries replay the recorded effect instead of re-executing |
| **Workspace jail** | Filesystem/shell/git operate under one root; `../` and symlink escapes blocked at two layers |
| **Secret hygiene** | Subprocess env stripped of `*KEY*/*TOKEN*/*SECRET*`; secrets scrubbed from tool output *and* from every trace payload |
| **Untrusted tool output** | Injection patterns flagged; external content quarantined; instructions in tool results are data, never commands |
| **Tenant isolation** | Every query is tenant-scoped; sessions, runs, traces, approvals never cross tenants |

Each of these boundaries has tests (`tests/test_tool_broker.py`,
`tests/test_agent_runtime.py`), including a full end-to-end flagship test:
read → edit → test → branch → PR → merge request → CRITICAL → approval →
execution → complete trace.

---

## Additional Capabilities

### Model & Gateway Abstraction

Bring any model: OpenAI-compatible endpoints (Groq, OpenRouter, Together,
local Ollama/vLLM/LM Studio, …), Anthropic, Gemini, or custom gateway
profiles with dynamic model discovery (`/refresh`) and router fallback
chains. Credentials are referenced by env-var name and never stored. The
abstraction is the point — provider count is not.

### Supporting Features

Kept deliberately off the critical path:

- **Governed completions** with PII/injection sentinel + RAG grounding
- **Evaluation worker** (groundedness, refusal, latency) with human review queue
- **Governed web research adapters**
- **MCP server registry** with trust gating and budgets
- **Encrypted browser sessions**
- **Domain workflow packs** (Engineering/Software/Finance demos)
- **Human-gated self-improvement proposal loop**

See [docs/](docs/) for detailed architecture documentation.

---

## Development

### Setup

```bash
git clone https://github.com/satvikndxd/helios-ai-command-center && cd helios-ai-command-center
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

### Running Tests

```bash
PYTHONPATH=src .venv/bin/pytest tests -q        # 138 tests, ~5s, no network, no Postgres
```

### Starting the Server

```bash
PYTHONPATH=src .venv/bin/uvicorn helios.main:app  # SQLite by default
```

### Documentation

- [V1 Architecture Plan](docs/V1_PLAN.md) — Implementation roadmap and engineering decisions
- [Web Access Architecture](docs/WEB_ACCESS_ARCHITECTURE.md) — Detailed web access patterns
- [Workflows](docs/WORKFLOWS.md) — Domain workflow documentation
- [Technical Checklist](docs/YC27_TECHNICAL_CHECKLIST.md) — YC27 technical requirements

---

<div align="center">

*An AI agent wants to do something dangerous. HELIOS understands what it wants
to do, who is doing it, decides whether it is allowed, asks a human when
necessary, executes safely — and leaves an exact audit trail.*

**That is HELIOS.**

<br/>

[⬆ Back to top](#helios)

</div>
