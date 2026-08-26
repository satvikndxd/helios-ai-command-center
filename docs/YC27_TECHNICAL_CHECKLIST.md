# Helios YC27 Technical Feature Checklist

**Scope:** Technical features only.
**Goal:** Make Helios genuinely competitive as a terminal-first agent runtime and governed AI control plane.

## The technical target

Helios should become **a Claude-Code-style terminal agent that can use any model, any OpenAI-compatible gateway, and any approved tool—while recording, evaluating, and governing every meaningful action**.

The current MVP already has a strong governance core: a gateway, traces, routing, fallback, tenant-isolated RAG, Sentinel, policy checks, evaluation jobs, review, simulation, dataset generation, and a knowledge-graph slice. The missing layer is a production-grade **agent runtime and developer product**. Hermes is a benchmark for persistent memory, skills, scheduling, messaging reach, parallel sub-agents, browser control, local execution, and self-hosting.[[1]] Langfuse, Portkey, and LangSmith establish expectations for observability, evaluation, provider breadth, governance, deployment, durable execution, and integrations.[[2]] [[3]] [[4]]

The most important warning is that **you do not need every enterprise feature before YC**. You need a technically impressive product that real developers use repeatedly, a narrow workflow that works end-to-end, and architecture that can expand without a rewrite. The priority sequence below is designed for that.

## Priority definitions

| Priority | Meaning | Expected timing |
| --- | --- | --- |
| P0 | Required for a credible competitive product and YC demo | Build before serious external pilots |
| P1 | Required for team adoption and early revenue | Build after the first repeated workflow works |
| P2 | Required for enterprise scale, regulated buyers, or major volume | Build after demand proves the need |
| Defer | Valuable eventually but likely to distract from product-market fit | Do not lead with it |

## P0 — Terminal-first agent runtime

This is the most important missing layer. Without it, Helios remains an infrastructure API that can observe agents but does not itself provide a compelling daily workflow.

| Feature | Technical requirements | Acceptance test |
| --- | --- | --- |
| Streaming TUI responses | Server-sent events or WebSocket stream; incremental Markdown rendering; trace finalization after stream completion | User sees tokens and tool events as they happen, and the final trace remains complete after interruption |
| Persistent sessions | Session IDs, local history, server synchronization, resume/fork/export, encrypted local cache | Close the terminal, reopen it, and resume the same agent context without losing trace linkage |
| Context management | Token estimation, automatic compaction, summarization checkpoints, pinned instructions, file/context references | Long sessions continue without exceeding the model context window or silently dropping critical instructions |
| Interrupt and cancellation | `Ctrl+C` cancellation, server cancellation endpoint, child-worker cancellation, cleanup hooks | An active run can be stopped without leaving an orphaned tool process or falsely marking the task complete |
| Tool-call event model | First-class events for proposed, approved, started, retried, completed, rejected, and failed tool calls | Every tool action appears in the TUI and in the parent-child DecisionTrace |
| Approval UX | Show tool, arguments, target, data classes, risk, cost, and policy; approve once/session, reject, edit, or always deny | A write action cannot execute until the required approval is recorded |
| Local workspace tools | Read/write files, patch application, directory listing, search, diff, and safe shell execution | Agent can inspect and modify a repository while showing a reviewable diff before applying changes |
| Safe shell execution | Process isolation, timeout, working-directory restriction, environment filtering, output limits, network policy | A command cannot escape the selected workspace or exceed configured time, memory, and network limits |
| Rich output rendering | Markdown, code blocks, diffs, tables, citations, errors, collapsible trace details | Common developer output is readable in a normal terminal and in a narrow SSH session |
| Slash-command system | `/model`, `/gateway`, `/models`, `/tools`, `/trace`, `/approve`, `/session`, `/compact`, `/clear`, `/help` | A new user can discover and control the session without memorizing undocumented flags |
| Agent status state machine | `thinking`, `planning`, `tool_pending`, `awaiting_approval`, `running`, `blocked`, `completed`, `failed`, `cancelled` | The interface never represents a waiting approval or failed tool call as generic "working" |
| Retry and replay | Retry last step, retry with another model, replay the entire task, preserve original trace | A failed run can be reproduced with the same inputs and compared to a candidate run |

