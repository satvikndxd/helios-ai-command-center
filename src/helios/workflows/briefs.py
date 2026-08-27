"""
Reusable operational-brief engine.

One deterministic aggregation mechanism shared by every domain: packs
configure severity rules (field/op/value -> bucket) instead of writing
domain-specific brief code.  Buckets: critical | important | informational |
requires_review.  Unsupported claims are impossible by construction — every
brief item points at the source record that produced it.
"""

from __future__ import annotations

from typing import Any

from helios.workflows.types import Fact

BUCKETS = ["critical", "important", "informational", "requires_review"]


def _matches(record: dict, rule: dict) -> bool:
    value = record.get(rule["field"])
    op = rule.get("op", "eq")
    target = rule.get("value")
    if op == "eq":
        return value == target
    if op == "ne":
        return value != target
    if op == "gt":
        return isinstance(value, (int, float)) and value > target
    if op == "gte":
        return isinstance(value, (int, float)) and value >= target
    if op == "lt":
        return isinstance(value, (int, float)) and value < target
    if op == "contains":
        return isinstance(value, str) and str(target).lower() in value.lower()
    return False


def make_brief_step(severity_rules: list[dict[str, Any]]):
    """
    Build a deterministic brief-aggregation step from config.

    severity_rules: [{"source_type": "incident_reports", "field": "status",
                      "op": "eq", "value": "open", "bucket": "critical",
                      "label": "Open incident"}]
    """

    def brief_aggregate(input_data: dict, sources: list, workspace) -> dict:
        by_type: dict[str, int] = {}
        items: dict[str, list[dict]] = {bucket: [] for bucket in BUCKETS}
        used = []

        for source in sources:
            by_type[source.type] = by_type.get(source.type, 0) + 1
            record = source.record or {}
            for rule in severity_rules:
                if rule.get("source_type") not in (None, source.type):
                    continue
                if _matches(record, rule):
                    items[rule.get("bucket", "informational")].append(
                        {
                            "label": rule.get("label", rule["field"]),
                            "source": source.name,
                            "source_id": source.id,
                            "type": source.type,
                            "value": record.get(rule["field"]),
                        }
                    )
                    used.append(
                        {"id": source.id, "name": source.name,
                         "trust": source.trust,
                         "excerpt": f"{rule.get('label')}: {source.name}"}
                    )

        facts = [
            Fact(name=f"sources_{stype}", value=count,
                 detail=f"{count} {stype} records in workspace")
            for stype, count in sorted(by_type.items())
        ]
        for bucket in BUCKETS:
            facts.append(
                Fact(
                    name=f"brief_{bucket}",
                    value=len(items[bucket]),
                    detail="; ".join(
                        f"{i['label']} ({i['source']})" for i in items[bucket][:5]
                    ) or None,
                )
            )
        return {"facts": facts, "tables": {"brief_items": items, "used_sources": used}}

    return brief_aggregate
