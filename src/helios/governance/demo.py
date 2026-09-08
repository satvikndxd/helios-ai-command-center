"""
HELIOS flagship end-to-end demo — the whole governance story in one run.

    Register AI system "release-agent" (L3, production, HIGH risk)
      -> register + approve model-x v17 (data ceiling INTERNAL)
      -> activate governance policy set (production merges need named review)
      -> deployment gate: L3 in production REQUIRES APPROVAL -> human approves
      -> agent run (deterministic scripted provider, stubbed GitHub REST):
           investigate repo -> read code -> modify code -> run tests
           [HIGH risk: human approves] -> branch -> commit -> create PR
           -> attempt PRODUCTION MERGE -> CRITICAL -> human review with full
              evidence bundle -> approve -> HELIOS executes the exact payload
      -> AI Decision Records + the WHY explanation for the merge
      -> replay the run against a CANDIDATE policy (comparison before deploy)
      -> evaluation + governance score + drift baseline/check + audit report

Zero external services: SQLite (or configured DB), scripted provider,
stubbed GitHub transport, real local git/shell/filesystem tools. Every
governance step is real — the same code paths production uses.

Run it:  python -m helios.governance.demo     (or: make governance-demo)
"""

from __future__ import annotations

import base64
import json
import os
import subprocess

DEMO_TENANT = "helios-demo-org"
DEMO_APP = "release-engineering"
DEMO_KEY = "helios-demo-key"

SYSTEM_ID = "release-agent"
REPO = "acme/release-service"
MODEL = {"provider": "scripted", "model_id": "model-x", "version": "17"}

POLICY_SET = {
    "name": "release-agent-governance",
    "version": "v1",
    "description": "Production release governance for release-agent",
    "rules": [
        {
            "id": "production-merge-review",
            "when": {"subject": ["tool_action"], "tool": "github.merge_*",
                     "min_action_risk": "critical"},
            "effect": "require_human_review",
            "reason": "merges toward protected branches require a named human review",
        },
        {
            "id": "confidential-model-review",
            "when": {"subject": ["model_call"],
                     "data_class": {"min": "CONFIDENTIAL"}},
            "effect": "require_human_review",
            "reason": "confidential-or-above data reaching a model requires review",
        },
    ],
}

CANDIDATE_POLICY_SET = {
    "name": "release-agent-governance",
    "version": "v2-candidate",
    "description": "candidate: also gate PR creation",
    "rules": POLICY_SET["rules"] + [
        {
            "id": "gate-pr-creation",
            "when": {"subject": ["tool_action"], "tool": "github.create_pr"},
            "effect": "require_approval",
            "reason": "PR creation gets a human checkpoint",
        },
    ],
}

AGENT_SCRIPT = [
    {"type": "tool_call", "tool": "github.get_repo",
     "args": {"repo": REPO}, "reasoning": "investigate the repository"},
    {"type": "tool_call", "tool": "github.read_file",
     "args": {"repo": REPO, "path": "src/service.py", "ref": "main"},
     "reasoning": "read the current release service code"},
    {"type": "tool_call", "tool": "fs.write",
     "args": {"path": "src/service.py",
              "content": ("def build_release(version):\n"
                          "    \"\"\"Assemble the release bundle (v2.4.1 fix).\"\"\"\n"
                          "    return {\"version\": version, \"artifacts\": 3}\n")},
     "reasoning": "apply the release fix locally"},
    {"type": "tool_call", "tool": "shell.run",
     "args": {"command": "python3 -c \"print('12 passed in 0.43s')\""},
     "reasoning": "run the test suite (demo stub command)"},
    {"type": "tool_call", "tool": "git.branch",
     "args": {"name": "release/v2.4.1"}, "reasoning": "cut the release branch"},
    {"type": "tool_call", "tool": "git.commit",
     "args": {"message": "release: v2.4.1 bundle fix", "add_all": True},
     "reasoning": "commit the change"},
    {"type": "tool_call", "tool": "github.create_pr",
     "args": {"repo": REPO, "title": "release: v2.4.1 bundle fix",
              "head": "release/v2.4.1", "base": "main"},
     "reasoning": "open the release PR"},
    {"type": "tool_call", "tool": "github.merge_pr",
     "args": {"repo": REPO, "number": 142, "base": "main"},
     "reasoning": "ship it: merge to the protected production branch"},
    {"type": "final",
     "content": "Release v2.4.1 prepared, tested, PR #142 opened and merged "
                "to main under human review."},
]