### P0 TUI quality bar

The TUI must feel like a daily tool, not a demo. It should start in under two seconds locally, work over SSH, support terminals with limited color, handle pasted multi-line input, preserve scrollback, and provide predictable keyboard behavior. The default route must be visibly marked **GOVERNED**. Direct custom-gateway mode must be visibly marked **DIRECT** so users understand when Helios governance is bypassed.

## P0 — Universal model and gateway connectivity

The correct strategy is not to hard-code every model forever. The ecosystem changes too quickly. LiteLLM documents a broad provider catalog and a JSON-based extension path for simple OpenAI-compatible providers.[[5]] OpenRouter exposes a unified endpoint, programmatic model listing, streaming, and OpenAI SDK compatibility.[[6]] Helios should implement the same extensibility principle.

| Feature | Technical requirements | Acceptance test |
| --- | --- | --- |
| OpenAI Chat Completions compatibility | Accept the standard request/response shape, streaming, usage, tool calls, structured output, vision content, and common parameters | Existing OpenAI SDK applications work by changing only `base_url` and API key |
| OpenAI Responses compatibility | Add an adapter for responses-style input, output items, tools, reasoning metadata, and response IDs | A Responses API client can use Helios without a custom translation layer |
| Dynamic `/models` discovery | Fetch, cache, normalize, and refresh model IDs and metadata from each gateway | `/refresh` discovers models from a previously unknown compatible endpoint |
| Custom gateway profiles | `name`, `base_url`, auth source, headers, provider label, default model, mode, timeout, TLS policy | User can connect an internal gateway without changing Helios source code |
| Credential abstraction | Environment-variable references now; secret-manager interface later; no raw keys in config, traces, or logs | A profile can be committed safely without exposing its credential |
| Provider health checks | Latency, availability, authentication, model availability, rate-limit status, and last successful call | Router can remove an unhealthy provider and restore it after recovery |
| Capability metadata | Tools, vision, streaming, structured outputs, reasoning, embeddings, responses, model listing | TUI suggests only models capable of the requested operation |
| Request transformation pipeline | Map model IDs, headers, parameters, message formats, and unsupported fields per gateway | A gateway needing custom headers or parameter names works through configuration/adapter hooks |
| Provider fallback | Ordered candidates, circuit breakers, retry budgets, error classification, idempotency | A provider outage triggers a safe fallback and records every attempt |
| Model routing policies | Route by task type, risk, privacy, latency, cost, region, capability, and customer preference | A high-risk task cannot silently route to a disallowed model |
| Pricing and usage registry | Versioned input/output prices, cached provider metadata, unknown-price state, currency normalization | Cost estimates are transparent and never falsely report an unknown price as zero |
| Local runtime connectors | Ollama, LM Studio, vLLM, llama.cpp, SGLang, LocalAI, Docker Model Runner | A local model can be discovered and used without an API key |
| Hosted and niche connectors | OpenAI, OpenRouter, Groq, Together, Fireworks, DeepInfra, Hyperbolic, NVIDIA, Cerebras, SambaNova, DeepSeek, Mistral, xAI, Cohere, Perplexity, Hugging Face, Cloudflare, LiteLLM, Portkey, Azure/Vertex bridges, and arbitrary custom endpoints | Each connector uses the generic profile path unless it truly needs a specialized adapter |
| Embedding gateway support | Separate embedding model selection, dimensions, batching, retries, and compatibility checks | RAG ingestion fails clearly if the selected embedding dimension does not match the index |
| Audio and multimodal readiness | Provider capability flags for image, audio input, transcription, and speech output | Unsupported modalities are rejected with compatible model suggestions rather than opaque provider errors |

