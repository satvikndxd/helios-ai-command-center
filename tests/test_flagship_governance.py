"""
FLAGSHIP end-to-end governance demo.

Register an AI system (release-agent, L3, production, model + policy), have
the agent run the full GitHub coding workflow, hit the CRITICAL production
merge, require human oversight, approve, execute, record everything, then:
replay under a candidate policy, and read the governance dashboard + audit.

The whole point: HELIOS knows WHAT the system is, WHAT it may do, WHAT it
did, WHY it was allowed, WHO supervised it, and WHETHER it stays in policy.
"""

import json
import os
import shutil
import subprocess

import httpx
import pytest

from helios.tools import github as github_tools
from helios.tools.filesystem import workspace_root


def H(api_key):
    return {"X-Helios-API-Key": api_key}


def _stub_github(responses):
    def factory():
        def handler(request):
            key = f"{request.method} {request.url.path}"
            return httpx.Response(200, json=responses.get(key, {}))
        return httpx.Client(base_url="https://api.github.test",
                            transport=httpx.MockTransport(handler))
    return factory


def test_flagship_governance_end_to_end(client, api_key):
    root = workspace_root()
    os.makedirs(root, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "work"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@t.io"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "agent"], cwd=root, check=True)

    github_tools.set_client_factory(_stub_github({
        "GET /repos/acme/release": {"full_name": "acme/release",
                                    "default_branch": "main"},
        "POST /repos/acme/release/pulls": {"number": 7, "html_url": "http://pr/7"},
        "PUT /repos/acme/release/pulls/7/merge": {"merged": True, "sha": "deadbeef"},
    }))
    try:
        # 1. register the model (POLICY plane) — approved for this env/data
        client.post("/v1/models", json={
            "provider": "scripted", "model_id": "release-model", "version": "17",
            "status": "approved",
            "allowed_data_classes": ["public", "internal"],
            "allowed_environments": ["dev", "staging", "production"]},
            headers=H(api_key))

        # 2. register the AI system (IDENTITY plane) — L3 conditional autonomy.
        #    The agent operates in the staging/CI environment; merging into the
        #    production branch `main` is the critical, human-gated action.
        system = client.post("/v1/systems", json={
            "system_id": "release-agent", "name": "Release Agent",
            "owner": "release-platform", "purpose": "ship approved changes",
            "environment": "staging", "autonomy_level": 3, "risk_class": "high",
            "models": [{"provider": "scripted", "model_id": "release-model"}],
            "data_classes": ["internal"]},
            headers=H(api_key))
        assert system.status_code == 201

        # 3. start a session bound to the system; it inherits env + autonomy
        session = client.post("/v1/agent/sessions", json={
            "name": "release", "system_id": "release-agent",
            "model_provider": "scripted", "model_id": "release-model",
            "github_repo": "acme/release"},
            headers=H(api_key)).json()
        assert session["environment"] == "staging"
        assert session["autonomy_level"] == 3

        # 4-8. the agent runs the full coding workflow up to the merge
        steps = [
            {"type": "tool_call", "tool": "github.get_repo",
             "args": {"repo": "acme/release"}},
            {"type": "tool_call", "tool": "fs.write",
             "args": {"path": "fix.py", "content": "def fix():\n    return 1\n"}},
            {"type": "tool_call", "tool": "shell.run",
             "args": {"command": "python3 -c \"import fix\""}},
            {"type": "tool_call", "tool": "git.branch", "args": {"name": "agent/fix"}},
            {"type": "tool_call", "tool": "git.commit",
             "args": {"message": "fix", "add_all": True}},
            {"type": "tool_call", "tool": "github.create_pr",
             "args": {"repo": "acme/release", "title": "fix", "head": "agent/fix",
                      "base": "main"}},
            {"type": "tool_call", "tool": "github.merge_pr",
             "args": {"repo": "acme/release", "number": 7, "base": "main"}},
            {"type": "final", "content": "Shipped."},
        ]
        run = client.post(f"/v1/agent/sessions/{session['id']}/messages",
                          json={"content": "ship the fix\nSCRIPT:" + json.dumps(steps)},
                          headers=H(api_key)).json()

        # 9-11. HELIOS classifies the production merge CRITICAL -> human oversight
        assert run["state"] == "awaiting_approval"
        assert run["pending"]["tool"] == "github.merge_pr"
        assert run["pending"]["risk"]["risk"] == "critical"
        approval_id = run["pending"]["approval_id"]

        # 12. the human inspects a decision's WHY (real evidence)
        decisions = client.get("/v1/decisions?system_id=release-agent&kind=tool_call",
                               headers=H(api_key)).json()["decisions"]
        merge_decision = next(d for d in decisions if d["action"] == "github.merge_pr")
        why = client.get(f"/v1/decisions/{merge_decision['id']}/why",
                         headers=H(api_key)).json()
        assert why["verdict"] == "APPROVAL REQUIRED"
        assert any("Human oversight" in c["label"] for c in why["checks"])

        # 13-14. human approves; HELIOS executes the exact approved payload
        client.post(f"/v1/agent/approvals/{approval_id}/decide",
                    json={"decision": "approved", "decided_by": "release-mgr",
                          "comment": "changelog verified"}, headers=H(api_key))
        resumed = client.post(f"/v1/agent/runs/{run['id']}/resume",
                              headers=H(api_key)).json()
        assert resumed["state"] == "completed"
        assert "Shipped" in resumed["output_text"]

        # 15-16. decision + governance trace recorded
        merge_dec = client.get(f"/v1/decisions/{merge_decision['id']}",
                               headers=H(api_key)).json()
        # the same logical action is now backed by an approved oversight record
        after = client.get("/v1/decisions?system_id=release-agent&kind=tool_call"
                           "&decision=allow", headers=H(api_key)).json()["decisions"]
        assert any(d["action"] == "github.merge_pr"
                   and d["human_oversight"] == "approved" for d in after)

        # 17. replay the run against a candidate policy (change before deploy)
        candidate = {
            "version": "cand-deny-merges",
            "rules": [
                {"id": "deny_merges", "match": {"tool": "github.merge_pr"},
                 "effect": "deny", "reason": "candidate: freeze merges"},
                {"id": "allow_rest", "match": {"max_risk": "critical"},
                 "effect": "allow", "reason": "ok"},
            ],
        }
        replay = client.post(f"/v1/agent/runs/{run['id']}/replay",
                             json={"policy": candidate}, headers=H(api_key)).json()
        assert replay["proposals"] >= 7
        # under the candidate, the merge flips to denied
        assert any(c["tool"] == "github.merge_pr" and c["candidate"] == "denied"
                   for c in replay["comparisons"])

        # 18. the same, expressed as a governed change record with evidence
        change = client.post("/v1/changes", json={
            "kind": "policy", "title": "freeze merges", "system_id": "release-agent",
            "current": {"policy": "helios-default-v1"},
            "candidate": {"policy": candidate}}, headers=H(api_key)).json()
        client.post(f"/v1/changes/{change['id']}/replay",
                    json={"run_id": run["id"]}, headers=H(api_key))
        change = client.get(f"/v1/changes/{change['id']}", headers=H(api_key)).json()
        assert change["status"] == "replayed"
        assert change["evidence"]["replay"]["changes"]

        # 19. the system governance dashboard + audit — all evidence-backed
        score = client.get("/v1/systems/release-agent/governance",
                           headers=H(api_key)).json()
        assert 0 <= score["overall"] <= 100
        identity = next(c for c in score["checks"] if c["key"] == "identity")
        assert identity["status"] == "pass"
        oversight_check = next(c for c in score["checks"]
                               if c["key"] == "human_oversight")
        assert oversight_check["status"] in ("pass", "warn")

        oversight = client.get("/v1/systems/release-agent/oversight",
                               headers=H(api_key)).json()
        assert oversight["summary"]["involved"] >= 1
        assert oversight["bypass_detected"] is False

        audit = client.get("/v1/systems/release-agent/audit",
                           headers=H(api_key)).json()
        assert audit["record_type"] == "helios_governance_audit_record"
        assert audit["system"]["system_id"] == "release-agent"
        assert audit["governance_status"]["overall"] == score["overall"]

        # lineage: every edge is backed by a real decision id
        lineage = client.get("/v1/systems/release-agent/lineage",
                             headers=H(api_key)).json()
        assert lineage["sources"] > 0
        assert all("decision_id" in e["evidence"] for e in lineage["edges"])
    finally:
        github_tools.set_client_factory(None)
        shutil.rmtree(os.path.join(root, ".git"), ignore_errors=True)
