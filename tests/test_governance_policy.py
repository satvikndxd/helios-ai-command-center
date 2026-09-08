"""
Phase 4 — Governance policy engine tests (POLICY plane).

Covers: rule matching semantics, first-match-wins, default effects, decision
merging (strictest wins), block_deployment escalation, static rule validation,
the built-in autonomy ceiling (production: L0-L2 allow, L3 approval, L4/L5
blocked), persisted policy set lifecycle (candidate -> active -> archived,
activation archives same-name), the deployment gate (block / approval flow /
allow), gated posture changes on active systems, dry-run against candidate
policies, runtime tool-action overlay (deny + escalation), and model-call
governance policies.
"""

import json
import uuid

import pytest

from helios.governance.policy import (
    ALLOW,
    BLOCK_DEPLOYMENT,
    DEFAULT_GOVERNANCE_POLICY,
    DENY,
    REQUIRE_APPROVAL,
    REQUIRE_HUMAN_REVIEW,
    GovernancePolicySet,
    GovernanceSubject,
    merge_decisions,
    validate_rule,
)


def _headers(api_key):
    return {"X-Helios-API-Key": api_key}


# --- unit: rule matching + sets ------------------------------------------------


def _subject(**kw):
    base = dict(kind="deployment", tenant_id="t", environment="production",
                autonomy_level=3, risk_class="HIGH", system_id="s1")
    base.update(kw)
    return GovernanceSubject(**base)


def test_first_match_wins_and_explanation_is_generated():
    policy = GovernancePolicySet(
        name="p", version="v1",
        rules=[
            {"id": "r1", "when": {"environment": ["staging"]}, "effect": DENY,
             "reason": "no prod-like staging"},
            {"id": "r2", "when": {"autonomy_level": {"gte": 3}},
             "effect": REQUIRE_APPROVAL, "reason": "high autonomy"},
        ],
    )
    decision = policy.evaluate(_subject(environment="production", autonomy_level=4))
    assert decision.decision == REQUIRE_APPROVAL
    assert decision.matched_rules[0]["rule_id"] == "r2"
    # the explanation cites BOTH rules with actual subject values
    text = " | ".join(decision.explanation)
    assert "r1' did not match" in text and "production" in text
    assert "r2' matched" in text and "autonomy_level 4 >= 3" in text


def test_default_effect_when_no_rule_matches():
    strict = GovernancePolicySet(name="s", version="v1", rules=[], default_effect=DENY)
    assert strict.evaluate(_subject()).decision == DENY
    permissive = GovernancePolicySet(name="p", version="v1", rules=[])
    assert permissive.evaluate(_subject()).decision == ALLOW


def test_block_deployment_escalates_to_deny_off_deployment():
    policy = GovernancePolicySet(
        name="p", version="v1",
        rules=[{"id": "blk", "when": {"environment": ["production"]},
                "effect": BLOCK_DEPLOYMENT, "reason": "no prod"}],
    )
    deploy = policy.evaluate(_subject(kind="deployment"))
    assert deploy.decision == BLOCK_DEPLOYMENT
    runtime = policy.evaluate(_subject(kind="tool_action", tool="github.merge_pr"))
    assert runtime.decision == DENY
    assert "escalated" in " ".join(runtime.explanation)