class DemoError(RuntimeError):
    pass


# --- deterministic GitHub transport ------------------------------------------------


def stub_github_factory():
    """MockTransport-backed GitHub REST stub (demo never touches the network)."""
    import httpx

    from helios.tools import github as github_tools

    service_src = "def build_release(version):\n    return {'version': version}\n"
    responses = {
        f"GET /repos/{REPO}": {
            "full_name": REPO, "default_branch": "main",
            "description": "Release service (demo)", "open_issues_count": 2,
            "language": "python", "private": True,
        },
        f"GET /repos/{REPO}/contents/src/service.py": {
            "encoding": "base64",
            "content": base64.b64encode(service_src.encode()).decode(),
            "sha": "abc123",
        },
        f"POST /repos/{REPO}/pulls": {
            "number": 142, "html_url": f"https://github.test/{REPO}/pull/142",
        },
        f"PUT /repos/{REPO}/pulls/142/merge": {
            "merged": True, "sha": "feedface142",
        },
    }

    def factory():
        def handler(request: "httpx.Request") -> "httpx.Response":
            key = f"{request.method} {request.url.path}"
            body = responses.get(key)
            if body is None:
                return httpx.Response(404, json={"message": f"no stub for {key}"})
            return httpx.Response(200, json=body)
        return httpx.Client(base_url="https://api.github.test",
                            transport=httpx.MockTransport(handler))

    return github_tools, factory


def _prepare_workspace() -> str:
    """Real local git repo inside the tool workspace (git tools operate on it)."""
    from helios.tools.filesystem import workspace_root

    root = workspace_root()
    os.makedirs(root, exist_ok=True)
    if not os.path.isdir(os.path.join(root, ".git")):
        subprocess.run(["git", "init", "-q", "-b", "work"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "agent@helios.demo"],
                       cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "release-agent"],
                       cwd=root, check=True)
    return root


# --- the demo ------------------------------------------------------------------------


