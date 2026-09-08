"""
Phase 10 — Governance drift tests (ASSURANCE plane).

Covers: baseline creation from real evidence, deterministic rule firing at
threshold boundaries, minimum-sample safeguards (no signals on thin data),
false-positive guard (identical window => clean), zero-tolerance bypass
detection, posture/policy/model drift, signal upsert (occurrences, no spam),
acknowledgment semantics, and governance-score integration.

Records are seeded directly into the store (they are the evidence drift reads)
so threshold behavior is tested exactly, without hundreds of HTTP runs.
"""

import uuid

from helios.db import SessionLocal
from helios.models import DecisionRecord


def _headers(api_key):
    return {"X-Helios-API-Key": api_key}


def _setup_system(client, api_key, system_id, **over):
    r = client.post("/v1/systems", json={
        "system_id": system_id, "name": system_id, "owner": "team",
        "purpose": "drift target", "environment": "dev", "autonomy_level": 2,
        **over}, headers=_headers(api_key))
    assert r.status_code == 201, r.text
    return r.json()


def _tenant_id(api_key_raw="test-key-abc123"):
    from helios.cli import get_or_create_tenant
    db = SessionLocal()
    try:
        return get_or_create_tenant(db, "acme").id
    finally:
        db.close()


def _seed_records(system_id, *, total, approval_rate=0.0, denial_rate=0.0,
                  risk="low", tools=None, model="scripted/m", bypasses=0,
                  tag=None):
    """Insert `total` tool_action decision records with the given mix."""
    tag = tag or uuid.uuid4().hex[:8]
    tenant_id = _tenant_id()
    approvals = int(total * approval_rate)
    denials = int(total * denial_rate)
    tools = tools or ["fs.read"]
    db = SessionLocal()
    try:
        for i in range(total):
            required = i < approvals
            denied = approvals <= i < approvals + denials
            oversight = {"required": required,
                         "actual": "approved" if required else "none",
                         "reviewer": "rev" if required else None,
                         "approval_id": None}
            if required and i % 2 == 1:
                oversight["actual"] = "approved"
            decision = "DENIED" if denied else "EXECUTED"
            if i < bypasses:
                # required oversight but executed with no human involvement
                oversight = {"required": True, "actual": "none",
                             "reviewer": None, "approval_id": None}
                decision = "EXECUTED"
            db.add(DecisionRecord(
                tenant_id=tenant_id, system_id=system_id,
                run_id=f"run-{tag}-{i}", source_event_id=f"ev-{tag}-{i}",
                kind="tool_action", action=tools[i % len(tools)],
                decision=decision, model=model, risk=risk,
                actor={"environment": "dev"}, oversight=oversight,
                evidence={"trace_event_ids": [f"ev-{tag}-{i}"]},
                policy={}, outcome={}, data_classification={},
            ))
        db.commit()
    finally:
        db.close()


def _baseline(client, api_key, system_id):
    r = client.post(f"/v1/systems/{system_id}/drift/baseline",
                    json={"by": "sre", "label": "v1"}, headers=_headers(api_key))
    assert r.status_code == 200, r.text
    return r.json()


def _check(client, api_key, system_id):
    r = client.post(f"/v1/systems/{system_id}/drift/check",
                    headers=_headers(api_key))
    assert r.status_code == 200, r.text
    return r.json()


# --- basics -------------------------------------------------------------------------


def test_check_without_baseline_reports_no_baseline(client, api_key):
    _setup_system(client, api_key, "drift-nobase")
    report = _check(client, api_key, "drift-nobase")
    assert report["status"] == "no_baseline"
    assert "baseline" in report["detail"]


def test_baseline_captures_real_metrics(client, api_key):
    _setup_system(client, api_key, "drift-base")
    _seed_records("drift-base", total=24, approval_rate=0.5, risk="low")
    baseline = _baseline(client, api_key, "drift-base")
    metrics = baseline["metrics"]
    assert metrics["decisions"] == 24
    assert metrics["approval_rate"] == 0.5
    assert metrics["denial_rate"] == 0.0
    assert metrics["tool_mix"] == {"fs.read": 1.0}
    assert baseline["window"]["decisions"] == 24


def test_identical_window_is_clean(client, api_key):
    """False-positive safeguard: no change, no signals."""
    _setup_system(client, api_key, "drift-clean")
    _seed_records("drift-clean", total=24, approval_rate=0.5)
    _baseline(client, api_key, "drift-clean")
    report = _check(client, api_key, "drift-clean")
    assert report["status"] == "clean"
    assert report["findings"] == []
    assert report["signals"] == []


