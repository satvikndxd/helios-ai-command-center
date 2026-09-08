"""
Phase 12 — Flagship end-to-end demo, headless.

The same `governance.demo.run_demo` that powers `make governance-demo` runs
here against the real app (TestClient transport) on a DEDICATED tenant so CI
proves the whole story:

    register release-agent (L3, production, HIGH) -> approve model-x v17 ->
    activate governance policy -> deployment gate REQUIRES APPROVAL -> human
    approves -> governed run: investigate/read/modify/test[HIGH->approved]/
    branch/commit/PR -> production merge CRITICAL -> governance review ->
    human approves -> executes -> DecisionRecords -> WHY explanation ->
    candidate-policy replay comparison -> evaluation -> governance score ->
    drift baseline/check -> audit report.

If any governance link is broken, this test fails.
"""

import json

import pytest

from helios.db import SessionLocal
from helios.governance.demo import SYSTEM_ID, run_demo
from helios.models import ApiKey, hash_api_key

DEMO_TENANT = "flagship-demo"
DEMO_KEY = "flagship-demo-key"


@pytest.fixture()
def demo_call():
    """In-process HTTP call bound to a dedicated demo tenant key."""
    from fastapi.testclient import TestClient

    from helios.cli import get_or_create_application, get_or_create_tenant
    from helios.main import app

    db = SessionLocal()
    try:
        tenant = get_or_create_tenant(db, DEMO_TENANT)
        application = get_or_create_application(db, tenant, "release-eng")
        if not (db.query(ApiKey)
                .filter(ApiKey.key_hash == hash_api_key(DEMO_KEY)).first()):
            db.add(ApiKey(key_hash=hash_api_key(DEMO_KEY),
                          tenant_id=tenant.id, application_id=application.id,
                          name="demo", active=True))
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    lines: list[str] = []

    def call(method, path, payload=None):
        response = client.request(method, path, json=payload,
                                  headers={"X-Helios-API-Key": DEMO_KEY})
        if response.status_code >= 400:
            lines.append(f"ERROR {method} {path} -> {response.status_code}: "
                         f"{response.text[:200]}")
            return None
        return response.json() if response.content else {}

    call.log = lines  # type: ignore[attr-defined]
    return call


def test_flagship_demo_end_to_end(demo_call):
    log_lines: list[str] = []
    artifacts = run_demo(demo_call, api_key=DEMO_KEY, log=log_lines.append)

    # the demo never hit an HTTP error
    assert demo_call.log == [], demo_call.log  # type: ignore[attr-defined]

    # --- IDENTITY -----------------------------------------------------------
    system = artifacts["system"]
    assert system["system_id"] == SYSTEM_ID
    assert system["owner"] == "release-engineering"
    assert system["autonomy_level"] == 3
    assert system["environment"] == "production"
    assert system["risk_class"] == "HIGH"

    # --- MODEL governance -----------------------------------------------------
    model = artifacts["model"]
    assert model["approval_status"] == "approved"
    assert model["approved_by"] == "security-review"
    assert model["allowed_data_classes"] == ["INTERNAL", "PUBLIC"]

    # --- deployment gate was real ----------------------------------------------
    assert artifacts["deploy_approval_id"]

    # --- the run completed through two human decisions ---------------------------
    assert artifacts["final_state"] == "completed"
    assert len(artifacts["approvals"]) == 2       # routine HIGH + CRITICAL merge
    assert artifacts["merge_approval_id"]

    # --- decision records + WHY ---------------------------------------------------
    decisions = artifacts["decisions"]
    actions = {(d["action"], d["decision"]) for d in decisions}
    assert ("github.merge_pr", "APPROVED") in actions      # the gate record
    assert ("github.merge_pr", "EXECUTED") in actions      # the execution record
    assert ("run", "COMPLETED") in actions
    executed_tools = {d["action"] for d in decisions if d["decision"] == "EXECUTED"}
    assert {"github.get_repo", "github.read_file", "fs.write", "shell.run",
            "git.branch", "git.commit", "github.create_pr"} <= executed_tools

    explanation = artifacts["merge_explanation"]
    checks = {c["check"]: c for c in explanation["why"]}
    assert checks["ai_system"]["passed"] is True
    assert checks["human_oversight"]["passed"] is True
    assert "release-manager" in checks["human_oversight"]["reason"]
    assert checks["governance_policy"]["reason"]  # generated, not canned
    assert checks["outcome"]["passed"] is True
    merge_record = [d for d in decisions
                    if d["action"] == "github.merge_pr"
                    and d["decision"] == "EXECUTED"][0]
    assert merge_record["risk"] == "critical"
    assert merge_record["oversight"]["reviewer"] == "release-manager"

    # --- replay against the candidate policy ---------------------------------------
    replay = artifacts["replay"]
    assert replay["runs_replayed"] == 1
    gov = replay["governance_layer"]
    # the run re-invoked both gated tools after approval, so each recorded
    # proposal carries its governance decision (shell HIGH stays tool-policy
    # gated; merge CRITICAL is governance-gated)
    assert gov["original"].get("require_human_review") == 2
    assert gov["candidate"].get("require_approval", 0) >= 1     # create_pr gated
    assert replay["summary"]["governance_layer"]["newly_gated"] == 1
    changed_tools = {c["tool"] for c in gov["changes"]}
    assert "github.create_pr" in changed_tools

    # --- assurance ---------------------------------------------------------------------
    evaluation = artifacts["evaluation"]
    assert evaluation["status"] == "completed"
    assert evaluation["passed"] is True, json.dumps(
        {k: v for k, v in evaluation["results"].items() if not v["passed"]},
        indent=1, default=str)[:2000]
    assert evaluation["score"] >= 0.9

    score = artifacts["score"]
    assert score["overall"] >= 85
    components = {c["name"]: c for c in score["components"]}
    assert components["identity"]["score"] == 100.0
    assert components["model_approval"]["score"] == 100.0
    assert components["human_oversight"]["score"] == 100.0
    assert components["drift"]["score"] == 100.0     # baseline fresh, no signals

    assert artifacts["drift"]["status"] == "clean"

    audit = artifacts["audit"]
    assert audit["report"] == "helios-audit-report-v1"
    assert audit["system"]["lifecycle"] == "active"
    assert audit["activity"]["runs"]["total"] == 1
    assert audit["activity"]["approvals"]["total"] == 3   # deploy + 2 in-run
    assert audit["human_oversight"]["compliant"] is True
    assert len(audit["changes"]) == 0                     # no changes yet
    assert "not a legal or regulatory compliance" in audit["disclaimer"]

    # the demo narration covers the story beats
    text = "\n".join(log_lines)
    assert "REQUIRE" in text.upper()            # deployment gate
    assert "CRITICAL" in text                   # merge classification
    assert "WHY WAS THE PRODUCTION MERGE ALLOWED?" in text
    assert "Overall:" in text                   # governance score panel