## P0 — Real agent execution

The current Helios trace model contains `tool_calls`, but the product must make tool execution a first-class runtime rather than a future field.

| Feature | Technical requirements | Acceptance test |
| --- | --- | --- |
| Tool registry | Versioned tool schemas, descriptions, owners, risk classes, input/output schemas, and lifecycle state | The agent can list tools with provenance and capability metadata |
| Tool broker | Central execution service that validates calls, applies policy, injects credentials, and records results | No tool can execute by directly receiving an unvalidated model-generated payload |
| Permission scopes | Read/write/admin scopes, resource constraints, project/environment boundaries, network destinations | Agent can read one repository but cannot write another or access arbitrary secrets |
| Tool manifests | Declarative YAML/JSON manifests for tools, permissions, policies, and required approvals | A tool installation is reviewable, diffable, and reproducible |
| MCP client and gateway | Connect to MCP servers, authenticate, filter tools, enforce policy, trace server calls, and isolate untrusted tool descriptions | An MCP tool appears in the same approval and audit flow as a native tool |
| Browser automation | Isolated browser worker, domain allowlist, downloads policy, cookie isolation, screenshots, and trace events | Browser actions cannot access unapproved domains or leak credentials into page content |
| Git provider tools | GitHub/GitLab/Bitbucket auth, repository scope, branches, diffs, pull requests, and checks | Agent can open a reviewable pull request without receiving unrestricted account access |
| Background jobs | Durable job state, retries, schedules, pause/resume, timeouts, and notifications | An agent continues after the terminal closes and can be resumed later |
| Sub-agents | Parent-child trace IDs, depth/concurrency/budget limits, isolated context, and cancellation propagation | Parallel work cannot exceed configured limits and all child results reconcile into the parent trace |
| Tool result safety | Treat tool outputs, web pages, files, and MCP descriptions as untrusted input; sanitize and label provenance | Prompt injection in a tool result cannot silently change the agent's policy or permissions |
| Idempotent actions | Idempotency keys, effect journal, duplicate detection, compensating actions, and confirmation for non-idempotent tools | Retry does not create duplicate tickets, commits, payments, or messages |

## P0 — Security and governance baseline

Helios's differentiator is not merely that it has a policy module. The policy must control actual agent actions, not only inspect text around model calls.

| Feature | Technical requirements | Acceptance test |
| --- | --- | --- |
| OIDC authentication | Login, issuer allowlist, token validation, logout/session handling, organization mapping | A design partner can sign in using its identity provider |
| Scoped authorization | Organization, project, environment, agent, tool, dataset, and trace scopes; service accounts; API keys | A reviewer can inspect traces but cannot change deployment policy |
| RBAC plus ABAC | Roles plus attributes such as data class, environment, tool risk, geography, and owner | Production write tools require the correct user, agent, environment, and approval context |
| Immutable audit log | Append-only events for auth, policy, data access, tool calls, approvals, exports, and changes | An auditor can reconstruct who authorized an action and what the agent did |
| Secret handling | Environment references, secret-manager integrations, redaction, rotation, and access audit | Secrets never appear in prompts, traces, error messages, or TUI output |
| Policy-as-code evolution | Versioned policies, dry-run mode, test corpus, simulation, policy diffs, activation windows, rollback | A policy can be tested on historical traces before affecting production |
| Action-level risk engine | Risk from tool, arguments, target, data class, user, model, environment, and workflow stage | The same tool is automatically treated differently in development and production |
| PII and sensitive-data controls | Detection, redaction, tokenization, reversible vault references, output scanning, retention controls | A configured sensitive field cannot reach a disallowed provider or tool |
| Prompt-injection defense | Scan user input, retrieved documents, tool outputs, web pages, uploaded files, and sub-agent messages | Untrusted instructions are labeled and cannot override system policy |
| Data-flow policy | Track data from source to prompt, model, tool, output, and export | Helios can explain why a request was allowed or blocked based on data movement |
| Tenant isolation | Database filters, object-storage prefixes, vector filters, cache namespaces, trace access tests | Cross-tenant access is denied across metadata, vectors, objects, tools, and logs |
| Retention and deletion | Per-tenant and per-data-class retention, legal hold hooks, deletion jobs, export, and verification | A customer can delete traces and confirm deletion across hot and cold storage |
| Security testing | Dependency scanning, SAST, secret scanning, container scanning, fuzzing, red-team prompt/tool suites | Every release runs security checks and has a documented exception process |

