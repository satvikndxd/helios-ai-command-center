"""
Phase 8 — Evaluation + governance score tests (ASSURANCE plane).

Covers: governance evaluation over evidence (all metrics), insufficient
evidence honesty (never scored 100), failing metrics citing record/event ids,
evaluation persistence + baseline chaining, benchmark evaluation via the
existing pipeline + Datasets, LLM-judge contract (opt-in, strict parse,
fail-closed), and the transparent governance score (weights, checks,
formula, component states).
"""

import json
import os
import uuid

from helios.db import SessionLocal
from helios.evaluators.base import EvalResult
from helios.governance.judge import LLMJudgeEvaluator
from helios.models import Dataset, DatasetItem, DecisionTrace, TraceEvent
from helios.tools.filesystem import workspace_root


def _headers(api_key):
    return {"X-Helios-API-Key": api_key}


def _script(steps):
    return "run this plan\nSCRIPT:" + json.dumps(steps)


def _setup(client, api_key, system_id, *, models=None, data_classes=None,
           environment="dev", description=None, policies=None, tools=None,
           organization=None):
    r = client.post("/v1/systems", json={
        "system_id": system_id, "name": system_id, "owner": "team",
        "purpose": f"{system_id} test purpose",
        "organization": organization, "description": description,
        "environment": environment, "autonomy_level": 2,
        "risk_class": "MEDIUM", "models": models or [],
        "data_classes": data_classes or [], "policies": policies or [],
        "tools": tools or [],
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


def _clean_run(client, api_key, session):
    os.makedirs(workspace_root(), exist_ok=True)
    with open(os.path.join(workspace_root(), "ev-clean.txt"), "w") as fh:
        fh.write("build notes")
    return client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "fs.read",
             "args": {"path": "ev-clean.txt"}},
            {"type": "final", "content": "summarized"},
        ])}, headers=_headers(api_key)).json()


# --- governance evaluation ------------------------------------------------------


def test_fresh_system_reports_insufficient_evidence(client, api_key):
    _setup(client, api_key, "ev-fresh")
    r = client.post("/v1/systems/ev-fresh/evaluate", json={"kind": "governance"},
                    headers=_headers(api_key))
    assert r.status_code == 201, r.text
    evaluation = r.json()
    assert evaluation["status"] == "insufficient_evidence"
    assert evaluation["score"] is None
    assert evaluation["passed"] is None
    assert evaluation["evidence"]["metrics_insufficient"]
    # honesty: insufficient metrics are NOT scored as passing
    for name in evaluation["evidence"]["metrics_insufficient"]:
        metric = evaluation["results"][name]
        assert metric["details"].get("status") == "insufficient_evidence"
        assert not metric["passed"]


def test_healthy_system_evaluates_clean(client, api_key):
    session = _setup(client, api_key, "ev-healthy")
    run = _clean_run(client, api_key, session)
    assert run["state"] == "completed"

    evaluation = client.post("/v1/systems/ev-healthy/evaluate",
                             json={}, headers=_headers(api_key)).json()
    assert evaluation["status"] == "completed"
    assert evaluation["score"] == 1.0
    assert evaluation["passed"] is True
    results = evaluation["results"]
    assert results["task_success"]["details"]["completed"] == 1
    assert results["trace_completeness"]["passed"] is True
    assert results["policy_compliance"]["passed"] is True
    assert results["data_flow"]["details"]["violations"] == 0
    assert results["pii_leakage"]["passed"] is True
    # evidence counts are real
    assert evaluation["evidence"]["runs_analyzed"] == 1
    assert evaluation["evidence"]["records_analyzed"] >= 1


def test_denials_show_up_as_compliance_and_misuse_evidence(client, api_key):
    session = _setup(client, api_key, "ev-denied")
    client.post("/v1/policies", json={
        "name": f"ev-deny-shell-{uuid.uuid4().hex[:8]}", "version": "v1",
        "status": "active", "scope": {"system_ids": ["ev-denied"]},
        "rules": [{"id": "no-shell", "when": {"subject": ["tool_action"],
                                              "tool": "shell.*"},
                   "effect": "deny", "reason": "no shell"}],
    }, headers=_headers(api_key))
    run = client.post(f"/v1/agent/sessions/{session['id']}/messages", json={
        "content": _script([
            {"type": "tool_call", "tool": "shell.run",
             "args": {"command": "echo x"}},
            {"type": "final", "content": "done"},
        ])}, headers=_headers(api_key)).json()
    assert run["state"] == "completed"

    evaluation = client.post("/v1/systems/ev-denied/evaluate",
                             json={}, headers=_headers(api_key)).json()
    results = evaluation["results"]
    compliance = results["policy_compliance"]
    assert compliance["passed"] is False
    assert compliance["details"]["negative_records"], "denial not cited"
    cited = compliance["details"]["negative_records"][0]["record_id"]
    records = client.get("/v1/decisions", params={"system_id": "ev-denied"},
                         headers=_headers(api_key)).json()["decisions"]
    assert cited in {r["id"] for r in records}
    misuse = results["tool_misuse"]
    assert misuse["details"]["denied"] == 1
    assert evaluation["passed"] is False