def test_condition_kinds():
    policy = GovernancePolicySet(name="p", version="v1", rules=[
        {"id": "data", "when": {"data_class": {"min": "CONFIDENTIAL"}},
         "effect": REQUIRE_HUMAN_REVIEW, "reason": "confidential+"},
    ])
    assert policy.evaluate(_subject(kind="model_call", data_class="PII")).decision \
        == REQUIRE_HUMAN_REVIEW
    assert policy.evaluate(_subject(kind="model_call", data_class="PUBLIC")).decision \
        == ALLOW
    # unknown data class on the subject fails closed (SENSITIVE >= CONFIDENTIAL)
    assert policy.evaluate(_subject(kind="model_call", data_class="WEIRD")).decision \
        == REQUIRE_HUMAN_REVIEW

    model_policy = GovernancePolicySet(name="m", version="v1", rules=[
        {"id": "no-openai", "when": {"model": {"provider": ["openai*"]}},
         "effect": DENY, "reason": "provider restricted"},
    ])
    subj = _subject(kind="model_call",
                    model={"provider": "openai-compat", "model_id": "gpt-x"})
    assert model_policy.evaluate(subj).decision == DENY

    tool_policy = GovernancePolicySet(name="t", version="v1", rules=[
        {"id": "merge-review", "when": {"tool": "github.merge_*",
                                        "min_action_risk": "high"},
         "effect": REQUIRE_HUMAN_REVIEW, "reason": "merges need review"},
    ])
    assert tool_policy.evaluate(
        _subject(kind="tool_action", tool="github.merge_pr",
                 action_risk="critical")).decision == REQUIRE_HUMAN_REVIEW
    assert tool_policy.evaluate(
        _subject(kind="tool_action", tool="github.merge_pr",
                 action_risk="low")).decision == ALLOW
    assert tool_policy.evaluate(
        _subject(kind="tool_action", tool="github.create_pr",
                 action_risk="critical")).decision == ALLOW


def test_merge_strictest_wins_and_accumulates_explanations():
    d1 = GovernancePolicySet(name="a", version="v1", rules=[
        {"id": "r", "effect": REQUIRE_APPROVAL, "reason": "needs approval"}]).evaluate(_subject())
    d2 = GovernancePolicySet(name="b", version="v1", rules=[
        {"id": "r", "effect": DENY, "reason": "forbidden"}]).evaluate(_subject())
    merged = merge_decisions([d1, d2])
    assert merged.decision == DENY
    assert set(merged.policy_versions) == {"a@v1", "b@v1"}
    assert len(merged.matched_rules) == 2


def test_validate_rule_static_errors():
    assert validate_rule({"effect": ALLOW}) == ["rule missing 'id'"]
    assert any("effect" in e for e in validate_rule({"id": "x", "effect": "maybe"}))
    errors = validate_rule({"id": "x", "effect": DENY,
                            "when": {"subject": ["launch Missiles"]}})
    assert any("subject kind" in e for e in errors)


def test_serialization_round_trip():
    policy = GovernancePolicySet(name="p", version="v2", description="d",
                                 default_effect=DENY, scope={"system_ids": ["a"]},
                                 rules=[{"id": "r", "effect": ALLOW}])
    clone = GovernancePolicySet.from_dict(json.loads(json.dumps(policy.to_dict())))
    assert clone.to_dict() == policy.to_dict()


# --- unit: the built-in autonomy ceiling ---------------------------------------


@pytest.mark.parametrize("level,expected", [
    (0, ALLOW), (1, ALLOW), (2, ALLOW),
    (3, REQUIRE_APPROVAL),
    (4, BLOCK_DEPLOYMENT), (5, BLOCK_DEPLOYMENT),
])
def test_default_policy_production_autonomy_ceiling(level, expected):
    decision = DEFAULT_GOVERNANCE_POLICY.evaluate(
        _subject(environment="production", autonomy_level=level))
    assert decision.decision == expected


def test_default_policy_staging_and_dev_unconstrained():
    for env in ("staging", "dev"):
        for level in range(6):
            decision = DEFAULT_GOVERNANCE_POLICY.evaluate(
                _subject(environment=env, autonomy_level=level))
            assert decision.decision == ALLOW, (env, level)


def test_default_policy_l5_denied_in_production_runtime():
    decision = DEFAULT_GOVERNANCE_POLICY.evaluate(
        _subject(kind="tool_action", environment="production",
                 autonomy_level=5, tool="shell.run"))
    assert decision.decision == DENY