def test_insufficient_sample_never_fires_rate_rules(client, api_key):
    _setup_system(client, api_key, "drift-thin")
    _seed_records("drift-thin", total=4, approval_rate=1.0)
    _baseline(client, api_key, "drift-thin")
    # add 4 more with 0 approvals — huge RATE delta, tiny SAMPLE
    _seed_records("drift-thin", total=4, approval_rate=0.0)
    report = _check(client, api_key, "drift-thin")
    rate_findings = [f for f in report["findings"]
                     if f["signal"] in ("governance_drift", "behavior_drift")]
    assert rate_findings == []
    assert report["insufficient_sample"] is not None
    assert report["insufficient_sample"]["observed_decisions"] == 8


# --- rule firing ----------------------------------------------------------------------


def test_approval_rate_drop_detected_with_severity_tiers(client, api_key):
    _setup_system(client, api_key, "drift-approval")
    # baseline: 100% of decisions required approval (mission example)
    _seed_records("drift-approval", total=30, approval_rate=1.0)
    _baseline(client, api_key, "drift-approval")
    # observed: 61% require approval (39pp drop -> critical)
    _seed_records("drift-approval", total=28, approval_rate=0.0)
    # total decisions now 58 with 30 requiring -> observed rate 30/58 = 0.517...
    # drop = 1.0 - 0.517 = 0.483 > 0.40 -> critical
    report = _check(client, api_key, "drift-approval")
    assert report["status"] == "drift_detected"
    governance = [f for f in report["findings"] if f["signal"] == "governance_drift"]
    assert governance, report["findings"]
    assert governance[0]["severity"] == "critical"
    assert governance[0]["detail"]["baseline_value"] == 1.0
    assert governance[0]["detail"]["observed_value"] < 0.62
    assert "0.2" in governance[0]["detail"]["rule"]

    # signal persisted and open
    listing = client.get("/v1/drift", params={"system_id": "drift-approval"},
                         headers=_headers(api_key)).json()
    assert listing["count"] >= 1
    assert any(s["signal"] == "governance_drift" and s["status"] == "open"
               for s in listing["signals"])


def test_oversight_bypass_zero_tolerance(client, api_key):
    _setup_system(client, api_key, "drift-bypass")
    _seed_records("drift-bypass", total=2, tag="b1")  # below min sample on purpose
    _baseline(client, api_key, "drift-bypass")
    _seed_records("drift-bypass", total=3, bypasses=3, tag="b2")
    report = _check(client, api_key, "drift-bypass")
    bypass = [f for f in report["findings"] if f["signal"] == "oversight_drift"]
    assert bypass and bypass[0]["severity"] == "critical"
    # fires despite insufficient sample for rate rules
    assert bypass[0]["detail"]["observed_value"] == 3


def test_autonomy_and_environment_posture_drift(client, api_key):
    _setup_system(client, api_key, "drift-posture", autonomy_level=2)
    _seed_records("drift-posture", total=2)
    _baseline(client, api_key, "drift-posture")

    # raise autonomy to L4 (patch allowed while draft)
    r = client.patch("/v1/systems/drift-posture", json={"autonomy_level": 4},
                     headers=_headers(api_key))
    assert r.status_code == 200, r.text
    report = _check(client, api_key, "drift-posture")
    autonomy = [f for f in report["findings"] if f["signal"] == "autonomy_drift"]
    assert autonomy and autonomy[0]["severity"] == "critical"
    assert autonomy[0]["detail"]["baseline_value"] == 2
    assert autonomy[0]["detail"]["observed_value"] == 4


def test_policy_drift_on_activation(client, api_key):
    _setup_system(client, api_key, "drift-policy")
    _baseline(client, api_key, "drift-policy")
    client.post("/v1/policies", json={
        "name": f"drift-set-{uuid.uuid4().hex[:6]}", "version": "v1",
        "status": "active",
        "rules": [{"id": "r", "when": {"subject": ["tool_action"],
                                       "tool": "nope.*"},
                   "effect": "deny", "reason": "x"}],
    }, headers=_headers(api_key))
    report = _check(client, api_key, "drift-policy")
    policy = [f for f in report["findings"] if f["signal"] == "policy_drift"]
    assert policy and policy[0]["severity"] == "info"
    assert policy[0]["detail"]["added"]


