# HELIOS V1.5 Governance Roadmap

Product convergence, not a rewrite: V1's broker / policy / trace / approval /
replay primitives become the enforcement and evidence layer of a four-plane
AI governance control plane.

## Phases

| Phase | Deliverable | Builds on |
|---|---|---|
| 1 | Architecture map + this roadmap | V1 audit |
| 2 | AI System Registry (`/v1/systems`): identity, owner, purpose, lifecycle, autonomy L0–L5, risk class, versioned revisions; session binding | AgentSession, tenancy |
| 3 | Model Registry (`/v1/models`): approval lifecycle, allowed data classes/environments; `evaluate_model_use()` in the runtime; governance events on change | providers/registry, TraceEvent |
| 4 | Policy expansion: match autonomy level, data classes, model/provider, system risk; effects REQUIRE_HUMAN_REVIEW, BLOCK_DEPLOYMENT | broker/policy |
| 5 | Data classification (Sentinel-backed, deterministic) + `data_access` events + real-event lineage (`/v1/systems/{id}/lineage`) | sentinel, TraceEvent |
| 6 | Normalized Decision Records (`/v1/decisions`) + WHY explanations from recorded state | broker, TraceEvent |
| 7 | Human oversight expansion: approval expiry, comments, oversight report (involved / should-have-been / bypass detection) | ApprovalRequest |
| 8 | Governance evaluations + transparent governance score (`/v1/systems/{id}/governance`) | TraceEvent, DecisionRecord |
| 9 | AI Change Management (`/v1/changes`): candidate → replay evidence → review → deploy → rollback | replay, policy registry |
| 10 | Drift detection (`/v1/drift`): stored baselines, documented deterministic thresholds | DecisionRecord |
| 11 | TUI governance views: /systems /models /decisions /changes /drift /audit /governance | tui |
| 12 | Flagship E2E: register release-agent (L3, production) → GitHub flow → CRITICAL → approval → decision records → replay comparison → dashboard | everything |
| 13 | README repositioning + hardening + full test pass | — |

## Migrations required

- New tables: `ai_systems`, `ai_models`, `decision_records`, `change_records`,
  `governance_baselines`, `governance_evaluations` — created by `create_all`
  (additive, no risk to existing data).
- New **nullable** columns on `agent_sessions` (`system_id`) — additive.
  `create_all` does not ALTER existing tables; fresh installs are unaffected,
  existing V1 dev databases need `helios down && rm ~/.helios/helios.sqlite3`
  or a manual `ALTER TABLE agent_sessions ADD COLUMN system_id VARCHAR(36)`.
  (Alembic remains the documented path for real deployments.)
- `TraceEvent` is NOT altered — system attribution flows through
  session→system joins and DecisionRecords.

## Compatibility risks (and mitigations)

1. **Policy engine extension** — new match keys are optional; existing rule
   documents behave identically (verified by the V1 policy tests).
2. **InvocationContext extension** — new fields (`system_id`,
   `autonomy_level`) default to safe values; recorded V1 traces replay
   unchanged via `from_dict` defaults.
3. **Runtime model-governance gate** — only enforced when the session is
   bound to a registered system (unbound sessions keep V1 behavior), so all
   existing runtime tests pass without modification.
4. **Approval expiry** — only enforced when an approval carries an
   `expires_at`; V1 approvals (no expiry) are unaffected.
5. **Autonomy levels** — the string `autonomy` field remains; L0–L5 is
   carried alongside and derived both ways (`autonomous` ⇔ L≥3 default).

## Explicitly out of scope for V1.5

Provider catalog growth, domain workspaces, knowledge-graph expansion,
Kubernetes/Kafka/ClickHouse/SCIM, multi-region, browser automation,
self-evolution as a feature, legal-compliance certification.