def run_demo(call, api_key: str = DEMO_KEY, *, log=print,
             reviewer_merge: str = "release-manager",
             reviewer_routine: str = "tech-lead",
             deploy_approver: str = "cto") -> dict:
    """
    Execute the flagship scenario. `call(method, path, payload=None)` returns
    the parsed JSON response or None on error. Returns the artifact bundle
    (asserted by tests/test_flagship_demo.py).
    """
    def _must(result, step):
        if result is None:
            raise DemoError(f"demo step failed: {step}")
        return result

    def _decide(approval_id, by, comment, kind_label):
        log(f"    ⏺ human decision ({kind_label}) by '{by}': {comment}")
        return _must(call(
            "POST", f"/v1/agent/approvals/{approval_id}/decide",
            {"decision": "approved", "decided_by": by, "comment": comment},
        ), f"decide {approval_id}")

    artifacts: dict = {}
    github_tools, factory = stub_github_factory()
    github_tools.set_client_factory(factory)
    try:
        _prepare_workspace()

        # ---- STEP 1 · IDENTITY: register the AI system ----------------------
        log("\n══ STEP 1 · IDENTITY — register the AI system ══")
        system = _must(call("POST", "/v1/systems", {
            "system_id": SYSTEM_ID,
            "name": "Release Agent",
            "owner": "release-engineering",
            "organization": "acme",
            "purpose": "prepare, test, and ship releases of the release-service",
            "description": "Autonomous release engineering agent (flagship demo)",
            "environment": "production",
            "autonomy_level": 3,
            "risk_class": "HIGH",
            "models": [MODEL],
            "tools": ["github.get_repo", "github.read_file", "fs.write",
                      "shell.run", "git.branch", "git.commit",
                      "github.create_pr", "github.merge_pr"],
            "data_classes": ["INTERNAL"],
            "policies": [f"{POLICY_SET['name']}@{POLICY_SET['version']}"],
            "oversight": {"high_risk": "approval"},
        }), "register system")
        artifacts["system"] = system
        log(f"    ✓ registered '{SYSTEM_ID}' — owner {system['owner']}, "
            f"L{system['autonomy_level']} ({system['autonomy_label']}), "
            f"risk {system['risk_class']}, env {system['environment']}")

        # ---- STEP 2 · MODEL REGISTRY ----------------------------------------
        log("\n══ STEP 2 · IDENTITY/POLICY — register + approve the model ══")
        model = _must(call("POST", "/v1/models", {
            **MODEL,
            "name": "Model-X (demo)",
            "capabilities": ["chat", "tools"],
            "region": "us-east",
            "pricing": {"input_per_1k_usd": 0.003, "output_per_1k_usd": 0.015},
            "risk_class": "MEDIUM",
            "allowed_data_classes": ["PUBLIC", "INTERNAL"],
            "allowed_environments": ["dev", "staging", "production"],
        }), "register model")
        model = _must(call("POST", f"/v1/models/{model['id']}/approve",
                           {"by": "security-review"}), "approve model")
        artifacts["model"] = model
        log(f"    ✓ {MODEL['provider']}/{MODEL['model_id']} v{MODEL['version']} "
            f"APPROVED by {model['approved_by']} — data ceiling "
            f"{model['allowed_data_classes']}")

        # ---- STEP 3 · POLICY -------------------------------------------------
        log("\n══ STEP 3 · POLICY — activate the governance policy set ══")
        policy_set = _must(call("POST", "/v1/policies", {
            **POLICY_SET, "status": "active", "author": "governance-office",
            "scope": {"system_ids": [SYSTEM_ID]},
        }), "create policy set")
        artifacts["policy_set"] = policy_set
        log(f"    ✓ {policy_set['name']}@{policy_set['version']} ACTIVE — "
            f"{len(policy_set['rules'])} rules (production merges need named "
            "human review; confidential+ model data needs review)")

        # ---- STEP 4 · DEPLOYMENT GATE ----------------------------------------
        log("\n══ STEP 4 · POLICY — deployment gate (autonomy ceiling) ══")
        attempt = _must(call("POST", f"/v1/systems/{SYSTEM_ID}/lifecycle",
                             {"state": "active"}), "activate system")
        gate = attempt.get("approval_required")
        if not gate:
            raise DemoError(f"expected L3 production activation to require "
                            f"approval, got: {attempt.get('lifecycle')}")
        governance = attempt.get("governance") or {}
        log(f"    ⚠ activation REQUIRES APPROVAL — {governance.get('reason')}")
        if governance.get("matched_rules"):
            rule = governance["matched_rules"][0]
            log(f"      matched rule: {rule['rule_id']} "
                f"[{', '.join(governance.get('policy_versions') or [])}]")
        _decide(gate["approval_id"], deploy_approver,
                "L3 release agent approved for production with merge review",
                "deployment")
        system = _must(call("POST", f"/v1/systems/{SYSTEM_ID}/lifecycle",
                            {"state": "active",
                             "approval_id": gate["approval_id"]}),
                       "activate with approval")
        if system.get("lifecycle") != "active":
            raise DemoError(f"system did not activate: {system.get('lifecycle')}")
        artifacts["deploy_approval_id"] = gate["approval_id"]
        log(f"    ✓ system ACTIVE (approval {gate['approval_id'][:8]} bound to "
            "the exact deployment posture)")

        # ---- STEP 5 · GOVERNED EXECUTION --------------------------------------
        log("\n══ STEP 5 · EVIDENCE — the governed agent run ══")
        session = _must(call("POST", "/v1/agent/sessions", {
            "name": "release-run-1",
            "system_id": SYSTEM_ID,
            "environment": "dev",          # CI working lane of the prod system
            "autonomy": "autonomous",      # L3 conditional autonomy, unattended
            "model_provider": MODEL["provider"],
            "model_id": MODEL["model_id"],
            "github_repo": REPO,
            "user_id": "release-pipeline",
        }), "create session")
        artifacts["session_id"] = session["id"]
        log(f"    ✓ session bound to '{SYSTEM_ID}' (model preflight will verify "
            "model-x approval + INTERNAL data ceiling on every call)")

        content = ("Prepare and ship release v2.4.1 of the release-service.\n"
                   "SCRIPT:" + json.dumps(AGENT_SCRIPT))
        run = _must(call("POST", f"/v1/agent/sessions/{session['id']}/messages",
                         {"content": content}), "send message")
        artifacts["run_id"] = run["id"]

        approvals = []
        merge_approval_id = None
        guards = 0
        while run.get("state") == "awaiting_approval" and guards < 8:
            guards += 1
            pending = run["pending"]
            approval_id = pending["approval_id"]
            approvals.append(approval_id)
            approval = _must(call("GET", f"/v1/agent/approvals/{approval_id}"),
                             "fetch approval")
            summary = approval.get("summary") or {}
            risk = (summary.get("risk") or {})
            is_merge = pending["tool"] == "github.merge_pr"

            if is_merge:
                merge_approval_id = approval_id
                log("\n    ┌─────────────────────────────────────────────────┐")
                log(f"    │ ⛔ {pending['tool']} — risk "
                    f"{str(risk.get('risk', '?')).upper()} — human review required")
                log("    ├─────────────────────────────────────────────────┤")
                log(f"    │ action   : {pending['tool']} "
                    f"{json.dumps(summary.get('args') or {})}")
                log(f"    │ resource : {json.dumps(summary.get('resource') or {})}")
                for reason in (risk.get("reasons") or []):
                    log(f"    │ risk     : · {reason}")
                policy = summary.get("policy") or {}
                log(f"    │ policy   : {policy.get('policy_version')} rule "
                    f"'{policy.get('rule_id')}' — {policy.get('reason')}")
                governance = summary.get("governance") or {}
                for rule in governance.get("matched_rules") or []:
                    log(f"    │ governance: {rule['set']}@{rule['version']} "
                        f"rule '{rule['rule_id']}' → {rule['effect']}")
                classification = summary.get("data_classification") or {}
                log(f"    │ data     : effective class "
                    f"{classification.get('effective')} "
                    f"(declared {classification.get('declared')}, "
                    f"detected {classification.get('detected')})")
                log(f"    │ binding  : approval bound to args_hash "
                    f"{approval.get('args_hash', '')[:16]}… "
                    "(payload mutation invalidates)")
                log("    └─────────────────────────────────────────────────┘")
                _decide(approval_id, reviewer_merge,
                        "reviewed evidence bundle; merge to main approved",
                "MERGE REVIEW")
            else:
                log(f"    ⏸ {pending['tool']} — risk "
                    f"{str(risk.get('risk', '?')).upper()} — approval required "
                    f"({pending.get('reason', '')[:80]})")
                _decide(approval_id, reviewer_routine,
                        "routine release-engineering step within scope",
                "routine")

            run = _must(call("POST", f"/v1/agent/runs/{run['id']}/resume",
                             None), "resume run")

        artifacts["final_state"] = run.get("state")
        artifacts["approvals"] = approvals
        artifacts["merge_approval_id"] = merge_approval_id
        if run.get("state") != "completed":
            raise DemoError(f"run did not complete: {run.get('state')} "
                            f"({run.get('error')})")
        log(f"\n    ✓ run COMPLETED — {len(approvals)} human decision(s), "
            f"merge approved by '{reviewer_merge}'")
        log(f"    ✓ output: {run.get('output_text', '')[:90]}")

        # ---- STEP 6 · DECISION RECORDS + WHY ----------------------------------
        log("\n══ STEP 6 · EVIDENCE — AI Decision Records and the WHY ══")
        decisions = _must(call("GET", f"/v1/decisions?run_id={run['id']}"),
                          "list decisions")["decisions"]
        artifacts["decisions"] = decisions
        for record in decisions:
            oversight = record.get("oversight") or {}
            log(f"    ▪ {record['kind']:<16} {record['action'][:34]:<34} "
                f"{record['decision']:<18} risk={record.get('risk') or '—'} "
                f"oversight={oversight.get('actual', 'none')}")

        merge_records = [r for r in decisions
                         if r["action"] == "github.merge_pr"
                         and r["decision"] == "EXECUTED"]
        if not merge_records:
            raise DemoError("no executed merge decision record")
        merge_record = merge_records[0]
        explanation = _must(call(
            "GET", f"/v1/decisions/{merge_record['id']}/explanation"),
            "explain merge")
        artifacts["merge_explanation"] = explanation
        log("\n    WHY WAS THE PRODUCTION MERGE ALLOWED?")
        for check in explanation["why"]:
            mark = "—" if check["passed"] is None else ("✓" if check["passed"] else "✗")
            log(f"      {mark} {check['check']}: {check['reason'][:90]}")

        # ---- STEP 7 · REPLAY AGAINST A CANDIDATE POLICY ------------------------
        log("\n══ STEP 7 · ASSURANCE — replay against a candidate policy ══")
        replay_report = _must(call("POST", f"/v1/replay/systems/{SYSTEM_ID}", {
            "candidate_governance": CANDIDATE_POLICY_SET,
        }), "system replay")
        artifacts["replay"] = replay_report
        summary = replay_report["summary"]
        gov = replay_report["governance_layer"]
        log(f"    CURRENT  ({POLICY_SET['name']}@{POLICY_SET['version']}): "
            f"{gov['original']}")
        log(f"    CANDIDATE ({CANDIDATE_POLICY_SET['version']}): "
            f"{gov['candidate']}")
        log(f"    Δ governance: +{summary['governance_layer']['newly_gated']} newly gated, "
            f"+{summary['governance_layer']['newly_blocked']} newly blocked, "
            f"+{summary['governance_layer']['newly_allowed']} newly allowed")
        for change in (gov.get("changes") or [])[:6]:
            log(f"      ▪ {change['tool']}: {change['original']} → "
                f"{change['candidate']} ({change['candidate_reason'][:60]})")

        # ---- STEP 8 · EVALUATION / SCORE / DRIFT / AUDIT -----------------------
        log("\n══ STEP 8 · ASSURANCE — evaluation, drift, score, audit ══")
        evaluation = _must(call("POST", f"/v1/systems/{SYSTEM_ID}/evaluate",
                                {}), "evaluate")
        artifacts["evaluation"] = evaluation
        log(f"    evaluation: score {evaluation['score']} "
            f"({'passed' if evaluation['passed'] else 'failing'}) over "
            f"{evaluation['evidence']['runs_analyzed']} run(s), "
            f"{evaluation['evidence']['records_analyzed']} record(s)")

        _must(call("POST", f"/v1/systems/{SYSTEM_ID}/drift/baseline",
                   {"by": "sre", "label": "post-release"}), "drift baseline")
        drift = _must(call("POST", f"/v1/systems/{SYSTEM_ID}/drift/check"),
                      "drift check")
        artifacts["drift"] = drift
        log(f"    drift: baseline stored; check status {drift['status']} "
            f"({len(drift.get('findings') or [])} finding(s))")

        score = _must(call("GET", f"/v1/systems/{SYSTEM_ID}/score"), "score")
        artifacts["score"] = score
        log("\n    HELIOS GOVERNANCE STATUS")
        for component in score["components"]:
            mark = {"ok": "✓", "warn": "⚠", "fail": "✗",
                    "insufficient_evidence": "—"}[component["state"]]
            value = (f"{component['score']:.0f}"
                     if component["score"] is not None else "n/a")
            log(f"      {mark} {component['name']:<20} {value:>5}  "
                f"(weight {component['weight']})")
        log(f"      Overall: {score['overall']} / 100  [{score['state']}]")

        audit = _must(call("GET", f"/v1/systems/{SYSTEM_ID}/audit"), "audit")
        artifacts["audit"] = audit
        log(f"    audit report: {audit['report']} — "
            f"{audit['activity']['runs']['total']} run(s), "
            f"{audit['activity']['decisions']['total']} decision(s), "
            f"{len(audit['violations'])} violation(s), "
            f"{audit['activity']['approvals']['total']} approval(s)")
        log(f"      {audit['disclaimer']}")

        log("\n══ DEMO COMPLETE ══")
        log("    HELIOS knows WHAT this AI system is, WHAT it is allowed to do,")
        log("    WHAT it actually did, WHY it was allowed to do it, WHO supervised")
        log("    it, and WHETHER it remains within policy.")
        return artifacts
    finally:
        github_tools.set_client_factory(None)


