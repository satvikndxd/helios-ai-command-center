"""
Governance drift detection (ASSURANCE plane).

Deterministic statistical comparison of CURRENT governance metrics against a
stored baseline. No fake anomaly detection: every rule is a documented
threshold with a minimum sample size, and every signal carries the baseline
value, the observed value, and the rule that fired.

Metric collection (all from stored evidence):
    decisions, approval_rate, denial_rate, oversight_bypasses,
    risk_mix, tool_mix, model_mix, human_involvement_rate,
    avg_run_cost_usd, avg_run_latency_ms, avg_run_steps,
    eval_pass_rate, autonomy_level, environment, active_policy_versions,
    approved_models

Detection rules (thresholds are product policy, documented here and in the
signal detail; the detector interface allows richer detectors later):

    MIN_SAMPLE_DECISIONS = 20   below this, rate rules report
                                `insufficient_sample` and never fire
    approval_rate_drop        > 0.20 warning, > 0.40 critical
    denial_rate_rise          > 0.15 warning, > 0.30 critical
    oversight_bypass          any occurrence -> critical (zero tolerance)
    high_risk_share_rise      > 0.20 warning
    tool_drift                new tool share > 0.10 warning;
                              existing-tool share shift > 0.30 info
    model_drift               model absent from baseline mix -> warning;
                              model no longer approved -> critical
    autonomy_drift            autonomy level raised -> critical;
                              environment moved to production -> warning
    policy_drift              active policy version set changed -> info
    evaluation_regression     eval pass-rate drop > 0.10 -> warning
                              (requires both baseline and current evals)
    cost_drift                avg run cost > 2x baseline -> info (>=5 runs)

Re-detection upserts: one open signal per (system, rule), occurrences++,
last_seen refreshed. Acknowledged signals leave the open set; a fresh
detection after acknowledgment opens a NEW signal (ack is not a mute).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from helios.models import (
    AgentRun,
    AgentSession,
    DecisionRecord,
    DriftBaseline,
    DriftSignal,
    EvaluationRun,
)

MIN_SAMPLE_DECISIONS = 20
MIN_SAMPLE_RUNS = 5

APPROVAL_DROP_WARN = 0.20
APPROVAL_DROP_CRIT = 0.40
DENIAL_RISE_WARN = 0.15
DENIAL_RISE_CRIT = 0.30
HIGH_RISK_RISE_WARN = 0.20
NEW_TOOL_SHARE_WARN = 0.10
TOOL_SHIFT_INFO = 0.30
EVAL_REGRESSION_WARN = 0.10
COST_MULTIPLIER_INFO = 2.0

HIGH_RISKS = ("high", "critical")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ratio(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


# --- metric collection -----------------------------------------------------------


def collect_metrics(db: Session, tenant_id: str, system_id: str,
                    *, record_limit: int = 2000, run_limit: int = 200) -> dict:
    """Deterministic governance metrics over ALL stored evidence of a system."""
    from helios.governance import engine as governance_engine
    from helios.governance.systems import get_system

    records = (
        db.query(DecisionRecord)
        .filter(DecisionRecord.tenant_id == tenant_id,
                DecisionRecord.system_id == system_id,
                DecisionRecord.kind == "tool_action")
        .order_by(DecisionRecord.created_at.desc())
        .limit(record_limit)
        .all()
    )
    runs = (
        db.query(AgentRun)
        .join(AgentSession, AgentRun.session_id == AgentSession.id)
        .filter(AgentSession.tenant_id == tenant_id,
                AgentSession.system_id == system_id)
        .order_by(AgentRun.created_at.desc())
        .limit(run_limit)
        .all()
    )

    decisions = len(records)
    required = sum(1 for r in records if (r.oversight or {}).get("required"))
    involved = sum(1 for r in records
                   if (r.oversight or {}).get("actual") in
                   ("approved", "existing", "session", "payload_bound", "denied"))
    denied = sum(1 for r in records if r.decision == "DENIED")
    bypassed = sum(1 for r in records
                   if (r.oversight or {}).get("required")
                   and r.decision == "EXECUTED"
                   and (r.oversight or {}).get("actual") == "none")

    risk_counts: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    model_counts: dict[str, int] = {}
    for record in records:
        risk_counts[record.risk or "unknown"] = risk_counts.get(record.risk or "unknown", 0) + 1
        tool_counts[record.action] = tool_counts.get(record.action, 0) + 1
        if record.model:
            model_counts[record.model] = model_counts.get(record.model, 0) + 1

    high_risk = sum(risk_counts.get(r, 0) for r in HIGH_RISKS)

    evaluation = (
        db.query(EvaluationRun)
        .filter(EvaluationRun.tenant_id == tenant_id,
                EvaluationRun.system_id == system_id,
                EvaluationRun.kind == "governance",
                EvaluationRun.status == "completed")
        .order_by(EvaluationRun.created_at.desc())
        .first()
    )
    eval_pass_rate = None
    if evaluation is not None and evaluation.results:
        scored = [r for r in evaluation.results.values()
                  if r.get("details", {}).get("status") != "insufficient_evidence"]
        if scored:
            eval_pass_rate = round(
                sum(1 for r in scored if r.get("passed")) / len(scored), 4)

    system = get_system(db, tenant_id, system_id)
    active_versions = []
    if system is not None:
        active_versions = sorted(
            f"{s.name}@{s.version}"
            for s in governance_engine.active_policy_sets(db, tenant_id)
        )
    approved_models = []
    from helios.models import ModelAsset
    for asset in (
        db.query(ModelAsset)
        .filter(ModelAsset.tenant_id == tenant_id,
                ModelAsset.approval_status == "approved")
        .all()
    ):
        approved_models.append(f"{asset.provider}/{asset.model_id}")

    terminal_runs = [r for r in runs if r.state in
                     ("completed", "failed", "cancelled", "blocked")]
    return {
        "collected_at": _now().isoformat(),
        "decisions": decisions,
        "runs": len(runs),
        "terminal_runs": len(terminal_runs),
        "approval_rate": _ratio(required, decisions),
        "human_involvement_rate": _ratio(involved, decisions),
        "denial_rate": _ratio(denied, decisions),
        "oversight_bypasses": bypassed,
        "high_risk_share": _ratio(high_risk, decisions),
        "risk_mix": {k: _ratio(v, decisions) for k, v in sorted(risk_counts.items())},
        "tool_mix": {k: _ratio(v, decisions) for k, v in sorted(tool_counts.items())},
        "model_mix": {k: _ratio(v, decisions) for k, v in sorted(model_counts.items())},
        "avg_run_cost_usd": round(
            sum(r.cost_usd or 0 for r in runs) / len(runs), 6) if runs else 0.0,
        "avg_run_latency_ms": round(
            sum(r.latency_ms or 0 for r in runs) / len(runs), 1) if runs else 0.0,
        "avg_run_steps": round(
            sum(r.steps or 0 for r in runs) / len(runs), 2) if runs else 0.0,
        "eval_pass_rate": eval_pass_rate,
        "autonomy_level": system.autonomy_level if system else None,
        "environment": system.environment if system else None,
        "active_policy_versions": active_versions,
        "approved_models": sorted(approved_models),
    }


# --- baselines ----------------------------------------------------------------------


def create_baseline(db: Session, tenant_id: str, system_id: str, *,
                    by: str | None = None, label: str = "baseline") -> DriftBaseline:
    metrics = collect_metrics(db, tenant_id, system_id)
    baseline = DriftBaseline(
        tenant_id=tenant_id,
        system_id=system_id,
        label=label[:255],
        window={"decisions": metrics["decisions"], "runs": metrics["runs"],
                "collected_at": metrics["collected_at"]},
        metrics=metrics,
        created_by=by,
    )
    db.add(baseline)
    db.commit()
    return baseline


def latest_baseline(db: Session, tenant_id: str,
                    system_id: str) -> DriftBaseline | None:
    return (
        db.query(DriftBaseline)
        .filter(DriftBaseline.tenant_id == tenant_id,
                DriftBaseline.system_id == system_id)
        .order_by(DriftBaseline.created_at.desc())
        .first()
    )


# --- detection rules ------------------------------------------------------------------


def _findings(baseline: dict, observed: dict) -> list[dict]:
    """Apply the documented rules. Pure function: metrics in, findings out."""
    findings: list[dict] = []

    def add(signal: str, severity: str, detail: dict) -> None:
        findings.append({"signal": signal, "severity": severity, "detail": detail})

    n_base = baseline.get("decisions", 0)
    n_obs = observed.get("decisions", 0)
    sample_ok = n_base >= MIN_SAMPLE_DECISIONS and n_obs >= MIN_SAMPLE_DECISIONS

    # zero-tolerance first: bypasses matter at ANY sample size
    if observed.get("oversight_bypasses", 0) > 0:
        add("oversight_drift", "critical", {
            "rule": "oversight_bypass == 0 (zero tolerance)",
            "baseline_value": baseline.get("oversight_bypasses", 0),
            "observed_value": observed["oversight_bypasses"],
        })

    if sample_ok:
        drop = baseline["approval_rate"] - observed["approval_rate"]
        if drop > APPROVAL_DROP_WARN:
            add("governance_drift", "critical" if drop > APPROVAL_DROP_CRIT else "warning", {
                "rule": f"approval_rate drop > {APPROVAL_DROP_WARN} "
                        f"(critical > {APPROVAL_DROP_CRIT})",
                "baseline_value": baseline["approval_rate"],
                "observed_value": observed["approval_rate"],
                "drop": round(drop, 4),
            })
        rise = observed["denial_rate"] - baseline["denial_rate"]
        if rise > DENIAL_RISE_WARN:
            add("behavior_drift", "critical" if rise > DENIAL_RISE_CRIT else "warning", {
                "rule": f"denial_rate rise > {DENIAL_RISE_WARN} "
                        f"(critical > {DENIAL_RISE_CRIT})",
                "baseline_value": baseline["denial_rate"],
                "observed_value": observed["denial_rate"],
                "rise": round(rise, 4),
            })
        risk_rise = observed["high_risk_share"] - baseline["high_risk_share"]
        if risk_rise > HIGH_RISK_RISE_WARN:
            add("behavior_drift_risk", "warning", {
                "rule": f"high/critical risk share rise > {HIGH_RISK_RISE_WARN}",
                "baseline_value": baseline["high_risk_share"],
                "observed_value": observed["high_risk_share"],
                "rise": round(risk_rise, 4),
            })
        # tool mix
        base_tools = baseline.get("tool_mix") or {}
        for tool, share in (observed.get("tool_mix") or {}).items():
            if tool not in base_tools and share > NEW_TOOL_SHARE_WARN:
                add("tool_drift", "warning", {
                    "rule": f"new tool share > {NEW_TOOL_SHARE_WARN}",
                    "tool": tool, "observed_value": share,
                    "baseline_value": 0.0,
                })
            elif tool in base_tools and abs(share - base_tools[tool]) > TOOL_SHIFT_INFO:
                add("tool_drift_mix", "info", {
                    "rule": f"tool share shift > {TOOL_SHIFT_INFO}",
                    "tool": tool, "baseline_value": base_tools[tool],
                    "observed_value": share,
                })
    else:
        findings.append({
            "signal": "insufficient_sample", "severity": "info",
            "detail": {
                "rule": f"rate rules require >= {MIN_SAMPLE_DECISIONS} decisions "
                        "on both sides",
                "baseline_decisions": n_base, "observed_decisions": n_obs,
            },
        })

    # model drift (independent of decision sample: registry state is posture)
    base_models = set(baseline.get("model_mix") or {})
    for model in (observed.get("model_mix") or {}):
        if base_models and model not in base_models:
            add("model_drift", "warning", {
                "rule": "model absent from baseline mix",
                "model": model,
            })
    approved_now = set(observed.get("approved_models") or [])
    approved_then = set(baseline.get("approved_models") or [])
    for model in approved_then - approved_now:
        add("model_drift_approval", "critical", {
            "rule": "previously approved model is no longer approved",
            "model": model,
        })

    # posture drift
    if (baseline.get("autonomy_level") is not None
            and observed.get("autonomy_level") is not None
            and observed["autonomy_level"] > baseline["autonomy_level"]):
        add("autonomy_drift", "critical", {
            "rule": "autonomy level raised above baseline",
            "baseline_value": baseline["autonomy_level"],
            "observed_value": observed["autonomy_level"],
        })
    if (baseline.get("environment") != observed.get("environment")
            and observed.get("environment") == "production"):
        add("posture_drift", "warning", {
            "rule": "environment moved to production since baseline",
            "baseline_value": baseline.get("environment"),
            "observed_value": observed.get("environment"),
        })

    # policy drift
    base_policies = set(baseline.get("active_policy_versions") or [])
    obs_policies = set(observed.get("active_policy_versions") or [])
    if base_policies != obs_policies:
        add("policy_drift", "info", {
            "rule": "active governance policy versions changed",
            "added": sorted(obs_policies - base_policies),
            "removed": sorted(base_policies - obs_policies),
        })

    # evaluation regression (needs both sides)
    if (baseline.get("eval_pass_rate") is not None
            and observed.get("eval_pass_rate") is not None):
        drop = baseline["eval_pass_rate"] - observed["eval_pass_rate"]
        if drop > EVAL_REGRESSION_WARN:
            add("evaluation_regression", "warning", {
                "rule": f"eval pass-rate drop > {EVAL_REGRESSION_WARN}",
                "baseline_value": baseline["eval_pass_rate"],
                "observed_value": observed["eval_pass_rate"],
                "drop": round(drop, 4),
            })

    # cost drift
    if (observed.get("runs", 0) >= MIN_SAMPLE_RUNS
            and baseline.get("avg_run_cost_usd", 0) > 0
            and observed.get("avg_run_cost_usd", 0)
            > COST_MULTIPLIER_INFO * baseline["avg_run_cost_usd"]):
        add("cost_drift", "info", {
            "rule": f"avg run cost > {COST_MULTIPLIER_INFO}x baseline",
            "baseline_value": baseline["avg_run_cost_usd"],
            "observed_value": observed["avg_run_cost_usd"],
        })

    return [f for f in findings if f["signal"] != "insufficient_sample"] , \
        next((f for f in findings if f["signal"] == "insufficient_sample"), None)


def check_drift(db: Session, tenant_id: str, system_id: str) -> dict:
    """Compare current metrics to the latest baseline; upsert open signals."""
    baseline_row = latest_baseline(db, tenant_id, system_id)
    if baseline_row is None:
        return {
            "system_id": system_id,
            "status": "no_baseline",
            "detail": "create a baseline first: "
                      "POST /v1/systems/{system_id}/drift/baseline",
        }
    observed = collect_metrics(db, tenant_id, system_id)
    findings, insufficient = _findings(dict(baseline_row.metrics or {}), observed)

    signals = []
    for finding in findings:
        signal = _upsert_signal(db, tenant_id, system_id, baseline_row.id,
                                finding)
        signals.append(signal)
    db.commit()

    return {
        "system_id": system_id,
        "status": "drift_detected" if findings else "clean",
        "baseline": {"id": baseline_row.id, "label": baseline_row.label,
                     "created_at": baseline_row.created_at.isoformat()
                     if baseline_row.created_at else None,
                     "window": baseline_row.window},
        "observed": observed,
        "insufficient_sample": insufficient["detail"] if insufficient else None,
        "findings": findings,
        "signals": [_signal_to_dict(s) for s in signals],
    }


def _upsert_signal(db: Session, tenant_id: str, system_id: str,
                   baseline_id: str, finding: dict) -> DriftSignal:
    existing = (
        db.query(DriftSignal)
        .filter(DriftSignal.tenant_id == tenant_id,
                DriftSignal.system_id == system_id,
                DriftSignal.signal == finding["signal"],
                DriftSignal.status == "open")
        .first()
    )
    if existing is not None:
        existing.occurrences += 1
        existing.last_seen = _now()
        existing.detail = finding["detail"]
        existing.severity = finding["severity"]
        existing.baseline_id = baseline_id
        return existing
    signal = DriftSignal(
        tenant_id=tenant_id,
        system_id=system_id,
        baseline_id=baseline_id,
        signal=finding["signal"],
        severity=finding["severity"],
        detail=finding["detail"],
        status="open",
    )
    db.add(signal)
    db.flush()
    return signal


def open_signals(db: Session, tenant_id: str, system_id: str) -> dict | None:
    """
    For the governance score: None when no baseline exists (insufficient
    evidence), otherwise the open/acknowledged signal sets.
    """
    if latest_baseline(db, tenant_id, system_id) is None:
        return None
    rows = (
        db.query(DriftSignal)
        .filter(DriftSignal.tenant_id == tenant_id,
                DriftSignal.system_id == system_id,
                DriftSignal.status.in_(("open", "acknowledged")))
        .order_by(DriftSignal.last_seen.desc())
        .all()
    )
    open_rows = [r for r in rows if r.status == "open"]
    return {
        "open": [_signal_to_dict(r) for r in open_rows],
        "open_count": len(open_rows),
        "acknowledged": [_signal_to_dict(r) for r in rows if r.status == "acknowledged"],
    }


def acknowledge_signal(db: Session, signal: DriftSignal, by: str,
                       note: str | None = None) -> DriftSignal:
    signal.status = "acknowledged"
    signal.detail = dict(signal.detail or {},
                         acknowledged_by=by, acknowledged_at=_now().isoformat(),
                         acknowledgment_note=note)
    db.commit()
    return signal


def _signal_to_dict(signal: DriftSignal) -> dict:
    return {
        "id": signal.id,
        "system_id": signal.system_id,
        "signal": signal.signal,
        "severity": signal.severity,
        "detail": signal.detail,
        "status": signal.status,
        "occurrences": signal.occurrences,
        "baseline_id": signal.baseline_id,
        "first_seen": signal.first_seen.isoformat() if signal.first_seen else None,
        "last_seen": signal.last_seen.isoformat() if signal.last_seen else None,
    }