def test_pii_leakage_detected_in_recorded_output(client, api_key):
    session = _setup(client, api_key, "ev-leak")
    run = _clean_run(client, api_key, session)

    # a recorded model output containing leak-class PII (stored evidence)
    db = SessionLocal()
    try:
        event = (db.query(TraceEvent)
                 .filter(TraceEvent.run_id == run["id"],
                         TraceEvent.event_type == "model_call")
                 .first())
        payload = dict(event.payload or {})
        payload["output_preview"] = "confirmed ssn 222-33-4444 in output"
        event.payload = payload
        db.commit()
        event_id = event.id
    finally:
        db.close()

    evaluation = client.post("/v1/systems/ev-leak/evaluate",
                             json={}, headers=_headers(api_key)).json()
    leak = evaluation["results"]["pii_leakage"]
    assert leak["passed"] is False
    assert leak["details"]["leaks"][0]["event_id"] == event_id
    assert leak["details"]["leaks"][0]["leak_type"] == "ssn"


def test_evaluation_persistence_and_baseline_chain(client, api_key):
    session = _setup(client, api_key, "ev-persist")
    _clean_run(client, api_key, session)

    first = client.post("/v1/systems/ev-persist/evaluate", json={},
                        headers=_headers(api_key)).json()
    assert first["baseline_evaluation_id"] is None
    second = client.post("/v1/systems/ev-persist/evaluate", json={},
                         headers=_headers(api_key)).json()
    assert second["baseline_evaluation_id"] == first["id"]

    listing = client.get("/v1/evaluations",
                         params={"system_id": "ev-persist"},
                         headers=_headers(api_key)).json()
    assert listing["count"] == 2
    fetched = client.get(f"/v1/evaluations/{first['id']}",
                         headers=_headers(api_key)).json()
    assert fetched["results"]["task_success"]["passed"] is True


# --- benchmark evaluation (existing pipeline + datasets) ---------------------------


def test_benchmark_evaluation_over_dataset(client, api_key):
    _setup(client, api_key, "ev-benchmark")
    # produce a governed completion trace
    complete = client.post("/v1/ai/complete",
                           json={"input": "hello benchmark"},
                           headers=_headers(api_key))
    assert complete.status_code == 200, complete.text
    trace_id = complete.json()["trace_id"]

    db = SessionLocal()
    try:
        from helios.cli import get_or_create_tenant
        tenant_id = get_or_create_tenant(db, "acme").id
        dataset = Dataset(tenant_id=tenant_id, name="bench", version=1,
                          kind="evaluation", source="production", item_count=1)
        db.add(dataset)
        db.flush()
        db.add(DatasetItem(dataset_id=dataset.id, trace_id=trace_id,
                           input_text="hello benchmark", labels={}))
        db.commit()
        dataset_id = dataset.id
    finally:
        db.close()

    r = client.post("/v1/systems/ev-benchmark/evaluate",
                    json={"kind": "benchmark", "dataset_id": dataset_id},
                    headers=_headers(api_key))
    assert r.status_code == 201, r.text
    evaluation = r.json()
    assert evaluation["kind"] == "benchmark"
    assert evaluation["status"] == "completed"
    assert evaluation["evidence"]["traces_matched"] == 1
    bench = evaluation["results"]["benchmark"]
    assert bench["details"]["items_scored"] == 1
    assert 0.0 <= evaluation["score"] <= 1.0

    # unknown dataset -> 404
    r = client.post("/v1/systems/ev-benchmark/evaluate",
                    json={"kind": "benchmark", "dataset_id": "ghost"},
                    headers=_headers(api_key))
    assert r.status_code == 404


# --- LLM judge (opt-in, strict contract, fail-closed) --------------------------------


class _FakeTrace:
    input_payload = {"input": "summarize the report"}
    output_text = "The report shows revenue up 5%."