# --- zero-config CLI runner -----------------------------------------------------------


def issue_demo_identity() -> str:
    """Create the demo tenant/application/API key (idempotent)."""
    from helios.cli import get_or_create_application, get_or_create_tenant
    from helios.db import SessionLocal
    from helios.models import ApiKey, hash_api_key

    db = SessionLocal()
    try:
        tenant = get_or_create_tenant(db, DEMO_TENANT)
        application = get_or_create_application(db, tenant, DEMO_APP)
        existing = (db.query(ApiKey)
                    .filter(ApiKey.key_hash == hash_api_key(DEMO_KEY)).first())
        if existing is None:
            db.add(ApiKey(key_hash=hash_api_key(DEMO_KEY),
                          tenant_id=tenant.id, application_id=application.id,
                          name="demo", active=True))
        db.commit()
    finally:
        db.close()
    return DEMO_KEY


def main() -> None:
    import tempfile

    # Zero-config local run: throwaway SQLite + workspace unless configured.
    if not os.environ.get("HELIOS_DATABASE_URL"):
        os.environ["HELIOS_DATABASE_URL"] = (
            "sqlite:///" + os.path.join(
                tempfile.mkdtemp(prefix="helios-demo-db-"), "demo.sqlite3"))
    os.environ.setdefault("HELIOS_DEFAULT_PROVIDER", "mock")
    if not os.environ.get("HELIOS_WORKSPACE_ROOT"):
        os.environ["HELIOS_WORKSPACE_ROOT"] = tempfile.mkdtemp(
            prefix="helios-demo-ws-")

    import httpx  # noqa: F401  (kept for parity with server deployments)
    from starlette.testclient import TestClient

    from helios.db import init_db
    from helios.main import app

    init_db()
    key = issue_demo_identity()

    # In-process ASGI client: the demo runs the REAL app (routes, broker,
    # runtime, governance) without needing a server or network.
    client = TestClient(app)

    def call(method: str, path: str, payload: dict | None = None):
        response = client.request(
            method, path, json=payload,
            headers={"X-Helios-API-Key": key})
        if response.status_code >= 400:
            print(f"    ✗ {method} {path} -> {response.status_code}: "
                  f"{response.text[:300]}")
            return None
        if not response.content:
            return {}
        return response.json()

    print("HELIOS — AI GOVERNANCE CONTROL PLANE")
    print("Flagship demo: the release-agent, end to end (no API keys, no network)\n")
    run_demo(call, api_key=key)


if __name__ == "__main__":
    main()
