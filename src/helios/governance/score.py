"""
Governance Score (ASSURANCE plane).

A composite, TRANSPARENT governance status for one AI system:

    HELIOS GOVERNANCE STATUS
    Identity            ✓ 100   (weight 10)
    Model approval      ✓ 100   (weight 12)
    Data policy         ✓ 100   (weight 12)
    Human oversight     ✓ 100   (weight 12)
    Policy compliance   ✓  95   (weight 14)
    Evaluation          ✓  94   (weight 10)
    Auditability        ✓ 100   (weight 10)
    Security            ✓ 100   (weight 10)
    Drift               ⚠ n/a   (weight  5, insufficient_evidence)
    Documentation       ⚠  60   (weight  5)
    Overall: 91 / 100

Anti-vanity rules:
* Every component is a list of checks with pass/fail + reason + evidence
  reference, computed from actual system state (registry rows, model assets,
  decision records, evaluation runs, drift signals).
* A component with no backing evidence reports `insufficient_evidence` —
  it is EXCLUDED from the weighted total (and shown), never counted as 100.
* Overall = sum(score_i * weight_i) / sum(weight_i) over scored components,
  scaled to 100. The formula inputs are returned in the payload.
* State badges: ok >= 90, warn >= 60, fail < 60, insufficient_evidence.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from helios.governance import models_registry
from helios.governance.evaluators import evaluate_system
from helios.models import AiSystem, EvaluationRun, ModelAsset

WEIGHTS = {
    "identity": 10,
    "model_approval": 12,
    "data_policy": 12,
    "human_oversight": 12,
    "policy_compliance": 14,
    "evaluation": 10,
    "auditability": 10,
    "security": 10,
    "drift": 5,
    "documentation": 5,
}

STATE_THRESHOLDS = {"ok": 90, "warn": 60}


def _state(score: float | None) -> str:
    if score is None:
        return "insufficient_evidence"
    if score >= STATE_THRESHOLDS["ok"]:
        return "ok"
    if score >= STATE_THRESHOLDS["warn"]:
        return "warn"
    return "fail"


def _component(name: str, checks: list[dict]) -> dict:
    scored = [c for c in checks if c["passed"] is not None]
    if not scored:
        score = None
    else:
        score = round(100.0 * sum(1 for c in scored if c["passed"]) / len(scored), 1)
    return {
        "name": name,
        "weight": WEIGHTS[name],
        "score": score,
        "state": _state(score),
        "checks": checks,
    }


def _check(name: str, passed: bool | None, reason: str, evidence=None) -> dict:
    return {"check": name, "passed": passed, "reason": reason,
            "evidence": evidence or []}


# --- components ------------------------------------------------------------------


def _identity(system: AiSystem) -> dict:
    checks = [
        _check("registered", True,
               f"AI system '{system.system_id}' is registered (v{system.version})",
               [system.id]),
        _check("owner_assigned", bool(system.owner),
               f"owner: {system.owner or 'MISSING'}"),
        _check("purpose_declared", bool((system.purpose or "").strip()),
               f"purpose: {system.purpose or 'MISSING'}"),
        _check("risk_classified", system.risk_class in
               ("LOW", "MEDIUM", "HIGH", "CRITICAL"),
               f"risk class: {system.risk_class}"),
        _check("environment_declared", system.environment in
               ("dev", "staging", "production"),
               f"environment: {system.environment}"),
        _check("autonomy_declared", 0 <= system.autonomy_level <= 5,
               f"autonomy level: L{system.autonomy_level}"),
        _check("lifecycle_not_broken", system.lifecycle != "blocked",
               f"lifecycle: {system.lifecycle}"
               + (f" — blocked: {(system.blocked_reason or {}).get('detail', '')}"
                  if system.lifecycle == "blocked" else ""),
               [system.blocked_reason] if system.lifecycle == "blocked" else []),
    ]
    return _component("identity", checks)


def _model_approval(db: Session, system: AiSystem) -> dict:
    declared = list(system.models or [])
    checks = [
        _check("models_declared", bool(declared),
               f"{len(declared)} model reference(s) declared"
               if declared else "no models declared on the system"),
    ]
    for ref in declared:
        provider = ref.get("provider") if isinstance(ref, dict) else None
        model_id = ref.get("model_id") if isinstance(ref, dict) else str(ref)
        version = ref.get("version") if isinstance(ref, dict) else None
        if not provider:
            # search by model_id across providers
            asset = (
                db.query(ModelAsset)
                .filter(ModelAsset.tenant_id == system.tenant_id,
                        ModelAsset.model_id == model_id)
                .order_by(ModelAsset.created_at.desc())
                .first()
            )
        else:
            asset = models_registry.find_model(
                db, system.tenant_id, provider, model_id, version=version)
        label = f"{provider or '*'}/{model_id}" + (f"@{version}" if version else "")
        if asset is None:
            checks.append(_check(f"model_approved:{label}", False,
                                 f"{label} is NOT REGISTERED (deny-by-default)"))
        else:
            checks.append(_check(
                f"model_approved:{label}", asset.approval_status == "approved",
                f"{label} approval status: {asset.approval_status}"
                + (f" (by {asset.approved_by})" if asset.approved_by else ""),
                [asset.id]))
    return _component("model_approval", checks)


def _data_policy(db: Session, system: AiSystem) -> dict:
    declared = list(system.data_classes or [])
    checks = [
        _check("data_classes_declared", bool(declared),
               f"declared: {declared}" if declared
               else "no data classes declared — classification relies on "
                    "detection only"),
    ]
    # every declared model must have a data ceiling when the system touches
    # sensitive classes
    sensitive = {"CONFIDENTIAL", "SENSITIVE", "PII"} & set(declared)
    if sensitive:
        for ref in (system.models or []):
            if not isinstance(ref, dict):
                continue
            asset = models_registry.find_model(
                db, system.tenant_id, ref.get("provider", ""),
                ref.get("model_id", ""), version=ref.get("version"))
            label = f"{ref.get('provider')}/{ref.get('model_id')}"
            if asset is None:
                checks.append(_check(f"data_ceiling:{label}", False,
                                     f"{label} not registered; cannot verify "
                                     f"data ceiling for {sorted(sensitive)}"))
            else:
                has_ceiling = bool(asset.allowed_data_classes)
                covers = all(c in (asset.allowed_data_classes or [])
                             for c in sensitive)
                checks.append(_check(
                    f"data_ceiling:{label}", has_ceiling and covers,
                    f"{label} data ceiling: {asset.allowed_data_classes or 'NONE DECLARED'}"
                    + ("" if covers or not has_ceiling
                       else f" — does not cover declared {sorted(sensitive)}"),
                    [asset.id]))
    return _component("data_policy", checks)


def _human_oversight(db: Session, system: AiSystem, evaluation: dict) -> dict:
    metric = evaluation["results"].get("human_oversight", {})
    details = metric.get("details", {})
    if details.get("status") == "insufficient_evidence":
        checks = [_check("oversight_evidence", None,
                         "no executed decisions yet — oversight compliance "
                         "cannot be evaluated")]
    else:
        checks = [
            _check("no_bypassed_oversight",
                   not details.get("bypassed"),
                   f"{len(details.get('bypassed') or [])} bypassed",
                   [b["record_id"] for b in (details.get("bypassed") or [])[:10]]),
            _check("declared_requirements_enforced",
                   not details.get("requirement_gaps"),
                   f"{len(details.get('requirement_gaps') or [])} requirement gap(s)",
                   [g["record_id"] for g in (details.get("requirement_gaps") or [])[:10]]),
        ]
    if system.autonomy_level >= 3:
        checks.append(_check(
            "oversight_declared_for_autonomy",
            bool(system.oversight),
            f"L{system.autonomy_level} system oversight requirements: "
            + (json.dumps(system.oversight) if system.oversight else "NONE DECLARED")))
    return _component("human_oversight", checks)


def _policy_compliance(evaluation: dict) -> dict:
    metric = evaluation["results"].get("policy_compliance", {})
    details = metric.get("details", {})
    if details.get("status") == "insufficient_evidence":
        return _component("policy_compliance", [
            _check("compliance_evidence", None,
                   "no decision records yet — compliance cannot be evaluated")])
    score = metric["score"]
    return {
        "name": "policy_compliance",
        "weight": WEIGHTS["policy_compliance"],
        "score": round(100 * score, 1),
        "state": _state(round(100 * score, 1)),
        "checks": [
            _check("policy_compliance_rate", metric["passed"],
                   f"{details.get('records')} decision records, "
                   f"{len(details.get('negative_records') or [])} negative "
                   f"(threshold: {details.get('threshold')})",
                   [n["record_id"] for n in (details.get("negative_records") or [])[:10]]),
        ],
    }


def _evaluation(db: Session, system: AiSystem) -> dict:
    latest = (
        db.query(EvaluationRun)
        .filter(EvaluationRun.tenant_id == system.tenant_id,
                EvaluationRun.system_id == system.system_id,
                EvaluationRun.kind == "governance",
                EvaluationRun.status == "completed")
        .order_by(EvaluationRun.created_at.desc())
        .first()
    )
    if latest is None:
        return _component("evaluation", [
            _check("evaluation_run", None,
                   "no completed governance evaluation — run "
                   "POST /v1/systems/{id}/evaluate")])
    failed = [name for name, r in (latest.results or {}).items()
              if not r.get("passed")
              and r.get("details", {}).get("status") != "insufficient_evidence"]
    return _component("evaluation", [
        _check("evaluation_passed", bool(latest.passed),
               f"latest evaluation score {latest.score}"
               + (f" — failing metrics: {failed}" if failed else " — all metrics passed"),
               [latest.id]),
    ])


def _auditability(evaluation: dict) -> dict:
    metric = evaluation["results"].get("trace_completeness", {})
    details = metric.get("details", {})
    if details.get("status") == "insufficient_evidence":
        return _component("auditability", [
            _check("trace_evidence", None,
                   "no runs recorded — auditability cannot be evaluated")])
    return _component("auditability", [
        _check("traces_complete", metric["passed"],
               f"{details.get('complete')}/{details.get('runs_checked')} runs "
               "fully evidenced (outcome + decided proposals + resolvable "
               "record citations)",
               [g["run_id"] for g in (details.get("gaps") or [])[:10]]),
    ])


def _security(evaluation: dict) -> dict:
    leak = evaluation["results"].get("pii_leakage", {})
    misuse = evaluation["results"].get("tool_misuse", {})
    checks = []
    if leak.get("details", {}).get("status") == "insufficient_evidence":
        checks.append(_check("leak_scans", None,
                             "no recorded model output to scan"))
    else:
        checks.append(_check(
            "no_pii_leakage", leak["passed"],
            f"{leak['details'].get('model_calls_scanned')} model outputs scanned, "
            f"{len(leak['details'].get('leaks') or [])} leak-pattern matches",
            [l["event_id"] for l in (leak["details"].get("leaks") or [])[:10]]))
    if misuse.get("details", {}).get("status") != "insufficient_evidence":
        checks.append(_check(
            "tool_misuse_rate", misuse["passed"],
            f"{misuse['details'].get('denied')} denied attempts / "
            f"{misuse['details'].get('proposals')} proposals "
            f"(threshold: {misuse['details'].get('threshold')})"))
    return _component("security", checks)


def _drift(db: Session, system: AiSystem) -> dict:
    try:
        from helios.governance.drift import open_signals
    except ImportError:
        return _component("drift", [
            _check("drift_detection", None, "drift detection not yet available")])
    signals = open_signals(db, system.tenant_id, system.system_id)
    if signals is None:
        return _component("drift", [
            _check("drift_baseline", None,
                   "no drift baseline recorded — create one with "
                   "POST /v1/systems/{id}/drift/baseline")])
    return _component("drift", [
        _check("no_open_drift", not signals["open"],
               f"{signals['open_count']} open drift signal(s)"
               + (f": {[s['signal'] for s in signals['open']]}" if signals["open"]
                  else ""),
               [s["id"] for s in signals["open"][:10]]),
    ])


def _documentation(system: AiSystem) -> dict:
    checks = [
        _check("description", bool((system.description or "").strip()),
               "description present" if (system.description or "").strip()
               else "no description recorded"),
        _check("policies_linked", bool(system.policies),
               f"policies: {system.policies}" if system.policies
               else "no policies linked on the system record"),
        _check("tools_declared", bool(system.tools),
               f"{len(system.tools or [])} tool(s) declared"
               if system.tools else "no tools declared"),
        _check("organization", bool(system.organization),
               f"organization: {system.organization or 'MISSING'}"),
    ]
    return _component("documentation", checks)


# --- composite ----------------------------------------------------------------------


def compute_governance_score(db: Session, system: AiSystem,
                             *, evaluation: dict | None = None) -> dict:
    """
    Compute the full governance status for one system. `evaluation` may be
    passed in (freshly computed); otherwise the metric suite runs inline
    WITHOUT persisting (persistence happens via the evaluate endpoint).
    """
    if evaluation is None:
        evaluation = evaluate_system(db, system.tenant_id, system.system_id,
                                     system=system)

    components = [
        _identity(system),
        _model_approval(db, system),
        _data_policy(db, system),
        _human_oversight(db, system, evaluation),
        _policy_compliance(evaluation),
        _evaluation(db, system),
        _auditability(evaluation),
        _security(evaluation),
        _drift(db, system),
        _documentation(system),
    ]

    scored = [c for c in components if c["score"] is not None]
    weight_sum = sum(c["weight"] for c in scored)
    overall = (
        round(sum(c["score"] * c["weight"] for c in scored) / weight_sum, 1)
        if weight_sum else None
    )
    return {
        "system_id": system.system_id,
        "overall": overall,
        "state": _state(overall),
        "components": components,
        "weights": dict(WEIGHTS),
        "formula": ("overall = sum(component.score * weight) / sum(weight) "
                    "over components with evidence; insufficient_evidence "
                    "components are excluded and surfaced, never scored 100"),
        "insufficient_evidence": [c["name"] for c in components
                                  if c["score"] is None],
        "evaluation": {
            "score": evaluation.get("score"),
            "passed": evaluation.get("passed"),
            "status": evaluation.get("status"),
        },
    }