# --- API: policy set lifecycle ----------------------------------------------------


def _create_set(client, api_key, **overrides):
    payload = {
        "name": f"policy-{uuid.uuid4().hex[:8]}",
        "version": "v3",
        "description": "Confidential+ data may only reach approved providers",
        "rules": [
            {"id": "review-confidential-models",
             "when": {"subject": ["model_call"], "data_class": {"min": "CONFIDENTIAL"}},
             "effect": "require_human_review",
             "reason": "confidential data requires human review of model usage"},
            {"id": "deny-unapproved-provider-tools",
             "when": {"subject": ["tool_action"], "tool": "github.*",
                      "min_action_risk": "critical"},
             "effect": "deny",
             "reason": "critical github actions denied by org policy"},
        ],
        **overrides,
    }
    return client.post("/v1/policies", json=payload, headers=_headers(api_key))


def test_policy_set_lifecycle(client, api_key):
    name = f"sensitive-data-routing-{uuid.uuid4().hex[:8]}"
    response = _create_set(client, api_key, name=name)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["status"] == "candidate"

    # duplicate name+version rejected
    assert _create_set(client, api_key, name=name).status_code == 400

    # invalid rule rejected at creation (static validation)
    bad = _create_set(client, api_key, name="bad", version="v1",
                      rules=[{"id": "r", "effect": "shrug"}])
    assert bad.status_code == 400
    assert "effect" in bad.json()["detail"]

    # activation
    activated = client.post(f"/v1/policies/governance/{created['id']}/activate",
                            headers=_headers(api_key))
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    assert activated.json()["activated_at"]

    # a new version of the same name archives the previous active on activation
    v4 = _create_set(client, api_key, name=name, version="v4").json()
    client.post(f"/v1/policies/governance/{v4['id']}/activate",
                headers=_headers(api_key))
    previous = client.get(f"/v1/policies/governance/{created['id']}",
                          headers=_headers(api_key)).json()
    assert previous["status"] == "archived"

    # archive the new one too
    archived = client.post(f"/v1/policies/governance/{v4['id']}/archive",
                           headers=_headers(api_key)).json()
    assert archived["status"] == "archived"

    # listing + V1 compatibility: GET /v1/policies keeps `policies` key and
    # adds governance_policies (built-in default always present)
    listing = client.get("/v1/policies", headers=_headers(api_key)).json()
    assert any(p["version"] == "helios-default-v1" for p in listing["policies"])
    assert any(g["name"] == "helios-governance-default"
               for g in listing["governance_policies"])


def test_candidate_sets_are_not_enforced_until_active(client, api_key):
    # register a system whose model call would trip the candidate rule
    client.post("/v1/systems", json={
        "system_id": "candidate-test", "name": "C", "owner": "o",
        "environment": "production", "autonomy_level": 2,
        "data_classes": ["CONFIDENTIAL"],
    }, headers=_headers(api_key))
    client.post("/v1/models", json={
        "provider": "scripted", "model_id": "cand-model",
        "approval_status": "approved", "approved_by": "sec",
    }, headers=_headers(api_key))
    created = _create_set(client, api_key, name="cand-policy", version="v1",
                          scope={"system_ids": ["candidate-test"]}).json()

    session = client.post("/v1/agent/sessions", json={
        "name": "s", "model_provider": "scripted", "model_id": "cand-model",
        "system_id": "candidate-test", "environment": "production",
    }, headers=_headers(api_key)).json()
    content = "go\nSCRIPT:" + json.dumps([{"type": "final", "content": "ok"}])
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages",
                      json={"content": content}, headers=_headers(api_key)).json()
    # candidate policy NOT enforced: run completes despite CONFIDENTIAL data
    assert run["state"] == "completed", run

    # dry-run shows what the candidate WOULD do
    dry = client.post("/v1/policies/dry-run", json={
        "subject": {"kind": "model_call", "environment": "production",
                    "data_class": "CONFIDENTIAL", "system_id": "candidate-test"},
        "candidate_policy_set_id": created["id"],
    }, headers=_headers(api_key)).json()
    assert dry["decision"]["decision"] == "require_human_review"
    assert dry["candidate_included"] is True

    # activate -> now the same run is gated by human review
    client.post(f"/v1/policies/governance/{created['id']}/activate",
                headers=_headers(api_key))
    run2 = client.post(f"/v1/agent/sessions/{session['id']}/messages",
                       json={"content": content}, headers=_headers(api_key)).json()
    assert run2["state"] == "blocked"
    assert "human oversight" in run2["error"]["message"]
    assert run2["error"]["approval_id"]


