"""
ASSURANCE PLANE — governance drift detection.

Deterministic, threshold-based comparison of recent behavior against a
stored baseline. No ML, no fake anomaly detection: documented thresholds,
explainable signals. More advanced detectors can slot in behind the same
metrics dict later.

Baseline metrics captured per system:
  * approval_rate    — fraction of write/execute decisions that required a human
  * denial_rate      — fraction of decisions denied
  * tools            — the set of tools the system used
  * models           — the set of models the system used
  * autonomy_level   — the system's autonomy level at baseline time
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.governance.systems import get_system
from helios.models import DecisionRecord, GovernanceBaseline


# Documented thresholds (absolute fractional change unless noted).
THRESHOLDS = {
    "approval_rate": 0.20,   # a 20-point drop in approval rate is a signal
    "denial_rate": 0.20,
}


def _behavior_metrics(db: Session, tenant_id: str, system_id: str) -> dict:
    records = (
        db.query(DecisionRecord)
        .filter(DecisionRecord.tenant_id == tenant_id,
                DecisionRecord.system_id == system_id)
        .all()
    )
    effectful = [r for r in records if r.kind == "tool_call"]
    approvals = sum(1 for r in effectful
                    if r.human_oversight in ("required", "approved", "denied", "bypassed"))
    denied = sum(1 for r in records if r.decision == "deny")
    tools = sorted({r.action for r in effectful})
    models = sorted({f"{r.model_provider}/{r.model_id}"
                     for r in records if r.kind == "model_use" and r.model_provider})
    return {
        "approval_rate": round(approvals / len(effectful), 4) if effectful else 0.0,
        "denial_rate": round(denied / len(records), 4) if records else 0.0,
        "tools": tools,
        "models": models,
        "sample_size": len(records),
    }


def capture_baseline(db: Session, tenant_id: str, system_id: str) -> GovernanceBaseline:
    """Snapshot current behavior as the active baseline (deactivates prior)."""
    system = get_system(db, tenant_id, system_id)
    metrics = _behavior_metrics(db, tenant_id, system_id)
    metrics["autonomy_level"] = system.autonomy_level if system else None

    db.query(GovernanceBaseline).filter(
        GovernanceBaseline.tenant_id == tenant_id,
        GovernanceBaseline.system_id == system_id,
        GovernanceBaseline.active.is_(True),
    ).update({"active": False})

    baseline = GovernanceBaseline(
        tenant_id=tenant_id, system_id=system_id, metrics=metrics,
        sample_size=metrics["sample_size"], active=True,
    )
    db.add(baseline)
    db.commit()
    return baseline


def detect_drift(db: Session, tenant_id: str, system_id: str) -> dict:
    """Compare current behavior to the active baseline. Deterministic."""
    baseline = (
        db.query(GovernanceBaseline)
        .filter(GovernanceBaseline.tenant_id == tenant_id,
                GovernanceBaseline.system_id == system_id,
                GovernanceBaseline.active.is_(True))
        .first()
    )
    if baseline is None:
        return {"system_id": system_id, "drift_detected": False,
                "signals": [], "note": "no baseline captured"}

    base = baseline.metrics or {}
    current = _behavior_metrics(db, tenant_id, system_id)
    signals: list[dict] = []

    for metric, threshold in THRESHOLDS.items():
        base_v = base.get(metric, 0.0)
        cur_v = current.get(metric, 0.0)
        delta = cur_v - base_v
        # A DROP in approval rate or a RISE in denial rate is what we flag.
        flagged = (metric == "approval_rate" and delta <= -threshold) or \
                  (metric == "denial_rate" and delta >= threshold)
        if flagged:
            signals.append({
                "metric": metric,
                "baseline": base_v,
                "observed": cur_v,
                "delta": round(delta, 4),
                "threshold": threshold,
                "detail": f"{metric} moved from {base_v:.0%} to {cur_v:.0%} "
                          f"(Δ{delta:+.0%}, threshold {threshold:.0%})",
            })

    # categorical drift: new tools / models not present at baseline
    new_tools = sorted(set(current["tools"]) - set(base.get("tools", [])))
    if new_tools:
        signals.append({
            "metric": "tools", "baseline": base.get("tools", []),
            "observed": current["tools"], "delta": new_tools,
            "detail": f"new tools since baseline: {new_tools}",
        })
    new_models = sorted(set(current["models"]) - set(base.get("models", [])))
    if new_models:
        signals.append({
            "metric": "models", "baseline": base.get("models", []),
            "observed": current["models"], "delta": new_models,
            "detail": f"new models since baseline: {new_models}",
        })

    # autonomy drift
    system = get_system(db, tenant_id, system_id)
    base_autonomy = base.get("autonomy_level")
    if system is not None and base_autonomy is not None \
            and system.autonomy_level > base_autonomy:
        signals.append({
            "metric": "autonomy_level", "baseline": base_autonomy,
            "observed": system.autonomy_level,
            "delta": system.autonomy_level - base_autonomy,
            "detail": f"autonomy raised from L{base_autonomy} to "
                      f"L{system.autonomy_level}",
        })

    return {
        "system_id": system_id,
        "drift_detected": bool(signals),
        "signals": signals,
        "baseline": {"metrics": base, "sample_size": baseline.sample_size,
                     "captured_at": baseline.created_at.isoformat()
                     if baseline.created_at else None},
        "current": current,
        "thresholds": THRESHOLDS,
    }
