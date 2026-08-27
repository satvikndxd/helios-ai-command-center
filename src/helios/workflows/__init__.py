"""
Helios workflow layer — domain-adaptable governed AI workflows.

    One governed AI runtime. Multiple enterprise workflows.
    The domain changes. The governance does not.

Layout:

  types.py     Typed configuration schemas: WorkspaceConfig,
               WorkflowDefinition, Evidence/Claim models, execution result
  analysis.py  Deterministic structured-data analysis (the LLM is never the
               source of truth for arithmetic)
  engine.py    Workflow engine — runs the reusable pipeline THROUGH the
               existing Helios governance (sentinel, policy, router, traces,
               evaluation, approvals); no parallel security path
  briefs.py    Reusable cross-domain operational brief aggregation
  registry.py  Workspace/workflow registry assembled from packs
  packs/       Configuration-driven domain packs (engineering, software,
               finance) with synthetic demo data — the core contains no
               industry conditionals
  seeding.py   Demo environment: workspaces + synthetic sources + RAG +
               knowledge-graph relationships
"""
