"""
Finance / Operations workspace pack — third reference implementation.

ENTIRELY SYNTHETIC DATA.  No autonomous financial transactions exist:
the only typed action is flagging an invoice for human review.
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

# Config-driven procurement policy — data, not code.
PROCUREMENT_POLICY = {
    "po_required_above": 5_000.0,
    "approval_required_above": 25_000.0,
    "registered_vendors_only": True,
}


def invoice_compliance(input_data: dict, sources: list, workspace) -> dict:
    policy = {**PROCUREMENT_POLICY, **workspace.policies.get("procurement", {})}
    invoices = [s for s in sources if s.type == "invoices"]
    vendors = {
        s.record.get("vendor_id")
        for s in sources
        if s.type == "vendor_records" and s.record.get("registered")
    }
    if not invoices:
        return {"error": "no invoices found in workspace"}

    violations = []
    seen_numbers: dict[str, str] = {}
    for inv in invoices:
        r = inv.record
        amount = r.get("amount", 0)
        number = r.get("invoice_number", "?")
        if amount > policy["po_required_above"] and not r.get("po_number"):
            violations.append({"invoice": number, "rule": "po_required_above",
                               "detail": f"amount {amount} > {policy['po_required_above']} with no PO"})
        if amount > policy["approval_required_above"] and r.get("approval_status") != "approved":
            violations.append({"invoice": number, "rule": "approval_required_above",
                               "detail": f"amount {amount} lacks required approval"})
        if policy["registered_vendors_only"] and r.get("vendor_id") not in vendors:
            violations.append({"invoice": number, "rule": "registered_vendors_only",
                               "detail": f"vendor '{r.get('vendor_id')}' is not registered"})
        if number in seen_numbers:
            violations.append({"invoice": number, "rule": "duplicate_invoice_number",
                               "detail": f"duplicate of {seen_numbers[number]}"})
        else:
            seen_numbers[number] = inv.name

    total = round(sum(i.record.get("amount", 0) for i in invoices), 2)
    facts = [
        Fact(name="invoices_reviewed", value=len(invoices)),
        Fact(name="total_invoice_amount", value=total, unit="USD"),
        Fact(name="policy_violations", value=len(violations),
             detail="; ".join(f"{v['invoice']}: {v['rule']} ({v['detail']})"
                              for v in violations) or "no violations detected"),
    ]
    used = [
        {"id": s.id, "name": s.name, "trust": s.trust,
         "excerpt": f"{s.record.get('invoice_number')} ${s.record.get('amount')} "
                    f"vendor={s.record.get('vendor_id')}"}
        for s in invoices
    ]
    return {"facts": facts,
            "tables": {"violations": violations, "used_sources": used}}


WORKFLOWS = [
    WorkflowDefinition(
        id="invoice_compliance_review",
        name="Invoice Compliance Review",
        description="Identify invoices violating procurement policy, with the computed values and the exact rule.",
        input_schema={},
        source_types=["invoices", "vendor_records"],
        analysis_steps=["invoice_compliance"],
        retrieval=RetrievalConfig(
            enabled=True,
            query_template="procurement policy purchase order approval vendor registration",
            top_k=3,
        ),
        reasoning=ReasoningConfig(
            task_template=(
                "Explain each flagged invoice: which policy rule it violates, the "
                "computed values, and what the reviewer should verify. Do not "
                "recommend any payment action."
            ),
        ),
        base_risk="low",
        risk_rules=[RiskRule(fact="policy_violations", op="nonzero", risk="high")],
        approval=ApprovalConfig(action="flag_invoice_for_review"),
    ),
    WorkflowDefinition(
        id="operations_brief",
        name="Operations Brief",
        description="Exceptions, unusual patterns, pending items, and policy violations across operational records.",
        input_schema={},
        source_types=["invoices", "vendor_records", "expense_reports"],
        analysis_steps=["brief_aggregate"],
        retrieval=RetrievalConfig(enabled=False),
        reasoning=ReasoningConfig(
            task_template="Produce an OPERATIONS BRIEF: exceptions, pending items, and items requiring review.",
        ),
        base_risk="informational",
        risk_rules=[RiskRule(fact="brief_critical", op="nonzero", risk="medium")],
    ),
]

CONFIG = WorkspaceConfig(
    id="finance",
    name="Finance / Operations",
    description="Invoice compliance and operational review (synthetic data; no transactions).",
    domain="finance-operations",
    terminology={"PO": "purchase order", "AP": "accounts payable"},
    system_instructions=(
        "You are the Helios operations analyst. Amounts and rule checks come "
        "ONLY from COMPUTED FACTS. Never recommend executing payments — flag "
        "for human review only."
    ),
    source_types=["invoices", "vendor_records", "expense_reports",
                  "procurement_policies"],
    capabilities=["compliance", "brief"],
    workflows=WORKFLOWS,
    actions=[
        ActionSpec(name="flag_invoice_for_review", risk="high",
                   description="Flag an invoice for human procurement review (no payment action)"),
    ],
    policies={"procurement": PROCUREMENT_POLICY},
    metadata={"synthetic": True},
)

SEED_SOURCES = [
    {"name": "Vendor: Acme Industrial (registered)", "type": "vendor_records",
     "record": {"vendor_id": "V-100", "name": "Acme Industrial", "registered": True}},
    {"name": "Vendor: Nimbus Cloud (registered)", "type": "vendor_records",
     "record": {"vendor_id": "V-200", "name": "Nimbus Cloud", "registered": True}},
    {"name": "Invoice INV-9001", "type": "invoices",
     "record": {"invoice_number": "INV-9001", "vendor_id": "V-100",
                "amount": 4200.0, "po_number": "PO-771",
                "approval_status": "approved", "date": "2026-08-20"}},
    {"name": "Invoice INV-9002", "type": "invoices",
     "record": {"invoice_number": "INV-9002", "vendor_id": "V-200",
                "amount": 12500.0, "po_number": None,
                "approval_status": "approved", "date": "2026-08-21"}},
    {"name": "Invoice INV-9003", "type": "invoices",
     "record": {"invoice_number": "INV-9003", "vendor_id": "V-999",
                "amount": 800.0, "po_number": "PO-802",
                "approval_status": "pending", "date": "2026-08-22"}},
    {"name": "Invoice INV-9004", "type": "invoices",
     "record": {"invoice_number": "INV-9004", "vendor_id": "V-100",
                "amount": 31000.0, "po_number": "PO-810",
                "approval_status": "pending", "date": "2026-08-24"}},
]

SEED_DOCUMENTS = [
    {"title": "Procurement Policy (synthetic)",
     "content": (
         "Procurement policy. Purchases above 5,000 USD require a purchase "
         "order issued before the invoice date. Purchases above 25,000 USD "
         "additionally require director approval before payment. Invoices "
         "may only be paid to registered vendors. Duplicate invoice numbers "
         "must be investigated as potential double billing.")},
]

SEED_RELATIONSHIPS = [
    {"source": ("INV-9001", "Invoice"), "relationship_type": "issued_by",
     "target": ("Acme Industrial", "Vendor")},
    {"source": ("INV-9002", "Invoice"), "relationship_type": "issued_by",
     "target": ("Nimbus Cloud", "Vendor")},
    {"source": ("INV-9002", "Invoice"), "relationship_type": "violates",
     "target": ("po_required_above", "PolicyRule")},
    {"source": ("INV-9004", "Invoice"), "relationship_type": "violates",
     "target": ("approval_required_above", "PolicyRule")},
]

PACK = WorkspacePack(
    config=CONFIG,
    analysis_steps={
        "invoice_compliance": invoice_compliance,
        "brief_aggregate": make_brief_step([
            {"source_type": "invoices", "field": "approval_status", "op": "eq",
             "value": "pending", "bucket": "requires_review",
             "label": "Invoice pending approval"},
            {"source_type": "invoices", "field": "amount", "op": "gte",
             "value": 25000.0, "bucket": "important", "label": "High-value invoice"},
        ]),
    },
    seed_sources=SEED_SOURCES,
    seed_documents=SEED_DOCUMENTS,
    seed_relationships=SEED_RELATIONSHIPS,
)