# --- API: deployment gate ---------------------------------------------------------


def _register_system(client, api_key, system_id, **overrides):
    payload = {"system_id": system_id, "name": system_id, "owner": "team",
               "environment": "production", "autonomy_level": 2, **overrides}
    return client.post("/v1/systems", json=payload, headers=_headers(api_key))


def test_deployment_gate_allows_l2_production(client, api_key):
    _register_system(client, api_key, "gate-l2", autonomy_level=2)
    response = client.post("/v1/systems/gate-l2/lifecycle", json={"state": "active"},
                           headers=_headers(api_key))
    assert response.status_code == 200
    assert response.json()["lifecycle"] == "active"
    assert response.json()["governance"]["decision"] == "allow"


def test_deployment_gate_blocks_l4_production(client, api_key):
    _register_system(client, api_key, "gate-l4", autonomy_level=4)
    response = client.post("/v1/systems/gate-l4/lifecycle", json={"state": "active"},
                           headers=_headers(api_key))
    assert response.status_code == 200
    body = response.json()
    assert body["lifecycle"] == "blocked"
    assert body["blocked_reason"]["rule_id"] == "prod-deploy-block-l4-plus"
    assert "L4/L5" in body["blocked_reason"]["detail"]
    # the block is versioned evidence
    versions = client.get("/v1/systems/gate-l4/versions",
                          headers=_headers(api_key)).json()["versions"]
    assert "blocked" in versions[0]["change_summary"]


def test_deployment_gate_l3_requires_bound_approval(client, api_key):
    _register_system(client, api_key, "gate-l3", autonomy_level=3)

    # first activation attempt -> approval required, lifecycle unchanged
    response = client.post("/v1/systems/gate-l3/lifecycle", json={"state": "active"},
                           headers=_headers(api_key))
    assert response.status_code == 200
    body = response.json()
    assert body["lifecycle"] == "draft"
    assert body["approval_required"]["action"] == "system.deploy:gate-l3"
    approval_id = body["approval_required"]["approval_id"]
    assert body["governance"]["decision"] == "require_approval"

    # repeating the attempt reuses the same pending request (no spam)
    again = client.post("/v1/systems/gate-l3/lifecycle", json={"state": "active"},
                        headers=_headers(api_key)).json()
    assert again["approval_required"]["approval_id"] == approval_id

    # activation with an unapproved id fails
    response = client.post("/v1/systems/gate-l3/lifecycle",
                           json={"state": "active", "approval_id": approval_id},
                           headers=_headers(api_key))
    assert response.status_code == 400
    assert "posture" in str(response.json()["detail"])

    # human approves the exact posture
    decided = client.post(f"/v1/agent/approvals/{approval_id}/decide",
                          json={"decision": "approved", "decided_by": "release-mgr"},
                          headers=_headers(api_key))
    assert decided.status_code == 200

    # activation now succeeds and cites the approval
    response = client.post("/v1/systems/gate-l3/lifecycle",
                           json={"state": "active", "approval_id": approval_id},
                           headers=_headers(api_key))
    assert response.status_code == 200
    body = response.json()
    assert body["lifecycle"] == "active"
    assert body["approval_id"] == approval_id


