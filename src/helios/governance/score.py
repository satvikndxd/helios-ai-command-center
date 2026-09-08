"""
ASSURANCE PLANE — the governance score.

A transparent, weighted set of checks. Every check reports a status
(pass/warn/fail), the concrete fact behind it, and its weight. The overall
score is the weighted average of check scores — no hidden vanity number;
you can always see exactly which checks moved it and why.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.governance.evaluation import evaluate_system
from helios.governance.systems import get_system
from helios.models import AIModel, DecisionRecord, GovernanceBaseline


# (key, label, weight)
CHECKS = [
    ("identity", "Identity", 10),
    ("model_approval", "Model approval", 12),
    ("data_policy", "Data policy", 12),
    ("human_oversight", "Human oversight", 14),
    ("policy_compliance", "Policy compliance", 16),
    ("evaluation", "Evaluation", 12),
    ("auditability", "Auditability", 10),
    ("security", "Security", 8),
    ("drift", "Drift", 6),
]

STATUS_SCORE = {"pass": 1.0, "warn": 0.5, "fail": 0.0, "na": 1.0}


def _check(status: str, detail: str) -> dict:
    return {"status": status, "detail": detail, "score": STATUS_SCORE[status]}


def governance_score(db: Session, tenant_id: str, system_id: str) -> dict:
    system = get_system(db, tenant_id, system_id)
    if system is None:
        raise KeyError(f"system '{system_id}' not found")

    evaluation = evaluate_system(db, tenant_id, system_id)
    metrics = evaluation["metrics"]
    counters = evaluation["counters"]
    results: dict[str, dict] = {}

    # identity: owner assigned, purpose stated, lifecycle active
    if system.owner and system.owner != "unassigned" and system.purpose:
        results["identity"] = _check("pass",
            f"owner {system.owner}, purpose set, lifecycle {system.lifecycle}")
    elif system.owner and system.owner != "unassigned":
        results["identity"] = _check("warn", "owner set but purpose missing")
    else:
        results["identity"] = _check("fail", "no owner assigned")

    # model approval: every model the system declares is registered+approved
    declared = system.models or []
    if not declared:
        results["model_approval"] = _check("warn", "no models declared on the system")
    else:
        unapproved = []
        for m in declared:
            provider = m.get("provider") if isinstance(m, dict) else None
            model_id = m.get("model_id") if isinstance(m, dict) else str(m)
            row = (db.query(AIModel)
                   .filter(AIModel.tenant_id == tenant_id,
                           AIModel.model_id == model_id,
                           AIModel.status == "approved")
                   .first())
            if row is None:
                unapproved.append(model_id)
        if unapproved:
            results["model_approval"] = _check("fail",
                f"declared models not approved: {unapproved}")
        else:
            results["model_approval"] = _check("pass",
                f"all {len(declared)} declared models approved")

    # data policy: model-governance compliance from real decisions
    mgc = metrics["model_governance_compliance"]
    results["data_policy"] = _check(
        "pass" if mgc >= 0.99 else "warn" if mgc >= 0.8 else "fail",
        f"model-use compliance {mgc:.0%} over {counters['model_uses']} model uses")

    # human oversight: no bypasses; required approvals honored
    if counters["bypassed"] > 0:
        results["human_oversight"] = _check("fail",
            f"{counters['bypassed']} oversight bypass(es) detected")
    else:
        oc = metrics["oversight_compliance"]
        results["human_oversight"] = _check(
            "pass" if oc >= 0.99 else "warn",
            f"oversight compliance {oc:.0%}; no bypasses")

    # policy compliance
    pc = metrics["policy_compliance"]
    results["policy_compliance"] = _check(
        "pass" if pc >= 0.95 else "warn" if pc >= 0.75 else "fail",
        f"{pc:.0%} of {counters['decisions']} decisions within policy "
        f"({counters['denied']} denied)")

    # evaluation coverage: is there recorded behavior to evaluate at all?
    if counters["decisions"] == 0:
        results["evaluation"] = _check("warn", "no recorded decisions to evaluate yet")
    else:
        ts = metrics["task_success"]
        results["evaluation"] = _check(
            "pass" if ts >= 0.8 else "warn",
            f"task success {ts:.0%}; PII protection "
            f"{metrics['pii_protection']:.0%}")

    # auditability: every decision has a trace + policy attribution
    if counters["decisions"] == 0:
        results["auditability"] = _check("warn", "no decisions recorded yet")
    else:
        traced = (db.query(DecisionRecord)
                  .filter(DecisionRecord.tenant_id == tenant_id,
                          DecisionRecord.system_id == system_id,
                          DecisionRecord.trace_event_id.isnot(None))
                  .count())
        rate = traced / counters["decisions"]
        results["auditability"] = _check(
            "pass" if rate >= 0.99 else "warn",
            f"{traced}/{counters['decisions']} decisions carry a trace lineage")

    # security: tool-misuse clean rate
    tm = metrics["tool_misuse_clean_rate"]
    results["security"] = _check(
        "pass" if tm >= 0.9 else "warn" if tm >= 0.6 else "fail",
        f"tool clean-rate {tm:.0%} over {counters['tool_calls']} tool calls")

    # drift: is there an active baseline and are we within it?
    baseline = (db.query(GovernanceBaseline)
                .filter(GovernanceBaseline.tenant_id == tenant_id,
                        GovernanceBaseline.system_id == system_id,
                        GovernanceBaseline.active.is_(True))
                .first())
    if baseline is None:
        results["drift"] = _check("warn", "no baseline captured — drift not monitored")
    else:
        from helios.governance.drift import detect_drift
        drift = detect_drift(db, tenant_id, system_id)
        if drift["drift_detected"]:
            results["drift"] = _check("fail",
                f"{len(drift['signals'])} drift signal(s): "
                + "; ".join(s["metric"] for s in drift["signals"]))
        else:
            results["drift"] = _check("pass", "within baseline thresholds")

    # weighted overall
    total_weight = sum(w for _, _, w in CHECKS)
    weighted = 0.0
    checks_out = []
    for key, label, weight in CHECKS:
        result = results.get(key, _check("na", "not evaluated"))
        weighted += result["score"] * weight
        checks_out.append({
            "key": key, "label": label, "weight": weight,
            "status": result["status"], "detail": result["detail"],
        })
    overall = round(100 * weighted / total_weight)

    return {
        "system_id": system_id,
        "overall": overall,
        "checks": checks_out,
        "metrics": metrics,
        "counters": counters,
        "explanation": "weighted average of per-check scores; "
                       "pass=1.0 warn=0.5 fail=0.0",
    }
