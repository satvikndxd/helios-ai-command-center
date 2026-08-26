"""
Self-evolution engine — the agent improves itself from production evidence,
but only through a governed gate.

The loop:

  1. MINE      failed / blocked / low-scoring traces and thumbs-down
               feedback from the DecisionTrace history.
  2. CLUSTER   them by failure signature (provider errors, refusals,
               hallucination risk, policy blocks, latency breaches).
  3. PROPOSE   typed changes — routing fallbacks, policy rules, generated
               evaluator patterns, prompt hints — each carrying its
               evidence (trace ids, counts) and validation metrics.
  4. GATE      a human approves or rejects; proposals never self-approve.
  5. APPLY     versioned, with the previous state captured for rollback.

This is "autonomous operations" from the enterprise checklist, shipped as a
walking skeleton: the mining, clustering, proposal, approval, versioning,
and rollback machinery is real; appliers write to the tenant's evolution
state (consumed by policy/routing as overrides) rather than mutating code.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from helios.models import DecisionTrace, EvolutionProposal

# Tenant-scoped runtime overrides produced by APPLIED proposals.
# Consumed by the router/policy/evaluator layers as configuration, kept
# in-process for the MVP (registry-backed on the enterprise track).
_EVOLUTION_STATE: dict[str, dict] = {}


def evolution_state(tenant_id: str) -> dict:
    return _EVOLUTION_STATE.setdefault(
        tenant_id,
        {"routing_fallbacks": [], "policy_rules": [], "evaluator_patterns": [],
         "prompt_hints": [], "applied_versions": []},
    )


# -- 1+2: mine and cluster -------------------------------------------------


def _signature(trace: DecisionTrace) -> str | None:
    """Classify one trace into a failure signature (None = healthy)."""
    if trace.status == "blocked":
        return "policy_block"
    if trace.status == "error":
        provider = trace.model_provider or "unknown"
        return f"provider_error:{provider}"
    scores = trace.evaluation_scores or {}
    if scores.get("refusal", {}).get("passed") is False:
        return "refusal"
    if scores.get("empty_output", {}).get("passed") is False:
        return "empty_output"
    if scores.get("latency_sla", {}).get("passed") is False:
        return "latency_breach"
    risk = scores.get("groundedness", {}).get("hallucination_risk")
    if isinstance(risk, (int, float)) and risk >= 0.5:
        return "hallucination_risk"
    feedback = trace.feedback or {}
    if feedback.get("thumbs") == "down":
        return "negative_feedback"
    return None


def mine_failures(db: Session, tenant_id: str, limit: int = 500) -> dict[str, list[DecisionTrace]]:
    traces = (
        db.query(DecisionTrace)
        .filter(DecisionTrace.tenant_id == tenant_id)
        .order_by(DecisionTrace.created_at.desc())
        .limit(limit)
        .all()
    )
    clusters: dict[str, list[DecisionTrace]] = defaultdict(list)
    for trace in traces:
        sig = _signature(trace)
        if sig:
            clusters[sig].append(trace)
    return dict(clusters)


# -- 3: typed proposals ----------------------------------------------------


def _proposal_for(signature: str, traces: list[DecisionTrace]) -> dict | None:
    evidence = {
        "signature": signature,
        "occurrences": len(traces),
        "trace_ids": [t.id for t in traces[:20]],
    }

    if signature.startswith("provider_error:"):
        provider = signature.split(":", 1)[1]
        return {
            "kind": "routing_fallback",
            "title": f"Demote provider '{provider}' after {len(traces)} failures",
            "change": {"demote_provider": provider, "prefer_fallback": True},
            "evidence": evidence,
        }
    if signature == "refusal":
        phrases = Counter()
        for t in traces:
            text = (t.output_text or "").lower()
            for phrase in ("i cannot", "i can't", "i am unable", "as an ai"):
                if phrase in text:
                    phrases[phrase] += 1
        top = [p for p, _ in phrases.most_common(3)]
        return {
            "kind": "evaluator_pattern",
            "title": f"Generate refusal evaluator patterns from {len(traces)} refusals",
            "change": {"add_refusal_patterns": top or ["i cannot"]},
            "evidence": evidence,
        }
    if signature == "hallucination_risk":
        return {
            "kind": "policy_rule",
            "title": "Require knowledge-base grounding for answer-style tasks",
            "change": {"require_grounding_for": ["answer", "completion"],
                       "min_groundedness": 0.5},
            "evidence": evidence,
        }
    if signature == "latency_breach":
        return {
            "kind": "routing_fallback",
            "title": f"Prefer lower-latency route after {len(traces)} SLA breaches",
            "change": {"prefer_low_latency": True},
            "evidence": evidence,
        }
    if signature == "negative_feedback":
        return {
            "kind": "prompt_hint",
            "title": f"Add corrective prompt hint from {len(traces)} thumbs-down",
            "change": {"hint": "Prefer cited, source-grounded answers; say so when unsure."},
            "evidence": evidence,
        }
    if signature == "policy_block":
        return {
            "kind": "policy_rule",
            "title": f"Review recurring policy blocks ({len(traces)}) for a preflight warning",
            "change": {"warn_before_block": True},
            "evidence": evidence,
        }
    return None


def analyze(db: Session, tenant_id: str, min_occurrences: int = 2) -> list[EvolutionProposal]:
    """
    Run one evolution analysis: mine -> cluster -> propose.

    Proposals are DEDUPED against open proposals with the same kind+title
    so repeated analysis converges instead of spamming the queue.
    """
    clusters = mine_failures(db, tenant_id)
    open_keys = {
        (p.kind, p.title)
        for p in db.query(EvolutionProposal)
        .filter(
            EvolutionProposal.tenant_id == tenant_id,
            EvolutionProposal.status.in_(["proposed", "approved", "applied"]),
        )
        .all()
    }

    created: list[EvolutionProposal] = []
    for signature, traces in clusters.items():
        if len(traces) < min_occurrences:
            continue
        spec = _proposal_for(signature, traces)
        if not spec or (spec["kind"], spec["title"]) in open_keys:
            continue
        proposal = EvolutionProposal(
            tenant_id=tenant_id,
            kind=spec["kind"],
            title=spec["title"],
            change=spec["change"],
            evidence=spec["evidence"],
            validation={
                "affected_traces": len(traces),
                "share_of_recent_traffic": round(
                    len(traces) / max(1, sum(len(v) for v in clusters.values())), 3
                ),
            },
        )
        db.add(proposal)
        created.append(proposal)
    db.commit()
    return created


# -- 4+5: human gate, versioned apply, rollback ----------------------------


def apply_proposal(db: Session, proposal: EvolutionProposal, decided_by: str) -> EvolutionProposal:
    """Human-approved apply: versioned, with previous state for rollback."""
    if proposal.status not in ("proposed", "approved"):
        raise ValueError(f"proposal is {proposal.status}; only proposed/approved can apply")

    state = evolution_state(proposal.tenant_id)
    proposal.previous_state = {k: list(v) if isinstance(v, list) else v for k, v in state.items()}

    bucket = {
        "routing_fallback": "routing_fallbacks",
        "policy_rule": "policy_rules",
        "evaluator_pattern": "evaluator_patterns",
        "prompt_hint": "prompt_hints",
    }[proposal.kind]
    state[bucket].append({"proposal_id": proposal.id, **proposal.change})

    proposal.version = len(state["applied_versions"]) + 1
    state["applied_versions"].append(proposal.id)

    proposal.status = "applied"
    proposal.decided_by = decided_by
    proposal.applied_at = datetime.now(timezone.utc)
    db.commit()
    return proposal


def rollback_proposal(db: Session, proposal: EvolutionProposal, decided_by: str) -> EvolutionProposal:
    if proposal.status != "applied":
        raise ValueError("only applied proposals can be rolled back")
    _EVOLUTION_STATE[proposal.tenant_id] = {
        k: list(v) if isinstance(v, list) else v
        for k, v in (proposal.previous_state or {}).items()
    } or evolution_state(proposal.tenant_id)
    proposal.status = "rolled_back"
    proposal.decided_by = decided_by
    db.commit()
    return proposal
