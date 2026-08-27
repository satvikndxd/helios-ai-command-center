"""
Engineering workspace pack — the primary reference implementation.

ALL DATA IS SYNTHETIC.  No confidential, proprietary, or third-party data.
The synthetic storyline (deliberately relational):

  Test Run 104 (battery module BM-2000) shows a thermal anomaly
    -> maintenance event ME-12 replaces coolant pump P-7
    -> incident report INC-7 documents the thermal event
    -> historical incident INC-3 (two quarters earlier) was similar
  Test Run 105 runs after maintenance with degraded cooling flow.
"""

from __future__ import annotations

from helios.workflows.analysis import (
    compare_records,
    facts_from_comparison,
    threshold_violations,
)
from helios.workflows.briefs import make_brief_step
from helios.workflows.types import (
    ActionSpec,
    ApprovalConfig,
    Fact,
    ReasoningConfig,
    RetrievalConfig,
    RiskRule,
    WorkflowDefinition,
    WorkspaceConfig,
    WorkspacePack,
)

# Config-driven thresholds — not hard-coded in the engine.
THRESHOLDS = {
    "max_temp_c": {"max": 60.0},
    "internal_resistance_mohm": {"max": 30.0},
    "capacity_retention_pct": {"min": 90.0},
    "cooling_flow_lpm": {"min": 10.0},
}

TEST_RUN_PARAMS = [
    "max_temp_c", "avg_temp_c", "peak_voltage_v", "internal_resistance_mohm",
    "capacity_retention_pct", "cycle_count", "cooling_flow_lpm",
]


# -- deterministic analysis steps -----------------------------------------


def _find_run(sources, run_id):
    for source in sources:
        if source.type == "test_runs" and str(source.record.get("run_id")) == str(run_id):
            return source
    return None


def compare_test_runs(input_data: dict, sources: list, workspace) -> dict:
    run_a = _find_run(sources, input_data["run_a"])
    run_b = _find_run(sources, input_data["run_b"])
    if run_a is None or run_b is None:
        missing = [r for r, s in
                   [(input_data["run_a"], run_a), (input_data["run_b"], run_b)]
                   if s is None]
        return {"error": f"test run(s) not found: {missing}"}

    rows = compare_records(run_a.record, run_b.record, TEST_RUN_PARAMS)
    facts = facts_from_comparison(rows, notable_pct=10.0)

    violations = threshold_violations(run_b.record, THRESHOLDS)
    facts.append(
        Fact(
            name="threshold_violations",
            value=len(violations),
            detail="; ".join(
                f"{v['parameter']}={v['value']} {v['kind']} limit {v['limit']}"
                for v in violations
            ) or "no configured threshold exceeded",
        )
    )
    used = [
        {"id": s.id, "name": s.name, "trust": s.trust,
         "excerpt": f"run_id={s.record.get('run_id')} module={s.record.get('module_id')}"}
        for s in (run_a, run_b)
    ]
    return {"facts": facts,
            "tables": {"comparison": rows, "violations": violations,
                       "used_sources": used}}


def incident_context(input_data: dict, sources: list, workspace) -> dict:
    incident_id = str(input_data["incident_id"])
    incident = next(
        (s for s in sources
         if s.type == "incident_reports" and str(s.record.get("incident_id")) == incident_id),
        None,
    )
    if incident is None:
        return {"error": f"incident '{incident_id}' not found"}

    component = incident.record.get("component")
    related_incidents = [
        s for s in sources
        if s.type == "incident_reports"
        and s.record.get("component") == component
        and str(s.record.get("incident_id")) != incident_id
    ]
    maintenance = [
        s for s in sources
        if s.type == "maintenance_logs" and s.record.get("component") == component
    ]
    runs = [
        s for s in sources
        if s.type == "test_runs" and s.record.get("run_id") == incident.record.get("test_run_id")
    ]

    facts = [
        Fact(name="incident_component", value=component,
             detail=f"{incident_id} affects component '{component}'",
             kind="fact"),
        Fact(name="related_historical_incidents", value=len(related_incidents),
             detail="; ".join(s.record.get("incident_id", "?") for s in related_incidents) or None),
        Fact(name="related_maintenance_events", value=len(maintenance),
             detail="; ".join(s.record.get("event_id", "?") for s in maintenance) or None),
        Fact(name="related_test_runs", value=len(runs),
             detail="; ".join(str(s.record.get("run_id")) for s in runs) or None),
    ]
    used = [
        {"id": s.id, "name": s.name, "trust": s.trust,
         "excerpt": s.record.get("summary") or s.name}
        for s in [incident, *related_incidents, *maintenance, *runs]
    ]
    return {"facts": facts, "tables": {"used_sources": used}}


noop_step = lambda input_data, sources, workspace: {"facts": []}  # noqa: E731


# -- workflow definitions --------------------------------------------------

WORKFLOWS = [
    WorkflowDefinition(
        id="test_run_comparison",
        name="Test Run Comparison",
        description="Deterministically compare two test runs; flag anomalies and threshold violations for engineering review.",
        input_schema={"required": ["run_a", "run_b"]},
        source_types=["test_runs"],
        analysis_steps=["compare_test_runs"],
        retrieval=RetrievalConfig(
            enabled=True,
            query_template="battery thermal management cooling test run comparison",
            top_k=3,
        ),
        reasoning=ReasoningConfig(
            task_template="Compare Test Run {run_a} and Test Run {run_b} and identify anything requiring engineering review.",
        ),
        base_risk="low",
        risk_rules=[
            RiskRule(fact="threshold_violations", op="nonzero", risk="high"),
            RiskRule(fact="notable_changes", op="gte", value=3, risk="medium"),
        ],
        approval=ApprovalConfig(action="create_engineering_review"),
    ),
    WorkflowDefinition(
        id="incident_investigation",
        name="Incident Investigation",
        description="Investigate an incident: related history, maintenance, test runs, documentation. Contributing factors, not premature root cause.",
        input_schema={"required": ["incident_id"]},
        source_types=["incident_reports", "maintenance_logs", "test_runs"],
        analysis_steps=["incident_context"],
        retrieval=RetrievalConfig(
            enabled=True,
            query_template="incident {incident_id} thermal cooling failure procedures",
            top_k=4,
        ),
        reasoning=ReasoningConfig(
            task_template=(
                "Investigate incident {incident_id}. Identify POTENTIAL CONTRIBUTING "
                "FACTORS (never a definitive root cause unless the evidence proves it), "
                "note uncertainty, and recommend investigation steps."
            ),
        ),
        base_risk="medium",
        risk_rules=[RiskRule(fact="related_historical_incidents", op="gte", value=1, risk="high")],
        approval=ApprovalConfig(action="create_engineering_review"),
    ),
    WorkflowDefinition(
        id="daily_brief",
        name="Engineering Daily Brief",
        description="Aggregate recent runs, anomalies, incidents, and maintenance into a structured operations brief.",
        input_schema={},
        source_types=["test_runs", "incident_reports", "maintenance_logs"],
        analysis_steps=["brief_aggregate"],
        retrieval=RetrievalConfig(enabled=False),
        reasoning=ReasoningConfig(
            task_template=(
                "Produce an ENGINEERING OPERATIONS BRIEF: critical items, notable "
                "changes, recurring patterns, unresolved items, and human-review items."
            ),
        ),
        base_risk="informational",
        risk_rules=[RiskRule(fact="brief_critical", op="nonzero", risk="medium")],
    ),
    WorkflowDefinition(
        id="knowledge_assistant",
        name="Technical Knowledge Assistant",
        description="Grounded Q&A over SOPs, manuals, safety procedures, and incident history — with citations.",
        input_schema={"required": ["question"]},
        source_types=[],
        require_sources=False,
        analysis_steps=[],
        retrieval=RetrievalConfig(enabled=True, query_template="{question}", top_k=4),
        reasoning=ReasoningConfig(
            task_template="Answer the engineer's question using ONLY the retrieved evidence: {question}",
        ),
        base_risk="informational",
    ),
]

CONFIG = WorkspaceConfig(
    id="engineering",
    name="Engineering",
    description="Battery/module test engineering operations (synthetic demo data).",
    domain="industrial-engineering",
    terminology={
        "run": "test run", "module": "battery module",
        "incident": "incident report", "SOP": "standard operating procedure",
    },
    system_instructions=(
        "You are the Helios engineering analyst. Numbers come ONLY from the "
        "COMPUTED FACTS block. Distinguish facts, computations, interpretation, "
        "and recommendations. Cite evidence. Flag uncertainty explicitly."
    ),
    source_types=["test_runs", "incident_reports", "maintenance_logs",
                  "sensor_data", "technical_manuals", "safety_procedures"],
    capabilities=["comparison", "investigation", "brief", "knowledge"],
    workflows=WORKFLOWS,
    actions=[
        ActionSpec(
            name="create_engineering_review",
            risk="high",
            description="Create an engineering review task for a flagged finding",
        )
    ],
    risk_config={"thresholds": THRESHOLDS},
    metadata={"synthetic": True},
)


# -- synthetic demo data ---------------------------------------------------

SEED_SOURCES = [
    {"name": "Test Run 101", "type": "test_runs",
     "record": {"run_id": 101, "module_id": "BM-2000", "max_temp_c": 46.1,
                "avg_temp_c": 38.9, "peak_voltage_v": 4.16,
                "internal_resistance_mohm": 24.0, "capacity_retention_pct": 97.2,
                "cycle_count": 120, "cooling_flow_lpm": 14.8}},
    {"name": "Test Run 102", "type": "test_runs",
     "record": {"run_id": 102, "module_id": "BM-2000", "max_temp_c": 47.0,
                "avg_temp_c": 39.2, "peak_voltage_v": 4.15,
                "internal_resistance_mohm": 24.6, "capacity_retention_pct": 96.8,
                "cycle_count": 180, "cooling_flow_lpm": 14.5}},
    {"name": "Test Run 104", "type": "test_runs",
     "record": {"run_id": 104, "module_id": "BM-2000", "max_temp_c": 48.2,
                "avg_temp_c": 39.8, "peak_voltage_v": 4.15,
                "internal_resistance_mohm": 25.1, "capacity_retention_pct": 96.1,
                "cycle_count": 240, "cooling_flow_lpm": 14.2}},
    {"name": "Test Run 105", "type": "test_runs",
     "record": {"run_id": 105, "module_id": "BM-2000", "max_temp_c": 61.3,
                "avg_temp_c": 44.6, "peak_voltage_v": 4.11,
                "internal_resistance_mohm": 31.4, "capacity_retention_pct": 93.5,
                "cycle_count": 300, "cooling_flow_lpm": 8.9}},
    {"name": "Incident INC-7 — thermal excursion during Run 104", "type": "incident_reports",
     "record": {"incident_id": "INC-7", "component": "coolant-pump-P7",
                "test_run_id": 104, "status": "open", "severity": "high",
                "summary": "Thermal excursion during endurance segment of Run 104; cell group 3 exceeded expected temperature envelope."}},
    {"name": "Incident INC-3 — coolant flow degradation", "type": "incident_reports",
     "record": {"incident_id": "INC-3", "component": "coolant-pump-P7",
                "test_run_id": 88, "status": "resolved", "severity": "medium",
                "summary": "Gradual coolant flow degradation traced to partial impeller wear in pump P-7; resolved by pump service."}},
    {"name": "Maintenance ME-12 — coolant pump replacement", "type": "maintenance_logs",
     "record": {"event_id": "ME-12", "component": "coolant-pump-P7",
                "action": "replaced", "status": "completed",
                "summary": "Replaced coolant pump P-7 following INC-7 thermal excursion."}},
]

SEED_DOCUMENTS = [
    {"title": "Battery Thermal Management Manual (synthetic)",
     "content": (
         "Battery module BM-2000 thermal design. The liquid cooling loop must "
         "maintain a coolant flow of at least 10 litres per minute during "
         "endurance testing. Sustained cell temperatures above 60 C indicate "
         "thermal stress and require an engineering review of cooling-system "
         "performance. Internal resistance above 30 mohm suggests cell aging "
         "or thermal damage. Pump P-7 impeller wear is a known cause of "
         "gradual coolant flow degradation.")},
    {"title": "SOP: Cooling System Inspection (synthetic)",
     "content": (
         "Standard operating procedure for cooling system inspection. Step 1: "
         "verify coolant flow at the pump outlet. Step 2: inspect pump P-7 "
         "impeller for wear. Step 3: check heat-exchanger fins for blockage. "
         "Step 4: record flow, pressure, and temperature readings in the test "
         "log. A flow reading below 10 lpm requires immediate escalation to "
         "the thermal engineering team.")},
    {"title": "Safety Procedure: Thermal Events (synthetic)",
     "content": (
         "Safety procedure for battery thermal events. If cell temperature "
         "exceeds 60 C, stop the active test segment, engage auxiliary "
         "cooling, and quarantine the module. File an incident report and do "
         "not resume testing until an engineering review has approved "
         "corrective actions.")},
]

SEED_RELATIONSHIPS = [
    {"source": ("TestRun-104", "TestRun"), "relationship_type": "involves",
     "target": ("BM-2000", "BatteryModule")},
    {"source": ("TestRun-105", "TestRun"), "relationship_type": "involves",
     "target": ("BM-2000", "BatteryModule")},
    {"source": ("INC-7", "Incident"), "relationship_type": "occurred_during",
     "target": ("TestRun-104", "TestRun")},
    {"source": ("INC-7", "Incident"), "relationship_type": "associated_with",
     "target": ("coolant-pump-P7", "Component")},
    {"source": ("INC-3", "Incident"), "relationship_type": "associated_with",
     "target": ("coolant-pump-P7", "Component")},
    {"source": ("ME-12", "MaintenanceEvent"), "relationship_type": "addressed",
     "target": ("coolant-pump-P7", "Component")},
]

PACK = WorkspacePack(
    config=CONFIG,
    analysis_steps={
        "compare_test_runs": compare_test_runs,
        "incident_context": incident_context,
        "brief_aggregate": make_brief_step([
            {"source_type": "incident_reports", "field": "status", "op": "eq",
             "value": "open", "bucket": "critical", "label": "Open incident"},
            {"source_type": "test_runs", "field": "max_temp_c", "op": "gt",
             "value": 60.0, "bucket": "requires_review",
             "label": "Test run above thermal limit"},
            {"source_type": "maintenance_logs", "field": "status", "op": "eq",
             "value": "completed", "bucket": "informational",
             "label": "Completed maintenance"},
        ]),
    },
    seed_sources=SEED_SOURCES,
    seed_documents=SEED_DOCUMENTS,
    seed_relationships=SEED_RELATIONSHIPS,
)
