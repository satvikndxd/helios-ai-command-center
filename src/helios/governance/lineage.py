"""
Data lineage derivation (EVIDENCE plane).

    DATA SOURCE -> RETRIEVAL -> MODEL -> TOOL -> OUTPUT -> DESTINATION

Hard rule: **every edge is derived from recorded TraceEvents and cites their
ids.** There is no stored lineage table, no synthetic graph, and no decorative
visualization data — if an event was not recorded, the edge does not exist.

Event sources:
    retrieval / data_access (inbound)   a source fed data into the run context
    policy_evaluation model_preflight:* what classes the model was cleared for
                                        and whether the call was allowed
    model_call                          the model actually received the context
    tool_proposal (+ child policy/governance/outcome events)
                                        a proposed flow model -> tool
    tool_execution                      the flow executed
    data_access (outbound)              data flowed to a destination

Edges carry: endpoints, effective data class, the decision that authorized
(or blocked) the flow, and the event ids that prove it. `violations` are
edges that were blocked or whose recorded checks failed — evidence, not
speculation.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.governance.data import SEVERITY, max_class, normalize_class
from helios.models import TraceEvent


def _node(kind: str, ident: str, **attrs) -> dict:
    return {"id": f"{kind}:{ident}", "kind": kind, "ident": ident, **attrs}


def _edge(src: dict, dst: dict, *, kind: str, data_class: str, event_ids: list,
          decision: str, allowed: bool, reasons: list[str] | None = None) -> dict:
    return {
        "from": src["id"], "to": dst["id"], "kind": kind,
        "data_class": data_class,
        "event_ids": list(event_ids),
        "decision": decision,
        "allowed": allowed,
        "reasons": list(reasons or []),
    }


def _resource_ident(prefix: str, resource: dict) -> str:
    if not resource:
        return prefix
    parts = [f"{k}={v}" for k, v in sorted(resource.items())]
    return f"{prefix}({' , '.join(parts)})".replace(" , ", ",")


def _events_for_run(db: Session, tenant_id: str, run_id: str) -> list[TraceEvent]:
    return (
        db.query(TraceEvent)
        .filter(TraceEvent.tenant_id == tenant_id, TraceEvent.run_id == run_id)
        .order_by(TraceEvent.seq)
        .all()
    )


def build_run_lineage(db: Session, tenant_id: str, run_id: str) -> dict:
    """Derive the data-flow graph of one run exclusively from its events."""
    events = _events_for_run(db, tenant_id, run_id)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    violations: list[dict] = []

    run_node = _node("run", run_id)
    nodes[run_node["id"]] = run_node
    system_id = next((e.system_id for e in events if e.system_id), None)
    if system_id:
        sys_node = _node("system", system_id)
        nodes[sys_node["id"]] = sys_node

    def add(node: dict) -> dict:
        nodes.setdefault(node["id"], node)
        return nodes[node["id"]]

    # pending inbound sources waiting for the next model call
    pending_sources: list[tuple[dict, str, TraceEvent]] = []
    last_model: dict | None = None
    last_preflight: dict | None = None   # payload of most recent model_preflight
    preflight_event_id: str | None = None
    proposals: dict[str, dict] = {}      # proposal event id -> info
    emitted_proposals: set[str] = set()  # proposals that got an outbound edge
    executed_hashes: set[str] = set()    # args_hashes that actually executed

    def flush_sources_to_model(model_node: dict, allowed: bool, decision: str,
                               reasons: list[str], extra_event_ids: list[str]) -> None:
        for source_node, source_class, source_event in pending_sources:
            edge = _edge(
                source_node, model_node, kind="data_to_model",
                data_class=source_class,
                event_ids=[source_event.id,
                           *[e for e in extra_event_ids if e]],
                decision=decision, allowed=allowed, reasons=reasons,
            )
            edges.append(edge)
            if not edge["allowed"]:
                violations.append(edge)
        pending_sources.clear()

    for event in events:
        payload = event.payload or {}
        etype = event.event_type

        if etype in ("retrieval", "data_access") and payload.get("direction") == "inbound":
            detected = payload.get("classes_detected") or []
            source_class = max_class(detected) if detected else "PUBLIC"
            ident = _resource_ident(payload.get("source_kind", "source"),
                                    payload.get("source") or {})
            source_node = add(_node("source", ident,
                                    source_kind=payload.get("source_kind"),
                                    tool=event.name))
            edges.append(_edge(run_node, source_node, kind="accessed",
                               data_class=source_class, event_ids=[event.id],
                               decision="recorded", allowed=True))
            pending_sources.append((source_node, source_class, event))

        elif etype == "policy_evaluation" and str(event.name).startswith("model_preflight:"):
            last_preflight = payload
            preflight_event_id = event.id
            model_info = payload.get("model") or {}
            model_ident = f"{model_info.get('provider', '?')}/{model_info.get('model_id', '?')}"
            model_node = add(_node("model", model_ident,
                                   provider=model_info.get("provider"),
                                   model_id=model_info.get("model_id"),
                                   approval_status=model_info.get("approval_status")))
            classification = payload.get("data_classification") or {}
            allowed = payload.get("decision") == "allow"
            reasons = payload.get("reasons") or []
            if not allowed:
                # a denied preflight still produced intended flows: record them
                # as blocked edges citing the denial event
                flush_sources_to_model(
                    model_node, allowed=False,
                    decision=payload.get("decision", "deny"),
                    reasons=reasons, extra_event_ids=[event.id])
            last_model = model_node

        elif etype == "model_call":
            model_ident = f"{payload.get('provider', '?')}/{payload.get('model', '?')}"
            model_node = add(_node("model", model_ident,
                                   provider=payload.get("provider"),
                                   model_id=payload.get("model")))
            preflight = (payload.get("preflight") or {})
            classification = preflight.get("data_classification") or \
                (last_preflight or {}).get("data_classification") or {}
            effective = classification.get("effective")
            decision = "allow" if preflight.get("decision", "allow") == "allow" else "deny"
            flush_sources_to_model(
                model_node, allowed=(decision == "allow"), decision=decision,
                reasons=[],
                extra_event_ids=[preflight_event_id, event.id])
            if effective:
                model_node.setdefault("data_classes_seen", [])
                if effective not in model_node["data_classes_seen"]:
                    model_node["data_classes_seen"].append(effective)
            last_model = model_node

        elif etype == "tool_proposal":
            classification = payload.get("data_classification") or {}
            proposals[event.id] = {
                "tool": payload.get("tool") or event.name,
                "class": classification.get("effective", "PUBLIC"),
                "args_hash": payload.get("args_hash"),
                "decision": None, "reasons": [], "executed": False,
                "children": [],
            }

        elif event.parent_id in proposals and etype in (
                "policy_evaluation", "governance_evaluation"):
            info = proposals[event.parent_id]
            decision = payload.get("decision")
            if decision in ("deny", "require_approval") or event.status == "denied":
                if info["decision"] is None or decision == "deny":
                    info["decision"] = decision
                    info["reasons"].append(payload.get("reason", ""))
            if info["decision"] is None:
                info["decision"] = decision or "allow"

        elif event.parent_id in proposals and etype == "tool_execution":
            info = proposals[event.parent_id]
            info["executed"] = event.status in ("ok", "replayed")
            info["children"].append(event.id)
            info["execution_status"] = event.status
            if info["executed"] and info.get("args_hash"):
                executed_hashes.add(info["args_hash"])

        elif event.parent_id in proposals and etype == "outcome" \
                and event.status == "denied":
            info = proposals[event.parent_id]
            info["decision"] = info["decision"] or "deny"
            info["reasons"].append(payload.get("reason", ""))
            info["children"].append(event.id)

        elif etype == "data_access" and payload.get("direction") == "outbound":
            dest_ident = _resource_ident(payload.get("source_kind", "destination"),
                                         payload.get("destination") or {})
            dest_node = add(_node("destination", dest_ident,
                                  source_kind=payload.get("source_kind"),
                                  tool=event.name))
            info = proposals.get(event.parent_id)
            tool_node = add(_node("tool", info["tool"] if info else event.name))
            data_class = payload.get("data_class", "PUBLIC")
            if last_model is not None:
                edges.append(_edge(
                    last_model, tool_node, kind="model_to_tool",
                    data_class=data_class,
                    event_ids=[event.parent_id, *info["children"]] if info
                    else [event.id],
                    decision=(info or {}).get("decision") or "allow",
                    allowed=bool((info or {}).get("executed")),
                    reasons=(info or {}).get("reasons") or [],
                ))
                if info:
                    emitted_proposals.add(event.parent_id)
            edges.append(_edge(
                tool_node, dest_node, kind="output_to_destination",
                data_class=data_class, event_ids=[event.id],
                decision="executed" if (info or {}).get("executed") else "recorded",
                allowed=True,
            ))

    # model -> tool edges for proposals whose flow was not already emitted via
    # an outbound data_access event (reads, denied attempts, errors, gates)
    for proposal_id, info in proposals.items():
        if proposal_id in emitted_proposals or last_model is None:
            continue
        tool_node = add(_node("tool", info["tool"]))
        gated = info["decision"] in ("require_approval", "require_human_review")
        # a gated proposal whose exact payload executed elsewhere in the run
        # (post-approval re-invocation) is AUTHORIZED — oversight worked
        authorized_via_approval = (
            gated and info.get("args_hash") in executed_hashes)
        allowed = bool(info["executed"]) or authorized_via_approval
        edge = _edge(
            last_model, tool_node, kind="model_to_tool",
            data_class=info["class"],
            event_ids=[proposal_id, *info["children"]],
            decision=info["decision"] or "allow",
            allowed=allowed,
            reasons=info["reasons"],
        )
        if gated:
            edge["gated"] = True
            edge["authorized_via_approval"] = authorized_via_approval
        edges.append(edge)
        # violations are BLOCKED flows (policy/governance denials), not gates:
        # a gate is oversight working as designed
        if not allowed and info["decision"] == "deny":
            violations.append(edge)

    classes_seen = sorted(
        {e["data_class"] for e in edges if e["data_class"] != "PUBLIC"},
        key=lambda c: -SEVERITY.get(normalize_class(c), 0),
    )
    return {
        "run_id": run_id,
        "system_id": system_id,
        "nodes": list(nodes.values()),
        "edges": edges,
        "violations": violations,
        "summary": {
            "event_count": len(events),
            "sources": sum(1 for n in nodes.values() if n["kind"] == "source"),
            "destinations": sum(1 for n in nodes.values() if n["kind"] == "destination"),
            "models": sum(1 for n in nodes.values() if n["kind"] == "model"),
            "tools": sum(1 for n in nodes.values() if n["kind"] == "tool"),
            "data_classes_seen": classes_seen,
            "violation_count": len(violations),
        },
    }


def build_system_lineage(db: Session, tenant_id: str, system_id: str,
                         *, limit_runs: int = 20) -> dict:
    """
    Aggregate lineage across a system's recent runs. Nodes are merged by id;
    every edge still cites the run and events that produced it.
    """
    from helios.models import AgentRun, AgentSession

    run_ids = [
        r.id for r in (
            db.query(AgentRun)
            .join(AgentSession, AgentRun.session_id == AgentSession.id)
            .filter(AgentSession.tenant_id == tenant_id,
                    AgentSession.system_id == system_id)
            .order_by(AgentRun.created_at.desc())
            .limit(limit_runs)
            .all()
        )
    ]
    merged_nodes: dict[str, dict] = {}
    merged_edges: list[dict] = []
    violations: list[dict] = []
    for run_id in run_ids:
        graph = build_run_lineage(db, tenant_id, run_id)
        for node in graph["nodes"]:
            if node["kind"] == "run":
                continue
            merged_nodes.setdefault(node["id"], node)
        for edge in graph["edges"]:
            if edge["kind"] == "accessed":
                continue  # run-containment edges stay per-run
            tagged = dict(edge, run_id=run_id)
            merged_edges.append(tagged)
        violations.extend(dict(v, run_id=run_id) for v in graph["violations"])

    classes_seen = sorted(
        {e["data_class"] for e in merged_edges if e["data_class"] != "PUBLIC"},
        key=lambda c: -SEVERITY.get(normalize_class(c), 0),
    )
    return {
        "system_id": system_id,
        "runs": len(run_ids),
        "nodes": list(merged_nodes.values()),
        "edges": merged_edges,
        "violations": violations,
        "summary": {
            "sources": sum(1 for n in merged_nodes.values() if n["kind"] == "source"),
            "destinations": sum(1 for n in merged_nodes.values()
                                if n["kind"] == "destination"),
            "models": sum(1 for n in merged_nodes.values() if n["kind"] == "model"),
            "tools": sum(1 for n in merged_nodes.values() if n["kind"] == "tool"),
            "data_classes_seen": classes_seen,
            "violation_count": len(violations),
        },
    }
