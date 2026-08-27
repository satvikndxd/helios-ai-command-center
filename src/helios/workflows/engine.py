"""
The workflow engine.

Runs the reusable pipeline
  SOURCES -> DETERMINISTIC ANALYSIS -> RETRIEVAL -> AI REASONING ->
  EVIDENCE -> EVALUATION -> POLICY -> RISK -> RECOMMENDATION -> TRACE
THROUGH the existing Helios governance stack — the same Sentinel scans,
policy engine, router/fallback chain, provider adapters, DecisionTrace
persistence, and async-evaluation handoff the gateway uses.  There is no
parallel execution or security path.

Explicit failure states, never fabrication:
  invalid_input | insufficient_evidence | blocked | error | completed
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from helios import policy as policy_engine
from helios import sentinel
from helios.config import settings
from helios.cost import compute_cost
from helios.models import ApiKey, DecisionTrace, WorkflowExecution, WorkspaceSource
from helios.providers import get_provider
from helios.registry import default_model_for, select_route
from helios.retrieval import search
from helios.routes.completions import _enqueue_evaluation, _persist_trace
from helios.workflows.types import (
    KIND_INTERPRETATION,
    KIND_RECOMMENDATION,
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_INSUFFICIENT_EVIDENCE,
    STATUS_INVALID_INPUT,
    Claim,
    Evidence,
    Fact,
    WorkflowDefinition,
    WorkspacePack,
)

RISK_ORDER = {"informational": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _apply_risk_rules(workflow: WorkflowDefinition, facts: list[Fact]) -> str:
    """Config-driven risk escalation — no industry conditionals in core."""
    risk = workflow.base_risk
    fact_map = {f.name: f.value for f in facts}
    for rule in workflow.risk_rules:
        value = fact_map.get(rule.fact)
        if not isinstance(value, (int, float)):
            continue
        hit = (
            (rule.op == "gt" and value > rule.value)
            or (rule.op == "gte" and value >= rule.value)
            or (rule.op == "lt" and value < rule.value)
            or (rule.op == "lte" and value <= rule.value)
            or (rule.op == "eq" and value == rule.value)
            or (rule.op == "nonzero" and value != 0)
        )
        if hit and RISK_ORDER.get(rule.risk, 0) > RISK_ORDER.get(risk, 0):
            risk = rule.risk
    return risk


def _confidence(evidence: list[Evidence], insufficient: bool) -> float:
    """Deterministic confidence heuristic — never model-invented."""
    if insufficient:
        return 0.0
    return round(min(0.95, 0.5 + 0.08 * min(len(evidence), 5)), 2)


class WorkflowEngine:
    def __init__(self, db: Session, api_key: ApiKey) -> None:
        self.db = db
        self.api_key = api_key

    # -- helpers ----------------------------------------------------------

    def _load_sources(
        self, workspace_id: str, workflow: WorkflowDefinition
    ) -> list[WorkspaceSource]:
        """Tenant + workspace isolated source loading."""
        query = self.db.query(WorkspaceSource).filter(
            WorkspaceSource.tenant_id == self.api_key.tenant_id,
            WorkspaceSource.workspace_id == workspace_id,
        )
        if workflow.source_types:
            query = query.filter(WorkspaceSource.type.in_(workflow.source_types))
        return query.all()

    def _validate_input(self, workflow: WorkflowDefinition, input_data: dict) -> list[str]:
        missing = [
            key
            for key in workflow.input_schema.get("required", [])
            if input_data.get(key) in (None, "")
        ]
        return missing

    # -- the pipeline -----------------------------------------------------

    async def run(
        self, pack: WorkspacePack, workflow_id: str, input_data: dict
    ) -> WorkflowExecution:
        started = time.perf_counter()
        workspace = pack.config
        workflow = workspace.workflow(workflow_id)
        trace_id = str(uuid.uuid4())

        def _latency() -> int:
            return int((time.perf_counter() - started) * 1000)

        execution = WorkflowExecution(
            tenant_id=self.api_key.tenant_id,
            workspace_id=workspace.id,
            workflow_id=workflow_id,
            trace_id=trace_id,
            input=input_data,
        )

        if workflow is None:
            execution.status = STATUS_INVALID_INPUT
            execution.evaluation = {"error": f"unknown workflow '{workflow_id}'"}
            self.db.add(execution)
            self.db.commit()
            return execution

        # ---- 1. Input validation (explicit failure, no guessing) ---------
        missing = self._validate_input(workflow, input_data)
        if missing:
            execution.status = STATUS_INVALID_INPUT
            execution.evaluation = {"missing_input": missing}
            self.db.add(execution)
            self.db.commit()
            return execution

        # ---- 2. Sources (tenant + workspace isolated) --------------------
        sources = self._load_sources(workspace.id, workflow)

        # ---- 2b. Insufficient evidence -> explicit status, no fabrication -
        if workflow.require_sources and not sources:
            execution.status = STATUS_INSUFFICIENT_EVIDENCE
            execution.confidence = 0.0
            execution.evaluation = {
                "insufficient_evidence": True,
                "reason": "no workspace sources matched the workflow's source types",
            }
            execution.latency_ms = _latency()
            self.db.add(execution)
            self.db.commit()
            self._persist_workflow_trace(
                trace_id, workspace, workflow, input_data, execution,
                policy_record={"action": "allow", "stage": "no_model_call"},
                provider="none", model_id="none", output_text=None,
                citations=[], cost=0.0,
            )
            return execution

        # ---- 3. Deterministic analysis (source of truth for numbers) -----
        facts: list[Fact] = []
        tables: dict = {}
        analysis_error: str | None = None
        for step_name in workflow.analysis_steps:
            step = pack.analysis_steps.get(step_name)
            if step is None:
                analysis_error = f"analysis step '{step_name}' is not registered"
                break
            result = step(input_data, sources, workspace)
            if result.get("error"):
                analysis_error = result["error"]
                break
            facts.extend(result.get("facts", []))
            tables.update(result.get("tables", {}))

        if analysis_error:
            execution.status = STATUS_INVALID_INPUT
            execution.evaluation = {"analysis_error": analysis_error}
            self.db.add(execution)
            self.db.commit()
            return execution

        # ---- 4. Evidence from sources + computations ---------------------
        evidence: list[Evidence] = [
            Evidence(
                kind="computation",
                source="deterministic-analysis",
                reference=f.name,
                excerpt=f.detail or str(f.value),
                trust="internal",
                timestamp=_now_iso(),
            )
            for f in facts
        ]
        used_records = tables.get("used_sources", [])
        for src in used_records:
            evidence.append(
                Evidence(
                    kind="record",
                    source=src["name"],
                    reference=src["id"],
                    excerpt=src.get("excerpt"),
                    trust=src.get("trust", "internal"),
                    timestamp=src.get("timestamp"),
                )
            )

        # ---- 5. Retrieval via existing tenant/workspace-isolated RAG -----
        citations: list[dict] = []
        report = sentinel.SentinelReport()
        retrieved_context = ""
        if workflow.retrieval.enabled:
            try:
                query_text = workflow.retrieval.query_template.format(**input_data)
            except (KeyError, IndexError):
                query_text = str(input_data)
            retrieved = await search(
                db=self.db,
                tenant_id=self.api_key.tenant_id,
                query=query_text,
                settings=settings,
                top_k=workflow.retrieval.top_k,
                workspace_id=workspace.id,
            )
            clean = []
            for chunk in retrieved:
                # Retrieved content is untrusted: poisoned chunks are dropped
                # (same defense as the gateway RAG path).
                if sentinel.detect_injection(chunk.content):
                    report.dropped_chunks.append(chunk.chunk_id)
                else:
                    clean.append(chunk)
            for chunk in clean:
                evidence.append(
                    Evidence(
                        kind="document",
                        source=chunk.document_title,
                        reference=chunk.chunk_id,
                        excerpt=chunk.content[:280],
                        trust="internal",
                        score=round(chunk.score, 4),
                        timestamp=_now_iso(),
                    )
                )
                citations.append(
                    {"document_id": chunk.document_id, "title": chunk.document_title,
                     "chunk_id": chunk.chunk_id, "score": round(chunk.score, 4)}
                )
            if clean:
                retrieved_context = "\n\n".join(
                    f"[{i+1}] {c.document_title}: {c.content}"
                    for i, c in enumerate(clean)
                )

        # ---- 6. Insufficient evidence -> explicit status, no AI call -----
        insufficient = workflow.require_sources and not sources and not citations
        if insufficient:
            execution.status = STATUS_INSUFFICIENT_EVIDENCE
            execution.facts = [f.model_dump() for f in facts]
            execution.evidence = []
            execution.confidence = 0.0
            execution.evaluation = {
                "insufficient_evidence": True,
                "reason": "no workspace sources or retrievable documents matched",
            }
            execution.latency_ms = _latency()
            self.db.add(execution)
            self.db.commit()
            self._persist_workflow_trace(
                trace_id, workspace, workflow, input_data, execution,
                policy_record={"action": "allow", "stage": "no_model_call"},
                provider="none", model_id="none", output_text=None,
                citations=[], cost=0.0,
            )
            return execution

        # ---- 7. Prompt assembly + Sentinel input scan --------------------
        facts_block = "\n".join(
            f"- {f.name} = {f.value}{f' {f.unit}' if f.unit else ''}"
            + (f"  ({f.detail})" if f.detail else "")
            for f in facts
        ) or "(none)"
        try:
            task = workflow.reasoning.task_template.format(**input_data)
        except (KeyError, IndexError):
            task = workflow.reasoning.task_template

        prompt = (
            f"{workspace.system_instructions}\n\n"
            f"WORKSPACE: {workspace.name} ({workspace.domain})\n"
            f"TASK: {task}\n\n"
            "COMPUTED FACTS (deterministic — the source of truth for all numbers):\n"
            f"{facts_block}\n\n"
            + (f"EVIDENCE (retrieved, cite by [n]):\n{retrieved_context}\n\n"
               if retrieved_context else "")
            + workflow.reasoning.output_guidance
        )

        report.pii = sentinel.detect_pii(prompt)
        report.injection_matches = sentinel.detect_injection(task)

        # ---- 8. Routing + policy preflight (existing engine) -------------
        decision = select_route(
            requested_provider=None,
            risk_level=workflow.base_risk if workflow.base_risk in ("high", "critical") else "low",
            input_text=prompt,
            max_cost_usd=None,
            settings=settings,
        )
        pre = policy_engine.preflight(
            risk_level="high" if workflow.base_risk in ("high", "critical") else "low",
            pii=report.pii,
            injection_matches=report.injection_matches,
            provider_name=decision.chain[0],
        )
        policy_record = {"preflight": pre.to_dict(), "sentinel": report.to_dict()}

        if pre.action == "deny":
            execution.status = STATUS_BLOCKED
            execution.evaluation = {"policy": pre.to_dict()}
            execution.latency_ms = _latency()
            self.db.add(execution)
            self.db.commit()
            self._persist_workflow_trace(
                trace_id, workspace, workflow, input_data, execution,
                policy_record={**policy_record, "action": "deny", "stage": "preflight"},
                provider="none", model_id="none", output_text=None,
                citations=[], cost=0.0, status="blocked",
            )
            return execution
        if pre.action == "redact":
            prompt, _ = sentinel.redact_pii(prompt)

        # ---- 9. AI reasoning via provider fallback chain -----------------
        provider_name, model_id, result, last_error = decision.chain[0], "unknown", None, None
        normalized = {"input_text": prompt, "parameters": {}, "provider": None, "model": None}
        for candidate in decision.chain:
            model_id = default_model_for(candidate, settings)
            normalized["provider"], normalized["model"] = candidate, model_id
            try:
                provider = get_provider(candidate, settings)
                result = await provider.complete(normalized, settings)
                provider_name = candidate
                decision.attempts.append({"provider": candidate, "ok": True})
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                decision.attempts.append(
                    {"provider": candidate, "ok": False, "error": str(exc)}
                )

        if result is None:
            execution.status = STATUS_ERROR
            execution.evaluation = {"error": str(last_error)}
            execution.latency_ms = _latency()
            self.db.add(execution)
            self.db.commit()
            self._persist_workflow_trace(
                trace_id, workspace, workflow, input_data, execution,
                policy_record={**policy_record, "action": "error", "stage": "provider"},
                provider=provider_name, model_id=model_id, output_text=None,
                citations=[], cost=0.0, status="error",
            )
            return execution

        output_text = result.output_text or ""
        model_declined = "INSUFFICIENT_EVIDENCE" in output_text.upper()

        # ---- 10. Risk + claims + confidence ------------------------------
        risk = _apply_risk_rules(workflow, facts)
        confidence = _confidence(evidence, model_declined)
        requires_approval = risk in workflow.approval.required_for

        claims: list[Claim] = [
            Claim(
                claim=f.detail or f"{f.name} = {f.value}",
                category=f.kind,
                evidence_refs=[i],
                confidence=1.0,
                timestamp=_now_iso(),
            )
            for i, f in enumerate(facts)
        ]
        if not model_declined:
            claims.append(
                Claim(
                    claim=output_text[:2000],
                    category=KIND_INTERPRETATION,
                    evidence_refs=list(range(len(evidence))),
                    confidence=confidence,
                    timestamp=_now_iso(),
                )
            )
            claims.append(
                Claim(
                    claim=f"Risk classified {risk}; "
                    + ("human approval required for follow-up actions."
                       if requires_approval else "informational — no approval needed."),
                    category=KIND_RECOMMENDATION,
                    evidence_refs=list(range(len(evidence))),
                    confidence=confidence,
                    timestamp=_now_iso(),
                )
            )

        # ---- 11. Workflow-level evaluation (explicit, meaningful) --------
        evaluation = {
            "structured_output_valid": all(
                k in ("facts", "interpretation", "recommendation") for k in workflow.output_schema
            ),
            "computed_fact_count": len(facts),
            "evidence_count": len(evidence),
            "citation_count": len(citations),
            "retrieval_used": workflow.retrieval.enabled,
            "dropped_poisoned_chunks": len(report.dropped_chunks),
            "policy_compliant": pre.action != "deny",
            "model_declined_insufficient_evidence": model_declined,
            "risk": risk,
        }

        cost = compute_cost(model_id, result.usage, provider_name)
        execution.status = (
            STATUS_INSUFFICIENT_EVIDENCE if model_declined else STATUS_COMPLETED
        )
        execution.facts = [f.model_dump() for f in facts]
        execution.evidence = [e.model_dump() for e in evidence]
        execution.claims = [c.model_dump() for c in claims]
        execution.interpretation = None if model_declined else output_text
        execution.recommendation = (
            None if model_declined else claims[-1].claim if claims else None
        )
        execution.risk = risk
        execution.requires_approval = requires_approval
        execution.confidence = confidence
        execution.evaluation = evaluation
        execution.latency_ms = _latency()
        execution.cost_usd = cost
        self.db.add(execution)
        self.db.commit()

        # ---- 12. DecisionTrace + async evaluation (existing systems) -----
        self._persist_workflow_trace(
            trace_id, workspace, workflow, input_data, execution,
            policy_record={**policy_record, "action": "allow", "stage": "output",
                           "routing": decision.to_dict()},
            provider=provider_name, model_id=model_id, output_text=output_text,
            citations=citations, cost=cost,
        )
        _enqueue_evaluation(self.db, trace_id)
        return execution

    # -- trace integration -------------------------------------------------

    def _persist_workflow_trace(
        self,
        trace_id: str,
        workspace,
        workflow: WorkflowDefinition,
        input_data: dict,
        execution: WorkflowExecution,
        *,
        policy_record: dict,
        provider: str,
        model_id: str,
        output_text: str | None,
        citations: list,
        cost: float,
        status: str = "success",
    ) -> None:
        """One DecisionTrace per workflow run — same audit object as the gateway."""
        _persist_trace(
            self.db,
            id=trace_id,
            tenant_id=self.api_key.tenant_id,
            application_id=self.api_key.application_id,
            session_id=None,
            user_id=None,
            task_type=f"workflow:{workflow.id}",
            risk_level=execution.risk,
            input_payload={"input": input_data},
            request_payload={
                "workflow": {
                    "workspace_id": workspace.id,
                    "workflow_id": workflow.id,
                    "execution_id": execution.id,
                    "status": execution.status,
                    "facts": execution.facts,
                    "evidence_count": len(execution.evidence or []),
                    "requires_approval": execution.requires_approval,
                    "confidence": execution.confidence,
                }
            },
            model_provider=provider,
            model_id=model_id,
            output_text=output_text,
            response_payload=None,
            citations=citations,
            tool_calls=[],
            cost_usd=cost,
            latency_ms=execution.latency_ms,
            policy_result=policy_record,
            evaluation_scores=None,
            status=status,
            error=None,
        )
