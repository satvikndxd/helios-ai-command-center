"""
POLICY + DATA plane tests: autonomy/data-class/system-risk policy matching,
new effects, deterministic data classification, and real-event lineage.
"""

import pytest

from helios.broker.core import ToolBroker
from helios.broker.permissions import PermissionSet, developer_grants
from helios.broker.policy import ToolPolicy
from helios.broker.registry import default_registry
from helios.broker.types import InvocationContext
from helios.governance.classification import classify_text, max_class, at_least
from helios.tools.filesystem import workspace_root


def H(api_key):
    return {"X-Helios-API-Key": api_key}


# --- data classification (deterministic) -----------------------------------


def test_classification_ordering():
    assert max_class(["public", "pii", "internal"]) == "pii"
    assert max_class([]) == "public"
    assert at_least("confidential", "internal") is True
    assert at_least("internal", "confidential") is False


def test_classify_detects_pii_and_secrets():
    c = classify_text("contact me at jane@example.com")
    assert c.data_class == "pii"
    assert any("pii" in s for s in c.signals)

    c2 = classify_text("here is a key sk-" + "a" * 40)
    assert at_least(c2.data_class, "sensitive")


def test_declared_classes_are_honored():
    c = classify_text("totally benign text", declared=["confidential"])
    assert c.data_class == "confidential"


# --- policy matching on new dimensions -------------------------------------


def _ctx(tenant_id="t", **kw):
    return InvocationContext(tenant_id=tenant_id, **kw)


def _perms():
    return PermissionSet(developer_grants(workspace_root=workspace_root()))


def test_confidential_write_requires_human_review():
    broker = ToolBroker(default_registry())
    ctx = _ctx(environment="dev", data_classes=["confidential"])
    ev = broker.evaluate("fs.write", {"path": "a.txt", "content": "x"}, ctx, _perms())
    assert ev["decision"] == "require_human_review"
    assert ev["policy"]["rule_id"] == "block_confidential_data_writes"
    assert ev["data_classification"]["data_class"] == "confidential"


def test_autonomy_level_policy_can_block(client, api_key):
    """A candidate policy blocking L4+ production writes is enforced."""
    broker = ToolBroker(default_registry())
    policy = ToolPolicy(version="autonomy-test", rules=[
        {"id": "block_l4_prod",
         "match": {"capability": ["write"], "environment": "production",
                   "min_autonomy_level": 4},
         "effect": "deny", "reason": "L4+ blocked in production"},
        {"id": "allow_rest", "match": {"max_risk": "critical"},
         "effect": "allow", "reason": "ok"},
    ])
    high = _ctx(environment="production", autonomy_level=4)
    low = _ctx(environment="production", autonomy_level=2)
    ev_high = broker.evaluate("fs.write", {"path": "a", "content": "b"}, high, _perms(), policy)
    ev_low = broker.evaluate("fs.write", {"path": "a", "content": "b"}, low, _perms(), policy)
    assert ev_high["decision"] == "deny"
    assert ev_low["decision"] == "allow"


def test_system_risk_class_match():
    broker = ToolBroker(default_registry())
    policy = ToolPolicy(version="risk-test", rules=[
        {"id": "review_critical_systems",
         "match": {"system_risk_class": ["critical"], "capability": ["write"]},
         "effect": "require_human_review", "reason": "critical system"},
        {"id": "allow_rest", "match": {"max_risk": "critical"},
         "effect": "allow", "reason": "ok"},
    ])
    crit = _ctx(system_risk_class="critical")
    normal = _ctx(system_risk_class="low")
    assert broker.evaluate("fs.write", {"path": "a", "content": "b"}, crit,
                           _perms(), policy)["decision"] == "require_human_review"
    assert broker.evaluate("fs.write", {"path": "a", "content": "b"}, normal,
                           _perms(), policy)["decision"] == "allow"


def test_v1_default_policy_unchanged_for_simple_writes():
    """Existing behavior preserved: a plain dev write with no data signals allows."""
    broker = ToolBroker(default_registry())
    ev = broker.evaluate("fs.write", {"path": "a.txt", "content": "hello"},
                         _ctx(environment="dev"), _perms())
    assert ev["decision"] == "allow"
    assert ev["policy"]["rule_id"] == "allow_low_medium"


# --- lineage from real events ----------------------------------------------


def test_lineage_is_backed_by_real_decisions(client, api_key):
    import os
    os.makedirs(workspace_root(), exist_ok=True)
    with open(os.path.join(workspace_root(), "lin.txt"), "w") as fh:
        fh.write("data")

    # register an approved model + a system, run the agent, then inspect lineage
    client.post("/v1/models", json={"provider": "scripted", "model_id": "lin-model",
                "status": "approved",
                "allowed_data_classes": ["public", "internal", "confidential"],
                "allowed_environments": ["dev", "staging", "production"]},
                headers=H(api_key))
    client.post("/v1/systems", json={"system_id": "lineage-sys", "environment": "dev",
                "autonomy_level": 2}, headers=H(api_key))
    session = client.post("/v1/agent/sessions", json={
        "name": "t", "system_id": "lineage-sys",
        "model_provider": "scripted", "model_id": "lin-model"},
        headers=H(api_key)).json()
    client.post(f"/v1/agent/sessions/{session['id']}/messages", json={"content":
        'go\nSCRIPT:[{"type":"tool_call","tool":"fs.read","args":{"path":"lin.txt"}},'
        '{"type":"final","content":"done"}]'}, headers=H(api_key))

    lin = client.get("/v1/systems/lineage-sys/lineage", headers=H(api_key)).json()
    assert lin["sources"] > 0
    kinds = {n["kind"] for n in lin["nodes"]}
    assert "system" in kinds and "model" in kinds
    # every edge cites a decision id
    assert all("decision_id" in e["evidence"] for e in lin["edges"])