def test_model_drift_new_model_and_lost_approval(client, api_key):
    _setup_system(client, api_key, "drift-model")
    model = client.post("/v1/models", json={
        "provider": "scripted", "model_id": "drift-model-a",
        "approval_status": "approved", "approved_by": "sec",
    }, headers=_headers(api_key)).json()
    _seed_records("drift-model", total=2, model="scripted/drift-model-a")
    _baseline(client, api_key, "drift-model")

    # new model appears in the mix + old model loses approval
    client.post("/v1/models", json={
        "provider": "scripted", "model_id": "drift-model-b",
        "approval_status": "approved", "approved_by": "sec",
    }, headers=_headers(api_key))
    _seed_records("drift-model", total=2, model="scripted/drift-model-b")
    client.post(f"/v1/models/{model['id']}/deprecate",
                json={"by": "sec", "reason": "eol"}, headers=_headers(api_key))

    report = _check(client, api_key, "drift-model")
    signals = {f["signal"] for f in report["findings"]}
    assert "model_drift" in signals           # new model in the mix
    assert "model_drift_approval" in signals  # previously approved, now deprecated
    approval_finding = [f for f in report["findings"]
                        if f["signal"] == "model_drift_approval"][0]
    assert approval_finding["severity"] == "critical"


# --- signal lifecycle -------------------------------------------------------------------


def test_signal_upsert_and_acknowledgment(client, api_key):
    _setup_system(client, api_key, "drift-upsert")
    _seed_records("drift-upsert", total=25, approval_rate=1.0)
    _baseline(client, api_key, "drift-upsert")
    _seed_records("drift-upsert", total=25, approval_rate=0.0)

    first = _check(client, api_key, "drift-upsert")
    gov = [s for s in first["signals"] if s["signal"] == "governance_drift"]
    assert gov and gov[0]["occurrences"] == 1

    # re-check upserts: same signal, occurrences++, no duplicate rows
    second = _check(client, api_key, "drift-upsert")
    gov2 = [s for s in second["signals"] if s["signal"] == "governance_drift"]
    assert gov2 and gov2[0]["id"] == gov[0]["id"]
    assert gov2[0]["occurrences"] == 2

    # acknowledge removes it from the open set
    ack = client.post(f"/v1/drift/{gov2[0]['id']}/acknowledge",
                      json={"by": "sre", "note": "known rollout effect"},
                      headers=_headers(api_key))
    assert ack.status_code == 200
    assert ack.json()["status"] == "acknowledged"
    open_listing = client.get("/v1/drift",
                              params={"system_id": "drift-upsert", "status": "open"},
                              headers=_headers(api_key)).json()
    assert gov2[0]["id"] not in {s["id"] for s in open_listing["signals"]}
    # acknowledging twice is a conflict
    assert client.post(f"/v1/drift/{gov2[0]['id']}/acknowledge",
                       json={"by": "sre"}, headers=_headers(api_key)
                       ).status_code == 409


# --- score integration ---------------------------------------------------------------------


def test_governance_score_drift_component(client, api_key):
    _setup_system(client, api_key, "drift-score")
    # no baseline -> drift component is insufficient evidence
    score = client.get("/v1/systems/drift-score/score",
                       headers=_headers(api_key)).json()
    drift = {c["name"]: c for c in score["components"]}["drift"]
    assert drift["score"] is None
    assert drift["state"] == "insufficient_evidence"

    _seed_records("drift-score", total=25, approval_rate=1.0, tag="s1")
    _baseline(client, api_key, "drift-score")
    score = client.get("/v1/systems/drift-score/score",
                       headers=_headers(api_key)).json()
    drift = {c["name"]: c for c in score["components"]}["drift"]
    assert drift["score"] == 100.0
    assert drift["checks"][0]["check"] == "no_open_drift"

    # drift appears -> component fails and the overall score drops
    _seed_records("drift-score", total=25, approval_rate=0.0, tag="s2")
    report = _check(client, api_key, "drift-score")
    assert report["status"] == "drift_detected"
    score2 = client.get("/v1/systems/drift-score/score",
                        headers=_headers(api_key)).json()
    drift2 = {c["name"]: c for c in score2["components"]}["drift"]
    assert drift2["state"] == "fail"
    assert score2["overall"] < score["overall"]