def test_llm_judge_contract():
    judge = LLMJudgeEvaluator(complete_fn=lambda p: "SCORE: 0.9 VERDICT: PASS ok")
    result = judge.evaluate(_FakeTrace())
    assert result.score == 0.9 and result.passed

    judge = LLMJudgeEvaluator(complete_fn=lambda p: "SCORE: 0.95 VERDICT: FAIL bad")
    result = judge.evaluate(_FakeTrace())
    assert not result.passed  # verdict overrides a high score

    judge = LLMJudgeEvaluator(complete_fn=lambda p: "looks fine to me")
    result = judge.evaluate(_FakeTrace())
    assert result.score == 0.0 and not result.passed
    assert result.details["parse_error"]

    judge = LLMJudgeEvaluator(complete_fn=lambda p: (_ for _ in ()).throw(RuntimeError("down")))
    result = judge.evaluate(_FakeTrace())
    assert result.score == 0.0 and "error" in result.details

    try:
        LLMJudgeEvaluator(complete_fn=None)
        assert False, "judge must be explicitly configured"
    except ValueError:
        pass


# --- governance score -----------------------------------------------------------------


def test_governance_score_transparency(client, api_key):
    session = _setup(
        client, api_key, "ev-score",
        models=[{"provider": "scripted", "model_id": "ev-score-model"}],
        data_classes=["INTERNAL"], description="Scores the release pipeline.",
        policies=["production-autonomy"], tools=["fs.read"],
        organization="acme-corp")
    run = _clean_run(client, api_key, session)
    client.post("/v1/systems/ev-score/evaluate", json={}, headers=_headers(api_key))

    score = client.get("/v1/systems/ev-score/score", headers=_headers(api_key)).json()
    assert score["system_id"] == "ev-score"
    assert 0 <= score["overall"] <= 100
    assert sum(score["weights"].values()) == 100
    assert score["formula"]

    components = {c["name"]: c for c in score["components"]}
    assert set(components) == set(score["weights"])

    # every component is checks-with-reasons, not a bare number
    for name, component in components.items():
        assert component["weight"] == score["weights"][name]
        if component["score"] is not None:
            assert component["checks"], f"{name} scored without checks"
            for check in component["checks"]:
                assert check["reason"]

    # identity is fully evidenced for a complete registration
    identity = components["identity"]
    assert identity["score"] == 100.0
    assert all(c["passed"] for c in identity["checks"])

    # model approval cites the registry asset
    model_component = components["model_approval"]
    assert model_component["score"] == 100.0
    approved_checks = [c for c in model_component["checks"]
                       if c["check"].startswith("model_approved:")]
    assert approved_checks and approved_checks[0]["evidence"]

    # documentation is complete for this system
    assert components["documentation"]["score"] == 100.0

    # the overall follows the documented formula over scored components
    scored = [c for c in score["components"] if c["score"] is not None]
    expected = round(sum(c["score"] * c["weight"] for c in scored)
                     / sum(c["weight"] for c in scored), 1)
    assert score["overall"] == expected
    assert score["state"] in ("ok", "warn", "fail")


def test_governance_score_fails_unapproved_model_and_gaps(client, api_key):
    _setup(client, api_key, "ev-score-bad",
           models=[{"provider": "openai", "model_id": "gpt-unknown"}])
    score = client.get("/v1/systems/ev-score-bad/score",
                       headers=_headers(api_key)).json()
    components = {c["name"]: c for c in score["components"]}
    model_component = components["model_approval"]
    assert model_component["state"] == "fail"
    failed = [c for c in model_component["checks"] if c["passed"] is False]
    assert any("NOT REGISTERED" in c["reason"] for c in failed)

    # no runs yet -> evidence-dependent components are insufficient, not 100
    assert "evaluation" in score["insufficient_evidence"]
    assert components["evaluation"]["score"] is None
    assert components["evaluation"]["state"] == "insufficient_evidence"
    # documentation gaps surface honestly
    doc = components["documentation"]
    assert doc["score"] < 100
    assert any(not c["passed"] and "description" in c["check"] for c in doc["checks"])


def test_score_tenant_isolation(client, api_key, other_tenant_api_key):
    _setup(client, api_key, "ev-score-iso")
    assert client.get("/v1/systems/ev-score-iso/score",
                      headers=_headers(other_tenant_api_key)).status_code == 404
    assert client.post("/v1/systems/ev-score-iso/evaluate", json={},
                       headers=_headers(other_tenant_api_key)).status_code == 404