## P0 — Observability, evaluation, and release quality

Helios must turn its existing trace → evaluation → review → dataset → simulation loop into the product's central advantage. Langfuse emphasizes hierarchical traces, evaluations, prompt management, experiments, human annotation, datasets, and cost/latency dashboards.[[2]] LangSmith emphasizes production traces, online evaluators, failure analysis, human review, durable deployment, versioning, and rollback.[[4]]

| Feature | Technical requirements | Acceptance test |
| --- | --- | --- |
| OpenTelemetry ingestion | OTel-compatible traces, spans, attributes, baggage, sampling, and correlation | An external agent framework can send traces to Helios without using the Helios gateway |
| Hierarchical traces | Agent run → model call → retrieval → tool call → child agent → approval → outcome | One trace explains the full task, not only the final model response |
| Trace schema versioning | Stable event envelope, migration strategy, schema registry, and backward compatibility | Old traces remain queryable after a schema upgrade |
| Real-time metrics | Request rate, latency, error rate, queue lag, token usage, cost, cache hit, fallback rate, policy blocks, and approvals | Operator can see a production regression before customers report it |
| LLM-as-judge | Pluggable judge models, rubric versioning, judge calibration, confidence, and disagreement capture | Judge scores correlate with human labels on a maintained benchmark |
| Code and deterministic evaluators | Exact-match, JSON schema, regex, citation, latency, policy, tool outcome, and business-rule evaluators | Critical regressions can be detected without paying for a judge model |
| Human annotation | Review queues, labels, comments, adjudication, sampling, and golden datasets | A domain expert can label a trace and feed it back into an evaluation set |
| Prompt and agent versioning | Version prompts, tools, model policy, evaluator suites, and manifests together | Every production result can be tied to an exact deployable version |
| Dataset lineage | Source trace IDs, transformations, labels, version graph, privacy status, export format | A training/evaluation example can be traced back to its origin and policy state |
| Replay and simulation | Historical traffic replay, deterministic fixtures, candidate routing, failure delta, and cost delta | Candidate deployment produces a report before it receives live traffic |
| Release gates | Minimum quality, maximum failure delta, safety block thresholds, cost budgets, and approval requirements | A failing candidate cannot be promoted automatically |
| Canary and rollback | Percentage-based traffic, shadow mode, health window, automatic rollback, and immutable release records | A degraded candidate is rolled back without manual database edits |
| Root-cause analysis | Cluster failures by prompt, model, tool, policy, document, connector, and code version | Operator gets likely cause rather than a raw list of failed traces |
| Business outcome feedback | Outcome API, labels such as resolved/rejected/escalated, delayed outcome updates, and cohort metrics | Helios can optimize for task success instead of only text similarity |

## P1 — Developer experience and ecosystem

