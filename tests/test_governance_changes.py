"""
Phase 9 — Replay expansion + AI Change Management tests (ASSURANCE plane).

Covers: system-level replay against candidate governance/tool/model state
(with diffs citing recorded evidence), the full change lifecycle
(candidate -> replay -> evaluation -> risk_comparison -> human_review ->
approved -> deployed), lifecycle enforcement (no skipping, no invisible
deploys), payload-bound review approvals (content change invalidates),
deploy actually applying the candidate (verified by a live gated run),
rollback restoring the previous state + opening its own change record,
system/model-asset change families, and tenant isolation.
"""

import json
import os
import uuid

from helios.db import SessionLocal
from helios.models import ChangeRecord, TraceEvent
from helios.tools.filesystem import workspace_root


def _headers(api_key):
    return {"X-Helios-API-Key": api_key}


def _script(steps):
    return "run this plan\nSCRIPT:" + json.dumps(steps)


def _setup(client, api_key, system_id, *, environment="dev", autonomy=2):
    r = client.post("/v1/systems", json={
        "system_id": system_id, "name": system_id, "owner": "team",
        "purpose": "change management target",
        "environment": environment, "autonomy_level": autonomy,
    }, headers=_headers(api_key))
    assert r.status_code == 201, r.text
    model_id = f"{system_id}-model"
    r = client.post("/v1/models", json={
        "provider": "scripted", "model_id": model_id,
        "approval_status": "approved", "approved_by": "sec",
    }, headers=_headers(api_key))
    assert r.status_code == 201, r.text
    r = client.post("/v1/agent/sessions", json={
        "name": "s", "model_provider": "scripted", "model_id": model_id,
        "system_id": system_id, "environment": environment,
    }, headers=_headers(api_key))
    assert r.status_code == 201, r.text
    return r.json()


