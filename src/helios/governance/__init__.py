"""
HELIOS governance — the V1.5 product core.

Four planes, one package:

    IDENTITY   systems.py, models_registry.py     who/what is this AI system?
    POLICY     autonomy.py, data.py, policy.py,   what may it do?
               engine.py
    EVIDENCE   decisions.py, lineage.py,          what did it actually do?
               oversight.py
    ASSURANCE  evaluators.py, score.py,           does it remain within policy?
               changes.py, drift.py, audit.py

Everything here reuses the V1 primitives (ToolBroker, ToolPolicy DNA,
TraceRecorder/TraceEvent, ApprovalRequest hash binding, replay, evaluators)
and reorganizes them under governance. Every status, score, and explanation
produced by this package is derived from stored evidence — never decorative.
"""