| Feature | Technical requirements | Acceptance test |
| --- | --- | --- |
| Python SDK | Typed client, streaming, retries, traces, tools, sessions, async support, and stable versioning | Python developer can integrate in under 10 minutes |
| TypeScript SDK | Browser/server support, streaming events, tools, sessions, and type-safe schemas | TypeScript developer can use the same contract without raw HTTP code |
| OpenAI SDK drop-in | Compatible base URL, auth, chat/responses, streaming, tools, usage, and errors | Existing app works with minimal configuration changes |
| CLI installer | `curl`/package install, setup wizard, update, doctor, login, gateway add, and shell completions | New user reaches a working local agent in one short setup flow |
| Config profiles | Named environments, project profiles, default gateway/model, env overrides, and secure auth references | Switching between local, team, and production profiles is one command |
| Plugin system | Versioned manifests, capability declarations, signed or trusted sources, install/uninstall, and compatibility checks | A community tool can be installed without modifying Helios core |
| Skills system | Markdown or structured skill files, versioning, scoped tools, tests, and sharing | Users can create reusable skills without copying hidden system prompts |
| Framework adapters | LangChain, LangGraph, PydanticAI, CrewAI, OpenAI Agents SDK, Vercel AI SDK, and custom Python/TypeScript | External agents can emit traces and use governance through standard adapters |
| MCP ecosystem | MCP server discovery, auth, tool filtering, resource tracing, and policy packs | A customer can connect existing MCP infrastructure safely |
| Webhooks and events | Run lifecycle events, approval events, alerts, retries, signatures, and replay | Customers can integrate Helios into ticketing and incident systems |
| Documentation playground | Copy-paste examples, generated API docs, gateway recipes, and local demo project | A developer can reproduce a complete governed workflow from the docs |

## P1 — Persistence, reliability, and operational scale

The current PostgreSQL queue is a reasonable MVP choice. Do not replace it merely because Kafka or ClickHouse sounds more scalable. First create stable interfaces and instrument the thresholds that justify a migration.

| Feature | Technical requirements | Acceptance test |
| --- | --- | --- |
| Database migrations | Alembic, forward/backward strategy, migration tests, and startup checks | Production schema changes are repeatable and reversible |
| Outbox/event boundary | Transactional event records, delivery status, retry, dedupe, and versioned envelopes | Trace and job events cannot be lost between database commit and worker processing |
| Durable workflow layer | Run state, leases, timers, retries, signals, cancellation, and recovery after worker loss | A long-running agent resumes from durable state after process failure |
| Queue isolation | Separate latency-sensitive gateway work from evaluation, indexing, browser, and batch jobs | Heavy evaluation traffic cannot block user completions |
| Object storage | Raw prompts, outputs, artifacts, files, screenshots, and exports with encryption and retention | Large payloads do not overload the transactional database |
| Analytical storage | Columnar event store, partitions, retention tiers, pre-aggregations, and query quotas | Trace analytics remain fast as event volume grows |
| Caching | Response cache, semantic cache, prompt cache, invalidation, tenant namespaces, and safety rules | Cache never returns data across tenants or bypasses a required policy check |
| Rate limiting | Organization/project/user/provider/model limits, burst controls, concurrency limits, and 429 metadata | One tenant or runaway agent cannot exhaust shared capacity |
| SLO instrumentation | Availability, p50/p95/p99 latency, queue lag, evaluation freshness, error budgets, and alerts | Operators can see whether Helios is meeting a published service target |
| Backups and recovery | Automated backups, point-in-time recovery, object versioning, restore tests, and RPO/RTO documentation | A restore drill produces a functioning tenant and verified trace set |
| Horizontal scaling | Stateless API, worker autoscaling, connection pooling, backpressure, and load tests | Adding workers increases throughput without duplicate execution |
| Deployment packaging | Docker Compose for local, Helm/Terraform or managed containers for production, health checks, and zero-downtime rollout | A design partner can deploy Helios reproducibly in its environment |

## P1 — Agent memory and knowledge

The current RAG and knowledge graph are useful foundations, but the product needs a clearer memory model.

