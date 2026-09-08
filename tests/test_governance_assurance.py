"""
ASSURANCE plane tests: evaluation, governance score (transparent + explained),
change management (replay evidence, lifecycle, rollback), drift detection
(baseline + deterministic thresholds + false-positive safeguard).
"""

import json
import os

import pytest

from helios.tools.filesystem import workspace_root


def H(api_key):
    return {"X-Helios-API-Key": api_key}


def _ensure_model(client, api_key, model_id="mock-model-1"):
    client.post("/v1/models", json={
        "provider": "scripted", "model_id": model_id, "status": "approved",
        "allowed_data_classes": ["public", "internal", "confidential", "sensitive", "pii"],
        "allowed_environments": ["dev", "staging", "production"]}, headers=H(api_key))


def _system(client, api_key, sid, **over):
    _ensure_model(client, api_key)
    payload = {"system_id": sid, "environment": "dev", "autonomy_level": 2,
               "owner": "platform", "purpose": "test system",
               "models": [{"provider": "scripted", "model_id": "mock-model-1"}]}
    payload.update(over)
    client.post("/v1/systems", json=payload, headers=H(api_key))


def _run(client, api_key, sid, steps):
    session = client.post("/v1/agent/sessions", json={
        "name": "t", "system_id": sid, "model_provider": "scripted"},
        headers=H(api_key)).json()
    return client.post(f"/v1/agent/sessions/{session['id']}/messages",
                       json={"content": "go\nSCRIPT:" + json.dumps(steps)},
                       headers=H(api_key)).json()


# --- evaluation + score ----------------------------------------------------


def test_evaluation_and_score_from_real_activity(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    with open(os.path.join(workspace_root(), "s.txt"), "w") as fh:
        fh.write("hello")
    _system(client, api_key, "score-sys")
    _run(client, api_key, "score-sys", [
        {"type": "tool_call", "tool": "fs.read", "args": {"path": "s.txt"}},
        {"type": "tool_call", "tool": "fs.write", "args": {"path": "o.txt", "content": "x"}},
        {"type": "final", "content": "done"},
    ])

    ev = client.get("/v1/systems/score-sys/evaluation", headers=H(api_key)).json()
    assert ev["sample_size"] >= 2
    assert 0.0 <= ev["metrics"]["policy_compliance"] <= 1.0
    assert ev["counters"]["runs_completed"] >= 1

    score = client.get("/v1/systems/score-sys/governance", headers=H(api_key)).json()
    assert 0 <= score["overall"] <= 100
    # transparent: every check has status + detail + weight
    keys = {c["key"] for c in score["checks"]}
    assert {"identity", "model_approval", "policy_compliance", "human_oversight",
            "auditability", "drift"} <= keys
    for check in score["checks"]:
        assert check["status"] in ("pass", "warn", "fail", "na")
        assert check["detail"]
    # identity should pass (owner + purpose set)
    identity = next(c for c in score["checks"] if c["key"] == "identity")
    assert identity["status"] == "pass"
    # model approval passes (declared model is approved)
    model_check = next(c for c in score["checks"] if c["key"] == "model_approval")
    assert model_check["status"] == "pass"


def test_unapproved_declared_model_fails_score(client, api_key):
    client.post("/v1/systems", json={
        "system_id": "bad-model-sys", "owner": "x", "purpose": "p",
        "models": [{"provider": "who", "model_id": "ghost"}]}, headers=H(api_key))
    score = client.get("/v1/systems/bad-model-sys/governance", headers=H(api_key)).json()
    model_check = next(c for c in score["checks"] if c["key"] == "model_approval")
    assert model_check["status"] == "fail"


# --- change management -----------------------------------------------------


def test_change_lifecycle_with_replay_evidence(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    with open(os.path.join(workspace_root(), "c.txt"), "w") as fh:
        fh.write("x")
    _system(client, api_key, "change-sys")
    run = _run(client, api_key, "change-sys", [
        {"type": "tool_call", "tool": "fs.write", "args": {"path": "c.txt", "content": "y"}},
        {"type": "final", "content": "done"},
    ])

    candidate_policy = {
        "version": "candidate-v2",
        "rules": [
            {"id": "approve_writes", "match": {"capability": ["write"]},
             "effect": "require_approval", "reason": "candidate: writes need approval"},
            {"id": "allow_rest", "match": {"max_risk": "critical"},
             "effect": "allow", "reason": "ok"},
        ],
    }
    change = client.post("/v1/changes", json={
        "kind": "policy", "title": "require approval for writes",
        "system_id": "change-sys",
        "current": {"policy": "helios-default-v1"},
        "candidate": {"policy": candidate_policy}}, headers=H(api_key)).json()
    assert change["status"] == "draft"

    # attach replay evidence: the historical run under the candidate policy
    replayed = client.post(f"/v1/changes/{change['id']}/replay",
                           json={"run_id": run["id"]}, headers=H(api_key)).json()
    assert replayed["status"] == "replayed"
    assert "replay" in replayed["evidence"]
    assert replayed["evidence"]["replay"]["changes"]  # write now flips to approval

    # lifecycle: review -> approved -> deployed
    for status in ("in_review", "approved", "deployed"):
        r = client.post(f"/v1/changes/{change['id']}/transition",
                        json={"status": status}, headers=H(api_key))
        assert r.status_code == 200, r.text
    final = client.get(f"/v1/changes/{change['id']}", headers=H(api_key)).json()
    assert final["status"] == "deployed"
    assert final["rollback_target"] == {"policy": "helios-default-v1"}

    # rollback is allowed from deployed
    r = client.post(f"/v1/changes/{change['id']}/transition",
                    json={"status": "rolled_back"}, headers=H(api_key))
    assert r.status_code == 200


def test_illegal_change_transition_rejected(client, api_key):
    _system(client, api_key, "illegal-change-sys")
    change = client.post("/v1/changes", json={
        "kind": "config", "title": "x", "system_id": "illegal-change-sys"},
        headers=H(api_key)).json()
    # draft -> deployed is not allowed
    r = client.post(f"/v1/changes/{change['id']}/transition",
                    json={"status": "deployed"}, headers=H(api_key))
    assert r.status_code == 409


# --- drift -----------------------------------------------------------------


def test_drift_baseline_and_detection(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    with open(os.path.join(workspace_root(), "d.txt"), "w") as fh:
        fh.write("x")
    # system at production+L2 so writes require approval (high approval rate)
    _system(client, api_key, "drift-sys", environment="production", autonomy_level=2)

    # baseline behavior: a production write that required approval
    import httpx
    from helios.tools import github as gh
    gh.set_client_factory(lambda: httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"merged": True}))))
    try:
        run = _run(client, api_key, "drift-sys", [
            {"type": "tool_call", "tool": "github.merge_pr",
             "args": {"repo": "a/b", "number": 1, "base": "main"}},
            {"type": "final", "content": "x"}])
        assert run["state"] == "awaiting_approval"

        base = client.post("/v1/systems/drift-sys/baseline", headers=H(api_key)).json()
        assert base["sample_size"] >= 1

        # immediately after baseline, no drift (false-positive safeguard)
        drift = client.get("/v1/systems/drift-sys/drift", headers=H(api_key)).json()
        assert drift["drift_detected"] is False
    finally:
        gh.set_client_factory(None)


def test_drift_flags_new_tool(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    with open(os.path.join(workspace_root(), "e.txt"), "w") as fh:
        fh.write("x")
    _system(client, api_key, "drift-tool-sys")
    _run(client, api_key, "drift-tool-sys", [
        {"type": "tool_call", "tool": "fs.read", "args": {"path": "e.txt"}},
        {"type": "final", "content": "x"}])
    client.post("/v1/systems/drift-tool-sys/baseline", headers=H(api_key))

    # new activity uses a different tool
    _run(client, api_key, "drift-tool-sys", [
        {"type": "tool_call", "tool": "fs.write", "args": {"path": "e.txt", "content": "z"}},
        {"type": "final", "content": "x"}])
    drift = client.get("/v1/systems/drift-tool-sys/drift", headers=H(api_key)).json()
    assert drift["drift_detected"] is True
    assert any(s["metric"] == "tools" for s in drift["signals"])


def test_no_baseline_means_no_drift(client, api_key):
    _system(client, api_key, "nobaseline-sys")
    drift = client.get("/v1/systems/nobaseline-sys/drift", headers=H(api_key)).json()
    assert drift["drift_detected"] is False
    assert "no baseline" in drift["note"]


# --- audit -----------------------------------------------------------------


def test_audit_report_is_evidence_backed(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    with open(os.path.join(workspace_root(), "a.txt"), "w") as fh:
        fh.write("x")
    _system(client, api_key, "audit-sys")
    _run(client, api_key, "audit-sys", [
        {"type": "tool_call", "tool": "fs.read", "args": {"path": "a.txt"}},
        {"type": "final", "content": "x"}])
    report = client.get("/v1/systems/audit-sys/audit", headers=H(api_key)).json()
    assert report["record_type"] == "helios_governance_audit_record"
    assert "not a legal" in report["disclaimer"].lower()
    assert report["system"]["system_id"] == "audit-sys"
    assert "overall" in report["governance_status"]
    assert report["evaluation"]["sample_size"] >= 1