def test_deployment_approval_invalidated_by_posture_change(client, api_key):
    _register_system(client, api_key, "gate-rebind", autonomy_level=3)
    body = client.post("/v1/systems/gate-rebind/lifecycle",
                       json={"state": "active"}, headers=_headers(api_key)).json()
    approval_id = body["approval_required"]["approval_id"]
    client.post(f"/v1/agent/approvals/{approval_id}/decide",
                json={"decision": "approved", "decided_by": "mgr"},
                headers=_headers(api_key))

    # change a NON-posture field (version bumps -> posture hash invalidated)
    patched = client.patch("/v1/systems/gate-rebind",
                           json={"description": "updated docs"},
                           headers=_headers(api_key))
    assert patched.status_code == 200

    response = client.post("/v1/systems/gate-rebind/lifecycle",
                           json={"state": "active", "approval_id": approval_id},
                           headers=_headers(api_key))
    # the old approval no longer binds to the current posture
    assert response.status_code in (200, 400)
    body = response.json()
    if response.status_code == 200:
        # a NEW pending approval was created (old hash no longer matches)
        assert body["lifecycle"] == "draft"
        assert body["approval_required"]["approval_id"] != approval_id


def test_registering_directly_active_is_gated(client, api_key):
    response = _register_system(client, api_key, "gate-direct",
                                autonomy_level=5, lifecycle="active")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["governance"]["decision"] == "block_deployment"
    assert "hint" in detail

    # L2 production may register directly active
    ok = _register_system(client, api_key, "gate-direct-ok",
                          autonomy_level=2, lifecycle="active")
    assert ok.status_code == 201
    assert ok.json()["lifecycle"] == "active"


def test_posture_change_on_active_system_is_gated(client, api_key):
    _register_system(client, api_key, "gate-patch", autonomy_level=2,
                     lifecycle="active")
    # raising autonomy to L4 in production while active -> refused (409)
    response = client.patch("/v1/systems/gate-patch", json={"autonomy_level": 4},
                            headers=_headers(api_key))
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["governance"]["decision"] == "block_deployment"
    # the change was NOT applied
    current = client.get("/v1/systems/gate-patch", headers=_headers(api_key)).json()
    assert current["autonomy_level"] == 2
    assert current["lifecycle"] == "active"

    # raising to L3 -> also gated (approval flow lives on activation)
    response = client.patch("/v1/systems/gate-patch", json={"autonomy_level": 3},
                            headers=_headers(api_key))
    assert response.status_code == 409

    # non-posture changes are fine
    assert client.patch("/v1/systems/gate-patch", json={"description": "docs"},
                        headers=_headers(api_key)).status_code == 200


# --- runtime: tool-action overlay ---------------------------------------------------


def _script(steps):
    return "run this plan\nSCRIPT:" + json.dumps(steps)


def _bound_session(client, api_key, system_id, environment="production", **sys_over):
    client.post("/v1/systems", json={
        "system_id": system_id, "name": system_id, "owner": "team",
        "environment": environment, "autonomy_level": 2, **sys_over},
        headers=_headers(api_key))
    client.post("/v1/models", json={
        "provider": "scripted", "model_id": f"{system_id}-model",
        "approval_status": "approved", "approved_by": "sec"},
        headers=_headers(api_key))
    return client.post("/v1/agent/sessions", json={
        "name": "s", "model_provider": "scripted",
        "model_id": f"{system_id}-model", "system_id": system_id,
        "environment": environment}, headers=_headers(api_key)).json()