def _write_run(client, api_key, session, name="chg-write.txt"):
    return client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.write",
             "args": {"path": name, "content": "release notes"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()


def _policy_candidate(system_id, name=None):
    return {
        "name": name or f"writes-gated-{uuid.uuid4().hex[:6]}",
        "version": "v2",
        "description": "candidate: writes by this system need eyes",
        "scope": {"system_ids": [system_id]},
        "rules": [{
            "id": "gate-writes",
            "when": {"subject": ["tool_action"], "tool": "fs.write"},
            "effect": "require_approval",
            "reason": "candidate policy: writes require approval",
        }],
    }


def _full_review_flow(client, api_key, change_id, by="release-mgr"):
    for endpoint, payload in (
        ("evaluate", {"by": by}),
        ("risk-comparison", {"by": by}),
        ("submit-review", {"by": by}),
    ):
        r = client.post(f"/v1/changes/{change_id}/{endpoint}", json=payload,
                        headers=_headers(api_key))
        assert r.status_code == 200, (endpoint, r.text)
    change = r.json()
    approval_id = change["approval_id"]
    r = client.post(f"/v1/agent/approvals/{approval_id}/decide",
                    json={"decision": "approved", "decided_by": by},
                    headers=_headers(api_key))
    assert r.status_code == 200, r.text
    r = client.post(f"/v1/changes/{change_id}/approve",
                    json={"by": by, "approval_id": approval_id},
                    headers=_headers(api_key))
    assert r.status_code == 200, r.text
    return r.json()


# --- system-level replay ------------------------------------------------------


def test_system_replay_diffs_candidate_governance(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    session = _setup(client, api_key, "rp-system")
    run = _write_run(client, api_key, session, name="rp-1.txt")
    assert run["state"] == "completed"  # baseline: write allowed, no policy

    candidate = _policy_candidate("rp-system")
    report = client.post("/v1/replay/systems/rp-system", json={
        "candidate_governance": candidate,
    }, headers=_headers(api_key))
    assert report.status_code == 200, report.text
    report = report.json()
    assert report["runs_replayed"] == 1
    gov = report["governance_layer"]
    assert gov["original"].get("allow") == 1
    assert gov["candidate"].get("require_approval") == 1
    assert report["summary"]["governance_layer"]["newly_gated"] == 1
    change = gov["changes"][0]
    assert change["tool"] == "fs.write"
    assert change["run_id"] == run["id"]
    assert change["candidate_rules"][0]["rule_id"] == "gate-writes"
    # the diff cites the recorded proposal event
    events = client.get(f"/v1/agent/runs/{run['id']}/events",
                        headers=_headers(api_key)).json()["events"]
    assert change["event_id"] in {e["id"] for e in events}
    # nothing executed during replay
    assert not [e for e in events if e["event_type"] == "tool_execution"
                and e["seq"] > 20]


def test_system_replay_candidate_model_and_posture(client, api_key):
    session = _setup(client, api_key, "rp-model")
    run = _write_run(client, api_key, session, name="rp-2.txt")
    assert run["state"] == "completed"

    # candidate: the model's approval is revoked -> preflights would deny
    report = client.post("/v1/replay/systems/rp-model", json={
        "candidate_model": {"approval_status": "deprecated"},
    }, headers=_headers(api_key)).json()
    changes = report["governance_layer"]["changes"]
    model_changes = [c for c in changes if c["tool"].startswith("model:")]
    assert model_changes
    assert model_changes[0]["original"] == "allow"
    assert model_changes[0]["candidate"] == "deny"

    # candidate posture: L5 in production -> the default ceiling denies
    prod = _setup(client, api_key, "rp-posture", environment="production")
    run2 = client.post(f"/v1/agent/sessions/{prod['id']}/messages", json={
        "content": _script([{"type": "final", "content": "ok"}])},
        headers=_headers(api_key)).json()
    assert run2["state"] == "completed"
    report = client.post("/v1/replay/systems/rp-posture", json={
        "candidate_system": {"autonomy_level": 5},
    }, headers=_headers(api_key)).json()
    assert report["posture"]["autonomy_level"] == 5


def test_system_replay_unknown_system_404(client, api_key):
    assert client.post("/v1/replay/systems/ghost", json={},
                       headers=_headers(api_key)).status_code == 404


# --- change lifecycle ------------------------------------------------------------


def test_policy_change_full_lifecycle_deploys_and_rolls_back(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    session = _setup(client, api_key, "chg-policy")
    baseline = _write_run(client, api_key, session, name="chg-base.txt")
    assert baseline["state"] == "completed"

    candidate = _policy_candidate("chg-policy")
    created = client.post("/v1/changes", json={
        "change_type": "policy",
        "title": "Gate writes for chg-policy",
        "author": "platform-lead",
        "target": {"family": "policy_set", "name": candidate["name"],
                   "system_id": "chg-policy"},
        "candidate": candidate,
    }, headers=_headers(api_key))
    assert created.status_code == 201, created.text
    change = created.json()
    change_id = change["id"]
    assert change["state"] == "candidate"
    assert change["previous"] == {"active": None}
    assert change["evidence"]["history"][0]["to"] == "candidate"

    # cannot skip ahead
    assert client.post(f"/v1/changes/{change_id}/deploy", json={"by": "x"},
                       headers=_headers(api_key)).status_code == 409
    assert client.post(f"/v1/changes/{change_id}/evaluate", json={"by": "x"},
                       headers=_headers(api_key)).status_code == 409

    # replay against the system's history
    replayed = client.post(f"/v1/changes/{change_id}/replay",
                           json={"by": "platform-lead"},
                           headers=_headers(api_key))
    assert replayed.status_code == 200, replayed.text
    change = replayed.json()
    assert change["state"] == "replay"
    summary = change["evidence"]["replay"]["summary"]
    assert summary["governance_layer"]["newly_gated"] >= 1

    # review flow -> approved
    change = _full_review_flow(client, api_key, change_id)
    assert change["state"] == "approved"
    risk = change["evidence"]["risk_comparison"]
    assert any("REQUIRE APPROVAL" in f or "ALLOWED" in f for f in risk["risk_flags"])

    # deploy applies the candidate: the policy set is now ACTIVE
    deployed = client.post(f"/v1/changes/{change_id}/deploy",
                           json={"by": "release-mgr"},
                           headers=_headers(api_key))
    assert deployed.status_code == 200, deployed.text
    change = deployed.json()
    assert change["state"] == "deployed"
    assert change["deployed_by"] == "release-mgr"
    assert change["rollback_target"]["family"] == "policy_set"
    listing = client.get("/v1/policies/governance",
                         params={"status": "active"},
                         headers=_headers(api_key)).json()
    assert candidate["name"] in {p["name"] for p in listing["policy_sets"]}

    # proof of application: a NEW write run is now gated
    gated = _write_run(client, api_key, session, name="chg-gated.txt")
    assert gated["state"] == "awaiting_approval"
    assert "governance policy requires human oversight" in gated["pending"]["reason"]

    # rollback: the set is archived and writes flow again
    rolled = client.post(f"/v1/changes/{change_id}/rollback",
                         json={"by": "release-mgr", "note": "false-positive gates"},
                         headers=_headers(api_key))
    assert rolled.status_code == 200, rolled.text
    body = rolled.json()
    assert body["state"] == "rolled_back"
    rollback_record = body["rollback_record"]
    assert rollback_record["change_type"] == "rollback"
    assert rollback_record["state"] == "deployed"
    assert rollback_record["author"] == "release-mgr"
    assert "false-positive gates" in (rollback_record["description"] or "")

    listing = client.get("/v1/policies/governance",
                         params={"name": candidate["name"]},
                         headers=_headers(api_key)).json()
    assert all(p["status"] == "archived" for p in listing["policy_sets"])

    flowing = _write_run(client, api_key, session, name="chg-after-rollback.txt")
    assert flowing["state"] == "completed"

    # governance_change events form the audit trail
    db = SessionLocal()
    try:
        events = (db.query(TraceEvent)
                  .filter(TraceEvent.event_type == "governance_change")
                  .all())
        kinds = [e.payload.get("event") for e in events
                 if e.payload.get("change_id") == change_id]
        assert "change_created" in kinds
        assert "change_deployed" in kinds
        assert "change_rolled_back" in kinds
    finally:
        db.close()


def test_review_approval_binds_to_change_content(client, api_key):
    os.makedirs(workspace_root(), exist_ok=True)
    session = _setup(client, api_key, "chg-bind")
    _write_run(client, api_key, session, name="chg-bind.txt")
    candidate = _policy_candidate("chg-bind")
    change = client.post("/v1/changes", json={
        "change_type": "policy", "title": "t", "author": "a",
        "target": {"family": "policy_set", "name": candidate["name"],
                   "system_id": "chg-bind"},
        "candidate": candidate,
    }, headers=_headers(api_key)).json()
    change_id = change["id"]
    client.post(f"/v1/changes/{change_id}/replay", json={"by": "a"},
                headers=_headers(api_key))
    client.post(f"/v1/changes/{change_id}/evaluate", json={"by": "a"},
                headers=_headers(api_key))
    client.post(f"/v1/changes/{change_id}/risk-comparison", json={"by": "a"},
                headers=_headers(api_key))
    change = client.post(f"/v1/changes/{change_id}/submit-review",
                         json={"by": "a"}, headers=_headers(api_key)).json()
    approval_id = change["approval_id"]
    client.post(f"/v1/agent/approvals/{approval_id}/decide",
                json={"decision": "approved", "decided_by": "boss"},
                headers=_headers(api_key))

    # tamper with the candidate AFTER review — the approval no longer binds
    db = SessionLocal()
    try:
        row = db.get(ChangeRecord, change_id)
        row.candidate = dict(row.candidate, description="sneaky edit")
        db.commit()
    finally:
        db.close()

    r = client.post(f"/v1/changes/{change_id}/approve",
                    json={"by": "boss", "approval_id": approval_id},
                    headers=_headers(api_key))
    assert r.status_code == 409
    assert "does not bind" in r.json()["detail"]


def test_system_config_change_applies_and_rolls_back(client, api_key):
    _setup(client, api_key, "chg-system", autonomy=2)
    change = client.post("/v1/changes", json={
        "change_type": "agent_config",
        "title": "Raise autonomy to L3",
        "author": "product",
        "target": {"family": "system", "system_id": "chg-system"},
        "candidate": {"autonomy_level": 3, "description": "L3 pilot"},
    }, headers=_headers(api_key)).json()
    change_id = change["id"]
    assert change["previous"]["autonomy_level"] == 2

    client.post(f"/v1/changes/{change_id}/replay", json={"by": "product"},
                headers=_headers(api_key))
    _full_review_flow(client, api_key, change_id)
    deployed = client.post(f"/v1/changes/{change_id}/deploy",
                           json={"by": "product"}, headers=_headers(api_key)).json()
    assert deployed["state"] == "deployed"

    system = client.get("/v1/systems/chg-system", headers=_headers(api_key)).json()
    assert system["autonomy_level"] == 3
    assert system["description"] == "L3 pilot"

    rolled = client.post(f"/v1/changes/{change_id}/rollback",
                         json={"by": "product"}, headers=_headers(api_key)).json()
    assert rolled["state"] == "rolled_back"
    system = client.get("/v1/systems/chg-system", headers=_headers(api_key)).json()
    assert system["autonomy_level"] == 2

    # the system's own version history shows change + rollback
    versions = client.get("/v1/systems/chg-system/versions",
                          headers=_headers(api_key)).json()["versions"]
    summaries = [v["change_summary"] or "" for v in versions]
    assert any(change_id[:8] in s or "change" in s for s in summaries)
    assert any("rollback" in s for s in summaries)


def test_model_asset_change_applies(client, api_key):
    model = client.post("/v1/models", json={
        "provider": "scripted", "model_id": "chg-model-x",
        "approval_status": "approved", "approved_by": "sec",
        "allowed_data_classes": ["PUBLIC", "PII"],
    }, headers=_headers(api_key)).json()

    change = client.post("/v1/changes", json={
        "change_type": "model",
        "title": "Tighten data ceiling to PUBLIC",
        "author": "security",
        "target": {"family": "model_asset", "model_asset_id": model["id"]},
        "candidate": {"allowed_data_classes": ["PUBLIC"]},
    }, headers=_headers(api_key)).json()
    change_id = change["id"]
    assert set(change["previous"]["allowed_data_classes"]) == {"PUBLIC", "PII"}

    client.post(f"/v1/changes/{change_id}/replay", json={"by": "security"},
                headers=_headers(api_key))
    _full_review_flow(client, api_key, change_id, by="security")
    deployed = client.post(f"/v1/changes/{change_id}/deploy",
                           json={"by": "security"}, headers=_headers(api_key)).json()
    assert deployed["state"] == "deployed"

    # no direct GET-by-id for assets in the models API; use lookup
    looked = client.get("/v1/models/lookup",
                        params={"provider": "scripted", "model_id": "chg-model-x"},
                        headers=_headers(api_key)).json()
    assert looked["allowed_data_classes"] == ["PUBLIC"]

    rolled = client.post(f"/v1/changes/{change_id}/rollback",
                         json={"by": "security"}, headers=_headers(api_key)).json()
    assert rolled["state"] == "rolled_back"
    looked = client.get("/v1/models/lookup",
                        params={"provider": "scripted", "model_id": "chg-model-x"},
                        headers=_headers(api_key)).json()
    assert set(looked["allowed_data_classes"]) == {"PUBLIC", "PII"}


def test_change_rejection_requires_reason_and_records(client, api_key):
    change = client.post("/v1/changes", json={
        "change_type": "prompt", "title": "t", "author": "a",
        "target": {"family": "system", "system_id": "chg-policy"},
        "candidate": {"deployment": {"prompt": "new instructions"}},
    }, headers=_headers(api_key))
    # target system must exist
    if change.status_code == 400:
        _setup(client, api_key, "chg-reject")
        change = client.post("/v1/changes", json={
            "change_type": "prompt", "title": "t", "author": "a",
            "target": {"family": "system", "system_id": "chg-reject"},
            "candidate": {"deployment": {"prompt": "new instructions"}},
        }, headers=_headers(api_key))
    assert change.status_code == 201, change.text
    change_id = change.json()["id"]

    rejected = client.post(f"/v1/changes/{change_id}/reject",
                           json={"by": "reviewer", "reason": "insufficient evidence"},
                           headers=_headers(api_key))
    assert rejected.status_code == 200
    body = rejected.json()
    assert body["state"] == "rejected"
    assert body["evidence"]["history"][-1]["note"] == "insufficient evidence"
    # a rejected change cannot be deployed
    assert client.post(f"/v1/changes/{change_id}/deploy", json={"by": "x"},
                       headers=_headers(api_key)).status_code == 409


def test_change_validation(client, api_key):
    # unknown type
    r = client.post("/v1/changes", json={
        "change_type": "vibes", "author": "a", "target": {}, "candidate": {"x": 1}},
        headers=_headers(api_key))
    assert r.status_code == 422
    # missing author
    r = client.post("/v1/changes", json={
        "change_type": "policy", "author": "",
        "target": {"family": "policy_set", "name": "x"}, "candidate": {"x": 1}},
        headers=_headers(api_key))
    assert r.status_code in (400, 422)
    # unknown family
    r = client.post("/v1/changes", json={
        "change_type": "policy", "author": "a",
        "target": {"family": "space-laser"}, "candidate": {"x": 1}},
        headers=_headers(api_key))
    assert r.status_code == 400


def test_change_tenant_isolation(client, api_key, other_tenant_api_key):
    _setup(client, api_key, "chg-iso")
    change = client.post("/v1/changes", json={
        "change_type": "agent_config", "title": "t", "author": "a",
        "target": {"family": "system", "system_id": "chg-iso"},
        "candidate": {"description": "x"},
    }, headers=_headers(api_key)).json()
    assert client.get(f"/v1/changes/{change['id']}",
                      headers=_headers(other_tenant_api_key)).status_code == 404
    assert client.post(f"/v1/changes/{change['id']}/deploy", json={"by": "evil"},
                       headers=_headers(other_tenant_api_key)).status_code == 404
    listing = client.get("/v1/changes", headers=_headers(other_tenant_api_key)).json()
    assert listing["count"] == 0
