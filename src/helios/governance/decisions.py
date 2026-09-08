"""
AI Decision Records (EVIDENCE plane).

`build_for_run` projects a run's TraceEvents into normalized DecisionRecords:

    tool_proposal (+children)   -> one record per action
                                   (EXECUTED | DENIED | APPROVAL_REQUIRED | ERROR)
    denied model_preflight      -> one record (DENIED / APPROVAL_REQUIRED)
    terminal outcome            -> one run_outcome record
                                   (COMPLETED | BLOCKED | FAILED | CANCELLED)

Rules:
* Records are derived ONLY from stored events — `source_event_id` cites the
  event each record projects, and every id in `evidence.trace_event_ids`
  resolves to a real TraceEvent. No record without evidence.
* Builds are idempotent upserts keyed by (tenant, run, source_event): as a
  run progresses (approval decided, resume executes), the record is refreshed
  from the newest events — events stay immutable, the projection converges.
* `explain(record)` assembles the governance WHY from the record's stored
  policy/oversight/classification state — generated from actual evidence,
  never canned text.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from helios.models import DecisionRecord, TraceEvent

SCHEMA_VERSION = "helios-decision-record-v1"

TOOL_DECISIONS = {"executed": "EXECUTED", "denied": "DENIED",
                  "approval_required": "APPROVAL_REQUIRED", "error": "ERROR"}
RUN_DECISIONS = {"completed": "COMPLETED", "blocked": "BLOCKED",
                 "failed": "FAILED", "cancelled": "CANCELLED"}


def _events_for_run(db: Session, tenant_id: str, run_id: str) -> list[TraceEvent]:
    return (
        db.query(TraceEvent)
        .filter(TraceEvent.tenant_id == tenant_id, TraceEvent.run_id == run_id)
        .order_by(TraceEvent.seq)
        .all()
    )


def _children_by_parent(events: list[TraceEvent]) -> dict[str, list[TraceEvent]]:
    grouped: dict[str, list[TraceEvent]] = {}
    for event in events:
        if event.parent_id:
            grouped.setdefault(event.parent_id, []).append(event)
    return grouped


def _first(children: list[TraceEvent], event_type: str,
           name: str | None = None) -> TraceEvent | None:
    for child in children:
        if child.event_type == event_type and (name is None or child.name == name):
            return child
    return None


def _all(children: list[TraceEvent], event_type: str) -> list[TraceEvent]:
    return [c for c in children if c.event_type == event_type]


def _model_context(events: list[TraceEvent], before_seq: int | None = None) -> dict:
    """Nearest preceding model identity + preflight classification."""
    model = None
    version = None
    for event in events:
        if before_seq is not None and event.seq >= before_seq:
            break
        payload = event.payload or {}
        if event.event_type == "policy_evaluation" \
                and str(event.name).startswith("model_preflight:"):
            info = payload.get("model") or {}
            if info.get("model_id"):
                model = f"{info.get('provider')}/{info.get('model_id')}"
                version = info.get("version")
        elif event.event_type == "model_call":
            model = f"{payload.get('provider')}/{payload.get('model')}"
    return {"model": model, "model_version": version}


def _actor(context_payload: dict, system_id: str | None) -> dict:
    return {
        "agent_id": context_payload.get("agent_id"),
        "user_id": context_payload.get("user_id"),
        "environment": context_payload.get("environment"),
        "autonomy": context_payload.get("autonomy"),
        "autonomy_level": context_payload.get("autonomy_level"),
        "system_id": system_id or context_payload.get("system_id"),
    }


def _tool_record_fields(db: Session, tenant_id: str, proposal: TraceEvent,
                        children: list[TraceEvent],
                        model_ctx: dict | None = None) -> dict:
    payload = proposal.payload or {}
    classification = payload.get("data_classification") or {}
    model_ctx = model_ctx or {}

    permission_event = _first(children, "permission_evaluation")
    risk_event = _first(children, "risk_evaluation")
    policy_event = _first(children, "policy_evaluation")
    governance_event = _first(children, "governance_evaluation")
    approval_events = _all(children, "approval")
    execution_event = _first(children, "tool_execution")
    outcome_event = _first(children, "outcome")
    data_events = _all(children, "data_access") + _all(children, "retrieval")

    # decision
    if outcome_event is not None and outcome_event.status == "denied":
        decision = "DENIED"
    elif execution_event is not None:
        decision = "ERROR" if execution_event.status == "error" else "EXECUTED"
    elif any(a.status == "pending" for a in approval_events):
        decision = "APPROVAL_REQUIRED"
    else:
        decision = "DENIED"

    policy_payload = (policy_event.payload if policy_event else {}) or {}
    governance_payload = (governance_event.payload if governance_event else {}) or {}
    if governance_payload.get("decision") == "deny" and decision == "DENIED":
        policy_stage = "governance"
    else:
        policy_stage = policy_payload.get("rule_id")

    # oversight
    required = (
        policy_payload.get("decision") == "require_approval"
        or governance_payload.get("decision") in ("require_approval",
                                                   "require_human_review")
    )
    actual = "none"
    reviewer = None
    approval_id = None
    for approval in approval_events:
        apayload = approval.payload or {}
        approval_id = apayload.get("approval_id") or approval_id
        if approval.status == "approved":
            actual = apayload.get("mode") or "approved"
            reviewer = apayload.get("decided_by")
        elif approval.status == "pending" and actual == "none":
            actual = "pending"
        elif approval.status == "denied":
            actual = "denied"
            reviewer = apayload.get("decided_by")

    # Converge gated records with the human's ACTUAL decision: the trace
    # event stays "pending" forever (events are immutable), but the approval
    # request carries the resolution — the record projects both.
    human_decision_note = None
    if approval_id:
        from helios.models import ApprovalRequest

        approval_row = db.get(ApprovalRequest, approval_id)
        if approval_row is not None and actual in ("pending", "none"):
            if approval_row.status == "approved":
                actual = "approved"
                reviewer = approval_row.decided_by
                decision = "APPROVED"
                human_decision_note = (
                    f"human decision: approved by {approval_row.decided_by}")
            elif approval_row.status == "denied":
                actual = "denied"
                reviewer = approval_row.decided_by
                decision = "DENIED"
                human_decision_note = (
                    f"human decision: denied by {approval_row.decided_by}")
            elif approval_row.status == "expired":
                actual = "expired"
                decision = "DENIED"
                human_decision_note = "approval expired before execution"

    risk_payload = (risk_event.payload if risk_event else {}) or {}
    event_ids = [proposal.id] + [c.id for c in children]
    data_refs = []
    for data_event in data_events:
        dpayload = data_event.payload or {}
        ref = dpayload.get("source") or dpayload.get("destination") or {}
        data_refs.append({
            "direction": dpayload.get("direction"),
            "kind": dpayload.get("source_kind"),
            "ref": ref,
            "data_class": dpayload.get("data_class")
            or (max(dpayload.get("classes_detected") or ["PUBLIC"],
                    key=lambda c: ["PUBLIC", "INTERNAL", "CONFIDENTIAL",
                                   "SENSITIVE", "PII"].index(c)
                    if c in ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "SENSITIVE", "PII"]
                    else 0)),
            "event_id": data_event.id,
        })

    return {
        "kind": "tool_action",
        "action": payload.get("tool") or proposal.name,
        "decision": decision,
        "model": model_ctx.get("model"),
        "model_version": model_ctx.get("model_version"),
        "actor": _actor(payload.get("context") or {}, proposal.system_id),
        "risk": risk_payload.get("risk") or proposal.risk,
        "data_class": classification.get("effective"),
        "data_classification": classification,
        "policy": {
            "tool_policy": {
                "version": policy_payload.get("policy_version"),
                "rule_id": policy_payload.get("rule_id"),
                "decision": policy_payload.get("decision"),
                "reason": policy_payload.get("reason"),
            } if policy_payload else None,
            "governance": {
                "decision": governance_payload.get("decision"),
                "reason": governance_payload.get("reason"),
                "matched_rules": governance_payload.get("matched_rules"),
                "policy_versions": governance_payload.get("policy_versions"),
            } if governance_payload else None,
            "stage": policy_stage,
            "permission_checks": ((permission_event.payload or {}).get("checks")
                                  if permission_event else None),
            "risk_reasons": risk_payload.get("reasons"),
        },
        "evidence": {
            "trace_event_ids": event_ids,
            "proposal_event_id": proposal.id,
            "policy_versions": sorted(filter(None, [
                policy_payload.get("policy_version"),
                *(governance_payload.get("policy_versions") or []),
            ])),
            "approval_id": approval_id,
            "data_refs": data_refs,
            "effect_id": ((execution_event.payload or {}).get("effect_id")
                          if execution_event else None),
        },
        "oversight": {
            "required": bool(required),
            "actual": actual,
            "reviewer": reviewer,
            "approval_id": approval_id,
        },
        "args_hash": payload.get("args_hash"),
        "outcome": {
            "status": (execution_event.status if execution_event
                       else (outcome_event.status if outcome_event else decision.lower())),
            "reason": human_decision_note
            or ((outcome_event.payload or {}).get("reason")
                if outcome_event and outcome_event.status == "denied"
                else (policy_payload.get("reason")
                      or governance_payload.get("reason"))),
            "latency_ms": execution_event.latency_ms if execution_event else 0,
            "replayed": bool((execution_event.payload or {}).get("replayed"))
            if execution_event else False,
        },
    }


def _preflight_record_fields(event: TraceEvent) -> dict | None:
    """Record for a denied / review-gated model preflight (allowed calls are
    evidenced by the model_call event and the run_outcome record)."""
    payload = event.payload or {}
    decision = payload.get("decision")
    if decision == "allow":
        return None
    classification = payload.get("data_classification") or {}
    model_info = payload.get("model") or {}
    fields = {
        "kind": "model_preflight",
        "action": f"model_call:{model_info.get('provider')}/{model_info.get('model_id')}",
        "decision": "DENIED" if decision == "deny" else "APPROVAL_REQUIRED",
        "model": (f"{model_info.get('provider')}/{model_info.get('model_id')}"
                  if model_info.get("model_id") else event.name),
        "model_version": model_info.get("version"),
        "actor": {
            "agent_id": None, "user_id": None,
            "environment": payload.get("environment"),
            "autonomy": None, "autonomy_level": None,
            "system_id": event.system_id,
        },
        "risk": None,
        "data_class": classification.get("effective"),
        "data_classification": classification,
        "policy": {
            "model_checks": payload.get("checks"),
            "governance": payload.get("governance"),
            "reasons": payload.get("reasons"),
        },
        "evidence": {
            "trace_event_ids": [event.id],
            "policy_versions": sorted(filter(None, [
                *((payload.get("governance") or {}).get("policy_versions") or []),
            ])),
            "approval_id": None,
            "data_refs": [],
        },
        "oversight": {
            "required": decision in ("require_approval", "require_human_review"),
            "actual": "pending" if decision in ("require_approval",
                                                 "require_human_review") else "none",
            "reviewer": None,
            "approval_id": None,
        },
        "args_hash": None,
        "outcome": {"status": "denied", "reason": "; ".join(payload.get("reasons") or []),
                    "latency_ms": 0, "replayed": False},
    }
    return fields


def _run_outcome_fields(events: list[TraceEvent], outcome: TraceEvent,
                        system_id: str | None) -> dict:
    payload = outcome.payload or {}
    model_ctx = _model_context(events, before_seq=outcome.seq)
    decision = RUN_DECISIONS.get(outcome.name, outcome.name.upper())
    model_calls = [e for e in events if e.event_type == "model_call"]
    tool_proposals = [e for e in events if e.event_type == "tool_proposal"]
    # actor context: from the run's recorded invocation contexts (proposals),
    # falling back to model-preflight evidence (which records the environment)
    actor = _actor({}, system_id)
    for event in events:
        context = (event.payload or {}).get("context")
        if context:
            actor = _actor(context, system_id)
            break
    else:
        for event in events:
            payload_ = event.payload or {}
            if event.event_type == "policy_evaluation" \
                    and str(event.name).startswith("model_preflight:") \
                    and payload_.get("environment"):
                actor["environment"] = payload_["environment"]
                break
    return {
        "kind": "run_outcome",
        "action": "run",
        "decision": decision,
        "model": model_ctx["model"],
        "model_version": model_ctx["model_version"],
        "actor": actor,
        "risk": None,
        "data_class": None,
        "data_classification": {},
        "policy": {},
        "evidence": {
            "trace_event_ids": [outcome.id],
            "policy_versions": [],
            "approval_id": None,
            "data_refs": [],
            "model_call_count": len(model_calls),
            "tool_proposal_count": len(tool_proposals),
        },
        "oversight": {"required": False, "actual": "none", "reviewer": None,
                      "approval_id": None},
        "args_hash": None,
        "outcome": {
            "status": outcome.status,
            "reason": payload.get("error") or payload.get("reason"),
            "output_preview": payload.get("output_preview"),
            "latency_ms": 0,
            "replayed": False,
        },
    }


def build_for_run(db: Session, tenant_id: str, run_id: str) -> dict:
    """
    (Re)build decision records for one run. Idempotent upsert keyed by
    (tenant, run, source_event). Returns {"created": n, "updated": n}.
    """
    events = _events_for_run(db, tenant_id, run_id)
    if not events:
        return {"created": 0, "updated": 0}
    children = _children_by_parent(events)
    system_id = next((e.system_id for e in events if e.system_id), None)
    session_id = next((e.session_id for e in events if e.session_id), None)

    projections: list[tuple[str, dict]] = []

    for event in events:
        if event.event_type == "tool_proposal":
            projections.append(
                (event.id,
                 _tool_record_fields(db, tenant_id, event,
                                     children.get(event.id, []),
                                     model_ctx=_model_context(events,
                                                              before_seq=event.seq + 1))))
        elif event.event_type == "policy_evaluation" \
                and str(event.name).startswith("model_preflight:"):
            fields = _preflight_record_fields(event)
            if fields is not None:
                # attach the run-level approval events that gate this preflight
                for candidate in events:
                    if candidate.event_type == "approval" \
                            and candidate.name.startswith("model.call:"):
                        apayload = candidate.payload or {}
                        fields["evidence"]["approval_id"] = apayload.get("approval_id")
                        fields["evidence"]["trace_event_ids"].append(candidate.id)
                        fields["oversight"]["approval_id"] = apayload.get("approval_id")
                        if candidate.status == "approved":
                            fields["oversight"]["actual"] = "approved"
                            fields["oversight"]["reviewer"] = apayload.get("decided_by")
                            # NOTE: the record's decision stays
                            # APPROVAL_REQUIRED — this run was gated; whether
                            # a later run used the approval is that run's
                            # evidence, not this record's.
                        elif candidate.status == "denied":
                            fields["oversight"]["actual"] = "denied"
                            fields["oversight"]["reviewer"] = apayload.get("decided_by")
                            fields["decision"] = "DENIED"
                projections.append((event.id, fields))

    # terminal outcome: the LAST top-level outcome event
    terminal = None
    for event in reversed(events):
        if event.event_type == "outcome" and not event.parent_id:
            terminal = event
            break
    if terminal is not None:
        projections.append(
            (terminal.id, _run_outcome_fields(events, terminal, system_id)))

    created = updated = 0
    for source_event_id, fields in projections:
        existing = (
            db.query(DecisionRecord)
            .filter(DecisionRecord.tenant_id == tenant_id,
                    DecisionRecord.run_id == run_id,
                    DecisionRecord.source_event_id == source_event_id)
            .first()
        )
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            existing.system_id = system_id
            existing.session_id = session_id
            updated += 1
        else:
            db.add(DecisionRecord(
                tenant_id=tenant_id, system_id=system_id, session_id=session_id,
                run_id=run_id, source_event_id=source_event_id,
                schema_version=SCHEMA_VERSION, **fields,
            ))
            created += 1
    db.commit()
    return {"created": created, "updated": updated}


# --- explanation (the WHY endpoint) --------------------------------------------


def explain(record: DecisionRecord) -> dict:
    """
    Assemble the governance explanation for one decision FROM THE RECORD'S
    stored evidence state. Each check cites the recorded reason and the event
    ids that prove it — this is what answers "WHY was this allowed/blocked?".
    """
    checks: list[dict] = []

    def add(check: str, passed: bool | None, reason: str,
            evidence: list[str] | None = None) -> None:
        checks.append({"check": check, "passed": passed, "reason": reason,
                       "evidence": evidence or []})

    event_ids = (record.evidence or {}).get("trace_event_ids") or []
    proposal_id = (record.evidence or {}).get("proposal_event_id")
    oversight = record.oversight or {}
    oversight_satisfied = oversight.get("actual") in (
        "approved", "existing", "session", "payload_bound")

    add("ai_system", record.system_id is not None,
        (f"actions attributed to registered AI system '{record.system_id}'"
         if record.system_id else
         "no registered AI system bound (legacy/unbound session)"))

    actor = record.actor or {}
    add("actor_identity", True,
        f"agent '{actor.get('agent_id') or 'unknown'}' in environment "
        f"'{actor.get('environment') or 'unknown'}'"
        + (f", autonomy {actor.get('autonomy_level')}"
           if actor.get("autonomy_level") is not None else ""),
        [proposal_id] if proposal_id else [])

    permission_checks = (record.policy or {}).get("permission_checks")
    if permission_checks is not None:
        passed = all(c.get("allowed") for c in permission_checks)
        reasons = []
        for c in permission_checks:
            reasons.extend(c.get("reasons") or [])
        add("permissions", passed,
            "; ".join(reasons) or ("grants cover required scopes" if passed
                                   else "permission denied"),
            event_ids[:4])

    tool_policy = (record.policy or {}).get("tool_policy")
    if tool_policy:
        tp_decision = tool_policy.get("decision")
        gated = tp_decision in ("require_approval", "require_human_review")
        passed = tp_decision == "allow" or (gated and oversight_satisfied)
        reason = (f"policy '{tool_policy.get('version')}' rule "
                  f"'{tool_policy.get('rule_id')}': {tool_policy.get('reason')}")
        if gated and oversight_satisfied:
            reason += (f" — approval REQUIRED and OBTAINED "
                       f"(reviewer: {oversight.get('reviewer') or 'unknown'})")
        elif gated:
            reason += " — approval required"
        add("tool_policy", passed, reason, event_ids[:4])

    governance = (record.policy or {}).get("governance")
    if governance:
        gv_decision = governance.get("decision")
        gated = gv_decision in ("require_approval", "require_human_review")
        rules = ", ".join(f"{m.get('set')}@{m.get('version')}:{m.get('rule_id')}"
                          for m in governance.get("matched_rules") or [])
        reason = (f"governance decision '{gv_decision}'"
                  + (f" via {rules}" if rules else " (no rules matched)")
                  + (f": {governance.get('reason')}" if governance.get("reason") else ""))
        if gated and oversight_satisfied:
            reason += " — human oversight obtained"
        add("governance_policy",
            gv_decision == "allow" or (gated and oversight_satisfied),
            reason, event_ids[:4])

    if record.kind == "model_preflight":
        for check in (record.policy or {}).get("model_checks") or []:
            add(f"model_{check.get('check')}", check.get("passed"),
                check.get("reason", ""))

    if record.data_classification:
        classification = record.data_classification
        add("data_classification", True,
            f"effective class '{classification.get('effective')}' "
            f"(declared: {classification.get('declared') or []}, "
            f"detected: {classification.get('detected') or []})",
            [d.get("event_id") for d in (record.evidence or {}).get("data_refs") or []
             if d.get("event_id")])

    if record.risk:
        high_risk = record.risk in ("high", "critical")
        reasons = (record.policy or {}).get("risk_reasons") or []
        if not high_risk:
            add("risk", True,
                f"contextual risk '{record.risk}'"
                + (f": {'; '.join(reasons)}" if reasons else ""))
        else:
            add("risk", oversight_satisfied,
                f"contextual risk '{record.risk}'"
                + (f": {'; '.join(reasons)}" if reasons else "")
                + (" — high-risk action, human oversight obtained"
                   if oversight_satisfied else
                   " — high-risk action WITHOUT completed human oversight")),

    if oversight.get("required"):
        actual = oversight.get("actual")
        passed = actual in ("approved", "existing", "session", "payload_bound")
        add("human_oversight", passed,
            f"human oversight REQUIRED; actual: {actual}"
            + (f" (reviewer: {oversight.get('reviewer')})"
               if oversight.get("reviewer") else ""),
            [oversight["approval_id"]] if oversight.get("approval_id") else [])
    else:
        add("human_oversight", True, "human oversight not required by policy")

    add("outcome", record.decision in ("EXECUTED", "COMPLETED"),
        f"decision: {record.decision}"
        + (f" — {(record.outcome or {}).get('reason')}"
           if (record.outcome or {}).get("reason") else ""),
        event_ids[:6])

    return {
        "decision_record_id": record.id,
        "schema_version": record.schema_version,
        "system_id": record.system_id,
        "run_id": record.run_id,
        "action": record.action,
        "decision": record.decision,
        "why": checks,
        "evidence": record.evidence,
    }


def record_to_dict(record: DecisionRecord) -> dict:
    return {
        "id": record.id,
        "schema_version": record.schema_version,
        "tenant_id": record.tenant_id,
        "system_id": record.system_id,
        "session_id": record.session_id,
        "run_id": record.run_id,
        "source_event_id": record.source_event_id,
        "kind": record.kind,
        "action": record.action,
        "decision": record.decision,
        "model": record.model,
        "model_version": record.model_version,
        "actor": record.actor,
        "risk": record.risk,
        "data_class": record.data_class,
        "data_classification": record.data_classification,
        "policy": record.policy,
        "evidence": record.evidence,
        "oversight": record.oversight,
        "args_hash": record.args_hash,
        "outcome": record.outcome,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }
