"""
Software Engineering workspace pack — second reference implementation.

Proves the platform is not specific to industrial engineering: same engine,
same governance, different configuration.  ALL DATA IS SYNTHETIC fixtures
(the live GitHub/web adapters remain available through the existing
governed web access plane).
"""

from __future__ import annotations

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


# -- deterministic analysis steps -----------------------------------------


def _deployments(sources):
    return sorted(
        (s for s in sources if s.type == "deployments"),
        key=lambda s: s.record.get("deploy_id", 0),
    )


def deployment_diff(input_data: dict, sources: list, workspace) -> dict:
    deploys = _deployments(sources)
    failed = next(
        (s for s in reversed(deploys) if s.record.get("status") == "failed"), None
    )
    success = next(
        (s for s in reversed(deploys) if s.record.get("status") == "succeeded"), None
    )
    if failed is None or success is None:
        return {"error": "need at least one failed and one successful deployment"}

    failed_commits = set(failed.record.get("commit_shas", []))
    success_commits = set(success.record.get("commit_shas", []))
    new_commits = sorted(failed_commits - success_commits)

    commit_sources = [
        s for s in sources
        if s.type == "commits" and s.record.get("sha") in new_commits
    ]
    services = sorted({s.record.get("service") for s in commit_sources if s.record.get("service")})

    facts = [
        Fact(name="failed_deploy", value=failed.record.get("deploy_id"),
             detail=f"deploy {failed.record.get('deploy_id')} failed at step "
                    f"'{failed.record.get('failed_step')}'", kind="fact"),
        Fact(name="last_successful_deploy", value=success.record.get("deploy_id"),
             kind="fact"),
        Fact(name="new_commits", value=len(new_commits),
             detail=", ".join(new_commits) or None),
        Fact(name="services_touched", value=len(services),
             detail=", ".join(services) or None),
        Fact(name="ci_error_lines", value=len(failed.record.get("error_lines", [])),
             detail="; ".join(failed.record.get("error_lines", [])[:3]) or None),
    ]
    used = [
        {"id": s.id, "name": s.name, "trust": s.trust,
         "excerpt": s.record.get("message") or s.record.get("failed_step") or s.name}
        for s in [failed, success, *commit_sources]
    ]
    return {"facts": facts, "tables": {"used_sources": used}}


def release_risk(input_data: dict, sources: list, workspace) -> dict:
    commits = [s for s in sources if s.type == "commits"]
    incidents = [s for s in sources if s.type == "incident_reports"]

    large = [c for c in commits if c.record.get("lines_changed", 0) >= 300]
    risky_services = {i.record.get("service") for i in incidents if i.record.get("service")}
    touching_risky = [c for c in commits if c.record.get("service") in risky_services]
    without_tests = [c for c in commits if not c.record.get("has_tests", False)]

    facts = [
        Fact(name="commits_in_release", value=len(commits)),
        Fact(name="large_changes", value=len(large),
             detail=", ".join(c.record.get("sha", "?") for c in large) or None),
        Fact(name="commits_touching_incident_prone_services", value=len(touching_risky),
             detail=", ".join(sorted(risky_services)) or None),
        Fact(name="commits_without_tests", value=len(without_tests),
             detail=", ".join(c.record.get("sha", "?") for c in without_tests) or None),
        Fact(name="historical_incidents_considered", value=len(incidents)),
    ]
    used = [
        {"id": s.id, "name": s.name, "trust": s.trust,
         "excerpt": s.record.get("message") or s.record.get("summary") or s.name}
        for s in [*commits, *incidents]
    ]
    return {"facts": facts, "tables": {"used_sources": used}}


WORKFLOWS = [
    WorkflowDefinition(
        id="deployment_failure_investigation",
        name="Deployment Failure Investigation",
        description="Why did the latest deployment fail compared with the last successful one?",
        input_schema={},
        source_types=["deployments", "commits", "ci_logs", "incident_reports"],
        analysis_steps=["deployment_diff"],
        retrieval=RetrievalConfig(
            enabled=True,
            query_template="deployment pipeline failure database migration rollback",
            top_k=3,
        ),
        reasoning=ReasoningConfig(
            task_template=(
                "Explain the likely CONTRIBUTING CHANGES behind the latest failed "
                "deployment versus the last successful one. Cite commits and CI "
                "evidence; state confidence and what to verify next."
            ),
        ),
        base_risk="medium",
        risk_rules=[RiskRule(fact="ci_error_lines", op="nonzero", risk="high")],
        approval=ApprovalConfig(action="create_review_task"),
    ),
    WorkflowDefinition(
        id="release_risk_analysis",
        name="Release Risk Analysis",
        description="Score the pending release against change size, test coverage, and incident-prone services.",
        input_schema={},
        source_types=["commits", "incident_reports"],
        analysis_steps=["release_risk"],
        retrieval=RetrievalConfig(
            enabled=True,
            query_template="release checklist incident postmortem risky services",
            top_k=3,
        ),
        reasoning=ReasoningConfig(
            task_template=(
                "Assess release risk from the computed factors and historical "
                "incidents. List risk factors with evidence and recommend review "
                "steps before shipping."
            ),
        ),
        base_risk="low",
        risk_rules=[
            RiskRule(fact="commits_touching_incident_prone_services", op="nonzero", risk="high"),
            RiskRule(fact="commits_without_tests", op="gte", value=2, risk="medium"),
        ],
        approval=ApprovalConfig(action="create_review_task"),
    ),
    WorkflowDefinition(
        id="daily_brief",
        name="Software Engineering Daily Brief",
        description="Failed builds, open incidents, high-risk changes, pending reviews.",
        input_schema={},
        source_types=["deployments", "commits", "incident_reports"],
        analysis_steps=["brief_aggregate"],
        retrieval=RetrievalConfig(enabled=False),
        reasoning=ReasoningConfig(
            task_template="Produce a SOFTWARE ENGINEERING DAILY BRIEF from the aggregated items.",
        ),
        base_risk="informational",
        risk_rules=[RiskRule(fact="brief_critical", op="nonzero", risk="medium")],
    ),
]

CONFIG = WorkspaceConfig(
    id="software",
    name="Software Engineering",
    description="Deployments, releases, and incident operations (synthetic fixtures).",
    domain="software-engineering",
    terminology={"deploy": "deployment", "PR": "pull request", "CI": "continuous integration"},
    system_instructions=(
        "You are the Helios software delivery analyst. Numbers come ONLY from "
        "COMPUTED FACTS. Distinguish evidence from inference; state confidence."
    ),
    source_types=["deployments", "commits", "ci_logs", "incident_reports",
                  "architecture_docs"],
    capabilities=["investigation", "risk-analysis", "brief"],
    workflows=WORKFLOWS,
    actions=[
        ActionSpec(name="create_review_task", risk="medium",
                   description="Create a review task for a flagged release/deployment finding"),
    ],
    metadata={"synthetic": True},
)

SEED_SOURCES = [
    {"name": "Deploy 41 (succeeded)", "type": "deployments",
     "record": {"deploy_id": 41, "status": "succeeded",
                "commit_shas": ["a1f9", "b2c3"], "duration_s": 412}},
    {"name": "Deploy 42 (failed)", "type": "deployments",
     "record": {"deploy_id": 42, "status": "failed",
                "commit_shas": ["a1f9", "b2c3", "c4d5", "e6f7"],
                "failed_step": "db-migration",
                "error_lines": [
                    "alembic.util.exc.CommandError: Can't locate revision 9f2a",
                    "ERROR: migration 0042_add_orders_index failed",
                ],
                "duration_s": 187}},
    {"name": "Commit c4d5 — add orders index migration", "type": "commits",
     "record": {"sha": "c4d5", "service": "orders-service",
                "message": "add composite index to orders table (migration 0042)",
                "lines_changed": 96, "has_tests": False}},
    {"name": "Commit e6f7 — payment retry refactor", "type": "commits",
     "record": {"sha": "e6f7", "service": "payments-service",
                "message": "refactor payment retry queue", "lines_changed": 412,
                "has_tests": True}},
    {"name": "Incident SW-INC-19 — orders DB lock", "type": "incident_reports",
     "record": {"incident_id": "SW-INC-19", "service": "orders-service",
                "status": "resolved", "severity": "high",
                "summary": "Long-running migration locked the orders table during peak traffic."}},
    {"name": "Incident SW-INC-23 — deploy rollback", "type": "incident_reports",
     "record": {"incident_id": "SW-INC-23", "service": "payments-service",
                "status": "open", "severity": "medium",
                "summary": "Deploy 42 rollback left payment retry consumers paused."}},
]

SEED_DOCUMENTS = [
    {"title": "Architecture: Orders Service (synthetic)",
     "content": (
         "The orders service owns the orders table. Schema migrations run "
         "through alembic during the db-migration deploy step. Long-running "
         "migrations against the orders table have previously caused table "
         "locks during peak traffic (see incident SW-INC-19). Index changes "
         "must use CONCURRENTLY and ship behind a feature flag.")},
    {"title": "Runbook: Deployment Rollback (synthetic)",
     "content": (
         "When a deploy fails at the db-migration step, verify the alembic "
         "revision graph, restore the previous revision, and resume paused "
         "queue consumers. A missing revision error usually means a commit "
         "was cherry-picked without its migration parent.")},
]

SEED_RELATIONSHIPS = [
    {"source": ("Deploy-42", "Deployment"), "relationship_type": "contains",
     "target": ("c4d5", "Commit")},
    {"source": ("Deploy-42", "Deployment"), "relationship_type": "contains",
     "target": ("e6f7", "Commit")},
    {"source": ("c4d5", "Commit"), "relationship_type": "modifies",
     "target": ("orders-service", "Service")},
    {"source": ("e6f7", "Commit"), "relationship_type": "modifies",
     "target": ("payments-service", "Service")},
    {"source": ("SW-INC-23", "Incident"), "relationship_type": "caused_by_or_related_to",
     "target": ("Deploy-42", "Deployment")},
]

PACK = WorkspacePack(
    config=CONFIG,
    analysis_steps={
        "deployment_diff": deployment_diff,
        "release_risk": release_risk,
        "brief_aggregate": make_brief_step([
            {"source_type": "deployments", "field": "status", "op": "eq",
             "value": "failed", "bucket": "critical", "label": "Failed deployment"},
            {"source_type": "incident_reports", "field": "status", "op": "eq",
             "value": "open", "bucket": "critical", "label": "Open incident"},
            {"source_type": "commits", "field": "lines_changed", "op": "gte",
             "value": 300, "bucket": "requires_review", "label": "Large change"},
        ]),
    },
    seed_sources=SEED_SOURCES,
    seed_documents=SEED_DOCUMENTS,
    seed_relationships=SEED_RELATIONSHIPS,
)
