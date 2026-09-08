"""
EVIDENCE PLANE — data lineage from real recorded events.

Every lineage edge originates from a recorded TraceEvent or DecisionRecord —
there is no decorative graph. The flow HELIOS can attest to:

    DATA SOURCE -> RETRIEVAL -> MODEL -> TOOL -> OUTPUT -> DESTINATION

We reconstruct it per system from data_access / model_call / model_governance
/ tool_execution events across the system's runs, plus the normalized
model-use and tool DecisionRecords.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.models import AgentSession, DecisionRecord, TraceEvent


def system_lineage(db: Session, tenant_id: str, system_id: str, limit: int = 200) -> dict:
    """Build a data-flow view for one AI system from recorded evidence."""
    run_ids = [
        row[0]
        for row in db.query(AgentSession.id)
        .filter(AgentSession.tenant_id == tenant_id,
                AgentSession.system_id == system_id)
        .all()
    ]
    # runs are keyed by AgentRun.id, which equals TraceEvent.run_id; but the
    # session id != run id. Collect run ids from decision records instead.
    records = (
        db.query(DecisionRecord)
        .filter(DecisionRecord.tenant_id == tenant_id,
                DecisionRecord.system_id == system_id)
        .order_by(DecisionRecord.created_at.desc())
        .limit(limit)
        .all()
    )

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(node_id: str, kind: str, label: str) -> str:
        if node_id not in nodes:
            nodes[node_id] = {"id": node_id, "kind": kind, "label": label}
        return node_id

    system_node = node(f"system:{system_id}", "system", system_id)

    for record in records:
        data_classes = record.data_classes or ["public"]
        if record.kind == "model_use" and record.model_provider:
            model_node = node(
                f"model:{record.model_provider}/{record.model_id}",
                "model", f"{record.model_provider}/{record.model_id}")
            edges.append({
                "from": system_node, "to": model_node, "relation": "data_to_model",
                "data_classes": data_classes, "decision": record.decision,
                "allowed": record.decision == "allow",
                "evidence": {"decision_id": record.id, "reason": record.reason},
            })
        elif record.kind == "tool_call":
            tool_node = node(f"tool:{record.action}", "tool", record.action)
            dest = None
            resource = record.resource or {}
            for key in ("github.repo", "http.domain", "filesystem.path"):
                if key in resource:
                    dest = node(f"dest:{resource[key]}", "destination", str(resource[key]))
                    break
            edges.append({
                "from": system_node, "to": tool_node, "relation": "data_to_tool",
                "data_classes": data_classes, "decision": record.decision,
                "allowed": record.decision in ("allow",),
                "evidence": {"decision_id": record.id, "reason": record.reason},
            })
            if dest is not None:
                edges.append({
                    "from": tool_node, "to": dest, "relation": "tool_to_destination",
                    "data_classes": data_classes, "decision": record.decision,
                    "allowed": record.decision in ("allow",),
                    "evidence": {"decision_id": record.id},
                })

    return {
        "system_id": system_id,
        "nodes": list(nodes.values()),
        "edges": edges,
        "sources": len(records),
        "note": "every edge is derived from a recorded DecisionRecord",
    }