| Feature | Technical requirements | Acceptance test |
| --- | --- | --- |
| Working memory | Per-run context, scratchpad, tool results, and automatic compaction | Agent can manage a multi-step task without polluting long-term memory |
| Episodic memory | User/project/session memories, provenance, confidence, expiration, and deletion | Agent remembers a preference only when it was explicitly authorized to do so |
| Organizational memory | Shared policies, runbooks, approved patterns, and access scopes | Team knowledge is reusable without leaking private user context |
| Memory controls | Inspect, edit, delete, pin, export, disable, and retention policy | User can see why a memory exists and remove it completely |
| Hybrid retrieval | Lexical, semantic, metadata, graph, freshness, and reranking signals | Search finds exact identifiers and semantically related content reliably |
| Citation integrity | Source spans, document version, retrieval score, freshness, and citation validation | Every grounded answer points to the exact source evidence used |
| Ingestion pipeline | Files, URLs, repositories, tickets, databases, incremental sync, OCR, chunking, and dedupe | A changed source updates only affected content and preserves lineage |
| Knowledge-graph evolution | Entity extraction, relation confidence, temporal validity, provenance, and graph queries | Graph facts can be inspected and retracted when source evidence changes |

## P1 — Team control plane

| Feature | Technical requirements | Acceptance test |
| --- | --- | --- |
| Organizations and projects | Hierarchy, environments, ownership, quotas, and resource inheritance | A team can separate development, staging, and production policies |
| Agent registry | Agent manifests, versions, owners, dependencies, health, and deployment state | An operator can list all running agent versions and owners |
| Model registry | Provider/model records, pricing, capabilities, privacy, regions, and verification state | A model is approved once and reused consistently across projects |
| Prompt registry | Versioned templates, variables, tests, approvals, rollback, and deployment references | Prompt changes are reviewed like code |
| Policy registry | Versioned policy packs, dry run, rollout, exceptions, and audit | A policy can be rolled out to 1% of traffic before full activation |
| Environment promotion | Dev → staging → production, approval gates, config diffs, and rollback | An agent cannot skip required evaluation or security checks |
| Cost center controls | Budgets, alerts, quotas, attribution, forecasts, and chargeback exports | Finance or platform teams can attribute usage to projects and agents |
| Review workbench | Queues, filters, assignment, SLA, adjudication, and dataset export | Reviewers can process failures efficiently and feed labels back to evals |

## P2 — Enterprise and very-large-scale capabilities

Build these when customer demand or volume justifies them. They are important for enterprise expansion but should not delay the first compelling product.

| Feature | Technical requirements |
| --- | --- |
| SAML SSO and SCIM | Enterprise identity provisioning, group mapping, deprovisioning, and audit |
| Customer-managed keys | KMS integration, envelope encryption, rotation, key access audit, and tenant isolation |
| Regional data planes | Data residency, routing by region, regional failover, and cross-region policy constraints |
| Private networking | VPC/VNet deployment, private endpoints, egress control, and service-to-service identity |
| Multi-region control plane | Replication, conflict handling, regional availability, disaster recovery, and failover testing |
| Enterprise deployment | Helm, Terraform, air-gapped installation, upgrade controller, and support diagnostics |
| Formal compliance evidence | SOC 2/ISO control evidence, vendor-risk package, data-processing documentation, and audit exports |
| Advanced data loss prevention | Classifiers, tokenization vaults, document-level entitlements, and policy simulation at scale |
| High-volume event architecture | Kafka or equivalent streaming, ClickHouse or equivalent analytics, partitioning, and replay |
| Durable exactly-once business effects | Effect journals, idempotent connectors, compensation workflows, and connector-specific semantics |
| A2A and agent interoperability | Agent discovery, capability negotiation, identity, task delegation, authorization, and trace propagation |
| Marketplace | Signed skills, tools, connectors, policies, evaluator packs, version compatibility, and billing |
| Fine-tuning and trajectory platform | Privacy-approved export, trajectory filtering, annotation, batch generation, training integration, and evaluation |
| Autonomous operations | Failure clustering, suggested policy changes, evaluator generation, and human-approved remediation |

## Defer until after real usage