def test_tool_overlay_denies_by_governance_policy(client, api_key):
    session = _bound_session(client, api_key, "overlay-deny")
    policy = client.post("/v1/policies", json={
        "name": f"no-shell-{uuid.uuid4().hex[:8]}", "version": "v1",
        "status": "active",
        "scope": {"system_ids": ["overlay-deny"]},
        "rules": [{"id": "deny-shell", "when": {"subject": ["tool_action"],
                                                "tool": "shell.*"},
                   "effect": "deny", "reason": "shell forbidden for this system"}],
    }, headers=_headers(api_key))
    assert policy.status_code == 201

    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "shell.run",
             "args": {"command": "echo hi"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "completed"  # denial is fed back; agent finishes

    events = client.get(f"/v1/agent/runs/{run['id']}/events",
                        headers=_headers(api_key)).json()["events"]
    gov = [e for e in events if e["event_type"] == "governance_evaluation"
           and e["name"] == "shell.run"]
    assert gov and gov[0]["payload"]["decision"] == "deny"
    assert gov[0]["payload"]["matched_rules"][0]["rule_id"] == "deny-shell"
    outcomes = [e for e in events if e["event_type"] == "outcome"
                and e["status"] == "denied"]
    assert outcomes
    assert "governance policy denied" in outcomes[0]["payload"]["reason"]
    # the tool never executed
    assert not [e for e in events if e["event_type"] == "tool_execution"]


def test_tool_overlay_escalates_allow_to_approval(client, api_key):
    """A dev-env filesystem write is tool-policy ALLOW; governance escalates it."""
    session = _bound_session(client, api_key, "overlay-escalate", environment="dev")
    client.post("/v1/policies", json={
        "name": f"writes-need-eyes-{uuid.uuid4().hex[:8]}", "version": "v1",
        "status": "active",
        "scope": {"system_ids": ["overlay-escalate"]},
        "rules": [{"id": "review-writes",
                   "when": {"subject": ["tool_action"], "capability": ["write"]},
                   "effect": "require_approval",
                   "reason": "all writes by this system need a human"}],
    }, headers=_headers(api_key))

    from helios.tools.filesystem import workspace_root
    import os
    os.makedirs(workspace_root(), exist_ok=True)

    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.write",
             "args": {"path": "note.txt", "content": "hello"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "awaiting_approval"
    assert run["pending"]["tool"] == "fs.write"
    assert "governance policy requires human oversight" in run["pending"]["reason"]
    # the pending approval carries the governance explanation for the human
    approval = client.get("/v1/approvals", headers=_headers(api_key)).json()
    pending = [a for a in approval["approvals"]
               if a["id"] == run["pending"]["approval_id"]]
    assert pending
    summary = pending[0]["summary"]
    assert summary["governance"]["matched_rules"][0]["rule_id"] == "review-writes"
    assert summary["data_classification"]["effective"] in (
        "PUBLIC", "INTERNAL", "CONFIDENTIAL", "SENSITIVE", "PII")


def test_tool_overlay_scoped_to_other_system_does_not_apply(client, api_key):
    session = _bound_session(client, api_key, "overlay-unscoped-target",
                             environment="dev")
    client.post("/v1/policies", json={
        "name": f"other-system-only-{uuid.uuid4().hex[:8]}", "version": "v1",
        "status": "active",
        "scope": {"system_ids": ["some-other-system"]},
        "rules": [{"id": "deny-all-tools", "when": {"subject": ["tool_action"]},
                   "effect": "deny", "reason": "scoped elsewhere"}],
    }, headers=_headers(api_key))

    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.read",
             "args": {"path": "missing.txt"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "completed"
    events = client.get(f"/v1/agent/runs/{run['id']}/events",
                        headers=_headers(api_key)).json()["events"]
    gov = [e for e in events if e["event_type"] == "governance_evaluation"
           and e["name"] == "fs.read"]
    assert gov and gov[0]["payload"]["decision"] == "allow"


def test_unbound_sessions_have_no_tool_overlay(client, api_key):
    """V1 compatibility: sessions without a system keep pure ToolPolicy semantics."""
    session = client.post("/v1/agent/sessions", json={
        "name": "legacy", "model_provider": "scripted", "environment": "dev"},
        headers=_headers(api_key)).json()
    created = client.post("/v1/policies", json={
        "name": f"tenant-wide-deny-{uuid.uuid4().hex[:8]}", "version": "v1",
        "status": "active",
        "rules": [{"id": "deny-everything", "when": {"subject": ["tool_action"]},
                   "effect": "deny", "reason": "would break legacy if applied"}],
    }, headers=_headers(api_key)).json()
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.read",
             "args": {"path": "missing.txt"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "completed"
    events = client.get(f"/v1/agent/runs/{run['id']}/events",
                        headers=_headers(api_key)).json()["events"]
    # no TOOL governance_evaluation events on unbound (legacy) sessions
    # (model-preflight governance is always recorded — that is Phase 3)
    assert not [e for e in events if e["event_type"] == "governance_evaluation"
                and e["name"] == "fs.read"]

    # archive the tenant-wide policy so it cannot contaminate later tests
    client.post(f"/v1/policies/governance/{created['id']}/archive",
                headers=_headers(api_key))


# --- runtime: model-call governance policies ---------------------------------------


def test_model_call_governance_deny(client, api_key):
    session = _bound_session(client, api_key, "model-gov-deny")
    client.post("/v1/policies", json={
        "name": f"provider-restriction-{uuid.uuid4().hex[:8]}", "version": "v1",
        "status": "active",
        "scope": {"system_ids": ["model-gov-deny"]},
        "rules": [{"id": "deny-scripted-in-prod",
                   "when": {"subject": ["model_call"],
                            "model": {"provider": ["scripted"]},
                            "environment": ["production"]},
                   "effect": "deny", "reason": "scripted models not for production"}],
    }, headers=_headers(api_key))
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([{"type": "final", "content": "hi"}])},
        headers=_headers(api_key)).json()
    assert run["state"] == "blocked"
    assert "governance policy" in run["error"]["message"]
    events = client.get(f"/v1/agent/runs/{run['id']}/events",
                        headers=_headers(api_key)).json()["events"]
    gov = [e for e in events if e["event_type"] == "governance_evaluation"]
    assert gov and gov[0]["payload"]["decision"] == "deny"


# --- runtime: lifecycle gate -----------------------------------------------------


def test_suspended_system_cannot_execute(client, api_key):
    session = _bound_session(client, api_key, "lifecycle-suspended",
                             environment="dev")
    client.post("/v1/systems/lifecycle-suspended/lifecycle",
                json={"state": "active"}, headers=_headers(api_key))
    client.post("/v1/systems/lifecycle-suspended/lifecycle",
                json={"state": "suspended", "author": "ops"},
                headers=_headers(api_key))

    # model call is refused while suspended
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([{"type": "final", "content": "hi"}])},
        headers=_headers(api_key)).json()
    assert run["state"] == "blocked"
    assert "suspended" in run["error"]["message"]

    # tool calls are refused too (direct governed invocation)
    response = client.post("/v1/tools/invoke", json={
        "tool": "fs.read", "args": {"path": "x.txt"},
        "session_id": session["id"]}, headers=_headers(api_key))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "denied"
    assert "suspended" in body["reason"]
    assert body["governance"]["matched_rules"][0]["rule_id"] == "lifecycle_gate"


def test_blocked_system_cannot_execute(client, api_key):
    _register_system(client, api_key, "lifecycle-blocked", autonomy_level=5)
    body = client.post("/v1/systems/lifecycle-blocked/lifecycle",
                       json={"state": "active"}, headers=_headers(api_key)).json()
    assert body["lifecycle"] == "blocked"

    session = client.post("/v1/agent/sessions", json={
        "name": "s", "model_provider": "scripted", "model_id": "whatever",
        "system_id": "lifecycle-blocked", "environment": "production"},
        headers=_headers(api_key)).json()
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([{"type": "final", "content": "hi"}])},
        headers=_headers(api_key)).json()
    assert run["state"] == "blocked"
    assert "blocked" in run["error"]["message"]
