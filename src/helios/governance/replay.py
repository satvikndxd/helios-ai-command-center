"""
System-level replay (ASSURANCE plane).

Extends the V1 run replay (`agent/replay.py`, which stays untouched for
single-run tool-policy replay) to a whole AI system:

    replay every recorded proposal + model preflight of the system's runs
    against CANDIDATE state — candidate tool policy, candidate governance
    policy set, candidate system posture (autonomy/environment), candidate
    model governance — and diff the outcomes:

        ORIGINAL                     CANDIDATE
        14 executed                  11 executed
        2 approvals                  4 approvals
        0 policy violations          0 policy violations
        +2 newly gated               -3 risky autonomous actions allowed

Nothing executes during replay; it is pure re-evaluation of recorded
evidence, which makes policy/model/posture changes testable BEFORE
deployment. Every diff cites the proposal/event it came from.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.agent.replay import replay_run
from helios.broker.types import InvocationContext
from helios.governance import engine as governance_engine
from helios.governance.policy import GovernancePolicySet
from helios.models import AgentRun, AgentSession, TraceEvent


def _recorded_governance(db: Session, proposal: TraceEvent) -> dict | None:
    child = (
        db.query(TraceEvent)
        .filter(TraceEvent.parent_id == proposal.id,
                TraceEvent.event_type == "governance_evaluation")
        .first()
    )
    return (child.payload or {}) if child else None


def _recorded_risk(db: Session, proposal: TraceEvent) -> str:
    child = (
        db.query(TraceEvent)
        .filter(TraceEvent.parent_id == proposal.id,
                TraceEvent.event_type == "risk_evaluation")
        .first()
    )
    return ((child.payload or {}).get("risk") if child else None) or "low"


def replay_system(
    db: Session,
    tenant_id: str,
    system_id: str,
    *,
    candidate_tool_policy: dict | None = None,
    candidate_governance_set: dict | None = None,
    candidate_system: dict | None = None,
    candidate_model: dict | None = None,
    run_limit: int = 25,
) -> dict:
    """
    Re-evaluate a system's recorded runs against candidate governance state.

    candidate_tool_policy     ToolPolicy document ({version, rules, ...})
    candidate_governance_set  GovernancePolicySet document (evaluated IN
                              ADDITION to the active sets — the realistic
                              "what would this new policy do" question)
    candidate_system          posture overrides {"autonomy_level", "environment"}
    candidate_model           model-registry overrides applied to recorded
                              preflights {"allowed_data_classes",
                              "allowed_environments", "approval_status"}
    """
    from helios.governance.systems import get_system

    system = get_system(db, tenant_id, system_id)
    posture = {
        "autonomy_level": (candidate_system or {}).get(
            "autonomy_level", system.autonomy_level if system else None),
        "environment": (candidate_system or {}).get(
            "environment", system.environment if system else None),
    }

    extra_sets = ([GovernancePolicySet.from_dict(candidate_governance_set)]
                  if candidate_governance_set else None)

    runs = (
        db.query(AgentRun)
        .join(AgentSession, AgentRun.session_id == AgentSession.id)
        .filter(AgentSession.tenant_id == tenant_id,
                AgentSession.system_id == system_id)
        .order_by(AgentRun.created_at.desc())
        .limit(run_limit)
        .all()
    )

    tool_original: dict[str, int] = {}
    tool_candidate: dict[str, int] = {}
    gov_original: dict[str, int] = {}
    gov_candidate: dict[str, int] = {}
    tool_changes: list[dict] = []
    governance_changes: list[dict] = []
    per_run = []

    for run in runs:
        session = db.get(AgentSession, run.session_id)

        # --- tool-policy layer (V1 replay machinery) -----------------------
        run_report = None
        if candidate_tool_policy is not None:
            run_report = replay_run(db, run, session,
                                    policy_doc=candidate_tool_policy)
            for key, value in run_report["original"].items():
                tool_original[key] = tool_original.get(key, 0) + value
            for key, value in run_report["candidate"].items():
                tool_candidate[key] = tool_candidate.get(key, 0) + value
            tool_changes.extend(dict(c, run_id=run.id)
                                for c in run_report["changes"])
        else:
            # count recorded outcomes so the report always has a baseline
            for key, value in _recorded_tool_counts(db, run).items():
                tool_original[key] = tool_original.get(key, 0) + value
                tool_candidate[key] = tool_candidate.get(key, 0) + value

        # --- governance layer over recorded proposals -----------------------
        proposals = (
            db.query(TraceEvent)
            .filter(TraceEvent.run_id == run.id,
                    TraceEvent.event_type == "tool_proposal")
            .order_by(TraceEvent.seq)
            .all()
        )
        for proposal in proposals:
            payload = proposal.payload or {}
            recorded = _recorded_governance(db, proposal)
            if recorded is None:
                continue  # legacy/unbound evidence — nothing to compare
            original_decision = recorded.get("decision", "allow")
            context = InvocationContext.from_dict(payload.get("context") or {})
            manifest = None
            risk = _recorded_risk(db, proposal)

            from helios.broker.registry import default_registry
            tool = default_registry().get(payload.get("tool") or proposal.name)
            capability = tool.manifest.capability if tool else "read"

            decision = governance_engine.check_tool_action(
                db, tenant_id,
                system=system,
                tool=payload.get("tool") or proposal.name,
                capability=capability,
                action_risk=risk,
                environment=posture["environment"] or context.environment,
                autonomy_level=posture["autonomy_level"],
                data_class=(payload.get("data_classification") or {}).get("effective"),
                extra_sets=extra_sets,
            )
            candidate_decision = decision.decision
            gov_original[original_decision] = gov_original.get(original_decision, 0) + 1
            gov_candidate[candidate_decision] = gov_candidate.get(candidate_decision, 0) + 1
            if candidate_decision != original_decision:
                governance_changes.append({
                    "run_id": run.id,
                    "event_id": proposal.id,
                    "tool": payload.get("tool") or proposal.name,
                    "original": original_decision,
                    "candidate": candidate_decision,
                    "candidate_reason": decision.reason,
                    "candidate_rules": decision.matched_rules,
                })

        # --- model preflights under candidate model governance ---------------
        preflights = (
            db.query(TraceEvent)
            .filter(TraceEvent.run_id == run.id,
                    TraceEvent.event_type == "policy_evaluation",
                    TraceEvent.name.like("model_preflight:%"))
            .all()
        )
        for event in preflights:
            payload = event.payload or {}
            original_decision = payload.get("decision")
            if candidate_model is not None and original_decision is not None:
                from helios.governance import models_registry

                model_info = payload.get("model") or {}
                simulated = models_registry.check_model_usage(
                    db, tenant_id,
                    model_info.get("provider", "?"), model_info.get("model_id", "?"),
                    version=model_info.get("version"),
                    data_classes=[(payload.get("data_classification") or {})
                                  .get("effective")]
                    if (payload.get("data_classification") or {}).get("effective")
                    else [],
                    environment=posture["environment"] or payload.get("environment"),
                    governed=True,
                    asset_override=_simulate_asset(db, tenant_id, model_info,
                                                   candidate_model),
                )
                candidate_decision = simulated["decision"]
                if candidate_decision != original_decision:
                    governance_changes.append({
                        "run_id": run.id, "event_id": event.id,
                        "tool": f"model:{event.name}",
                        "original": original_decision,
                        "candidate": candidate_decision,
                        "candidate_reason": "; ".join(simulated["reasons"]),
                        "candidate_rules": [],
                    })

        per_run.append({
            "run_id": run.id,
            "state": run.state,
            "tool_replay": ({"original": run_report["original"],
                             "candidate": run_report["candidate"],
                             "changes": len(run_report["changes"])}
                            if run_report else None),
        })

    summary = _summarize(tool_original, tool_candidate,
                         gov_original, gov_candidate, governance_changes)
    return {
        "system_id": system_id,
        "runs_replayed": len(runs),
        "posture": posture,
        "candidates": {
            "tool_policy": (candidate_tool_policy or {}).get("version"),
            "governance_set": (candidate_governance_set or {}).get("name"),
            "system": candidate_system,
            "model": candidate_model,
        },
        "tool_layer": {"original": tool_original, "candidate": tool_candidate,
                       "changes": tool_changes[:200]},
        "governance_layer": {"original": gov_original, "candidate": gov_candidate,
                             "changes": governance_changes[:200]},
        "per_run": per_run,
        "summary": summary,
    }


def _simulate_asset(db: Session, tenant_id: str, model_info: dict,
                    overrides: dict):
    """Transient (unsaved) ModelAsset reflecting candidate registry state."""
    from helios.governance import models_registry
    from helios.models import ModelAsset

    base = models_registry.find_model(
        db, tenant_id, model_info.get("provider", ""),
        model_info.get("model_id", ""), version=model_info.get("version"))
    asset = ModelAsset(
        tenant_id=tenant_id,
        provider=model_info.get("provider", ""),
        model_id=model_info.get("model_id", ""),
        version=model_info.get("version") or "latest",
    )
    if base is not None:
        asset.approval_status = base.approval_status
        asset.allowed_data_classes = list(base.allowed_data_classes or [])
        asset.allowed_environments = list(base.allowed_environments or [])
        asset.approved_by = base.approved_by
        asset.deprecation_reason = base.deprecation_reason
    else:
        asset.approval_status = "not_approved"
    for key in ("approval_status", "allowed_data_classes",
                "allowed_environments"):
        if key in overrides:
            setattr(asset, key, overrides[key])
    return asset


def _recorded_tool_counts(db: Session, run: AgentRun) -> dict[str, int]:
    """Baseline outcome counts from the recorded evidence of one run."""
    from helios.agent.replay import _original_outcome

    counts: dict[str, int] = {}
    proposals = (
        db.query(TraceEvent)
        .filter(TraceEvent.run_id == run.id,
                TraceEvent.event_type == "tool_proposal")
        .all()
    )
    for proposal in proposals:
        outcome = _original_outcome(db, proposal)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def _summarize(tool_original, tool_candidate, gov_original, gov_candidate,
               governance_changes) -> dict:
    newly_blocked = [c for c in governance_changes
                     if c["candidate"] == "deny" and c["original"] != "deny"]
    newly_gated = [c for c in governance_changes
                   if c["candidate"] in ("require_approval", "require_human_review")
                   and c["original"] == "allow"]
    newly_allowed = [c for c in governance_changes
                     if c["candidate"] == "allow" and c["original"] != "allow"]
    return {
        "tool_layer": {
            "executed_delta": (tool_candidate.get("executed", 0)
                               - tool_original.get("executed", 0)),
            "denied_delta": (tool_candidate.get("denied", 0)
                             - tool_original.get("denied", 0)),
            "approval_delta": (tool_candidate.get("approval_required", 0)
                               - tool_original.get("approval_required", 0)),
        },
        "governance_layer": {
            "newly_blocked": len(newly_blocked),
            "newly_gated": len(newly_gated),
            "newly_allowed": len(newly_allowed),
        },
        "changed_decisions": len(governance_changes),
    }