| Feature | Reason to defer |
| --- | --- |
| Full no-code agent builder | It can distract from proving the terminal-first developer wedge |
| Dozens of shallow integrations | A few secure, deeply traced integrations are more valuable |
| Neo4j migration | The graph should earn its infrastructure through demonstrated product value |
| Kafka and ClickHouse on day one | Introduce them only after measured event throughput or query pressure requires it |
| Fine-tuning platform | First prove that customers want Helios-generated data and that evaluations improve outcomes |
| Custom model hosting | Model serving is a separate capital-intensive business unless it directly supports the wedge |
| Generic workflow automation suite | Competes with mature products before Helios has a clear advantage |
| Full mobile app | It is less important than a great terminal and durable background runs |

## Recommended build order

### Milestone 1: Competitive developer preview

Build streaming, sessions, context compaction, tool calls, safe shell/file tools, approvals, cancellation, OpenAI SDK compatibility, dynamic model discovery, custom gateway profiles, and a stable CLI installer. The output should be a developer who can use Helios every day for repository and operations work.

### Milestone 2: Safe agent runtime

Add the tool broker, permission manifests, MCP, browser isolation, background runs, sub-agents, idempotency, and parent-child traces. The output should be an agent that can act, not only answer, without becoming a security liability.

### Milestone 3: Quality and release system

Add OTel ingestion, hierarchical traces, judge and code evaluators, human annotation, dataset lineage, replay, simulation, release gates, canary, rollback, and business outcome feedback. The output should be a measurable improvement loop.

### Milestone 4: Team-ready platform

Add OIDC, RBAC/ABAC, audit logs, secrets, budgets, environments, registries, retention, deletion, backups, and deployment packaging. The output should be a platform a small team can trust with production workflows.

### Milestone 5: Scale and enterprise expansion

Add object and analytical storage, durable workflows, regional data planes, private networking, customer keys, formal compliance evidence, and high-volume event infrastructure only when design partners require them.

## The minimum genuinely competitive feature set

If resources are limited, build these **15 capabilities before expanding anything else**:

1. Streaming terminal interaction.
2. Persistent resumable sessions.
3. Tool calls with a central permissioned broker.
4. Safe shell, file, Git, browser, and MCP tools.
5. Human approval for risky actions.
6. Cancellation, retry, idempotency, and durable background runs.
7. OpenAI Chat Completions and Responses compatibility.
8. Dynamic model discovery and custom gateway profiles.
9. Capability-aware routing, fallback, health checks, and cost limits.
10. OTel-compatible hierarchical traces.
11. LLM-as-judge plus deterministic and human evaluators.
12. Dataset lineage, replay, simulation, canary, and rollback.
13. OIDC, RBAC/ABAC, immutable audit logs, and secret management.
14. Python/TypeScript SDKs, CLI installer, skills, and MCP support.
15. A repeatable local-to-production deployment path.

This is the smallest set that turns Helios from an impressive AI gateway into a **daily-use agent product with a defensible governance and reliability layer**.

## References

1. [Hermes Agent — Open-Source AI Agent with Persistent Memory](https://hermes-agent.org/)
2. [Langfuse — Open Source Agent Evals & Observability](https://langfuse.com/)
3. [Portkey — Production Stack for Gen AI Builders](https://portkey.ai/)
4. [LangSmith — AI Agent & LLM Observability and Evals Platform](https://www.langchain.com/langsmith-platform)
5. [LiteLLM Providers](https://docs.litellm.ai/docs/providers)
6. [OpenRouter Quickstart Guide](https://openrouter.ai/docs/quickstart)

[1]: https://hermes-agent.org/ "Hermes Agent — Open-Source AI Agent with Persistent Memory"
[2]: https://langfuse.com/ "Langfuse — Open Source Agent Evals & Observability"
[3]: https://portkey.ai/ "Portkey — Production Stack for Gen AI Builders"
[4]: https://www.langchain.com/langsmith-platform "LangSmith — AI Agent & LLM Observability and Evals Platform"
[5]: https://docs.litellm.ai/docs/providers "LiteLLM Providers"
[6]: https://openrouter.ai/docs/quickstart "OpenRouter Quickstart Guide"
