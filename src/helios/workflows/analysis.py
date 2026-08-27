"""
Deterministic structured-data analysis.

The LLM is never the source of truth for arithmetic.  Every function here is
pure, typed, and unit-tested; workflow packs compose them into analysis
steps whose outputs become COMPUTED FACTS with kind="computation".
"""

from __future__ import annotations

import statistics
from typing import Any

from helios.workflows.types import Fact


def summarize_series(values: list[float]) -> dict[str, float | None]:
    """min/max/mean/median/stdev for a numeric series (None-safe)."""
    clean = [v for v in values if isinstance(v, (int, float))]
    if not clean:
        return {"count": 0, "min": None, "max": None, "mean": None,
                "median": None, "stdev": None}
    return {
        "count": len(clean),
        "min": min(clean),
        "max": max(clean),
        "mean": round(statistics.fmean(clean), 4),
        "median": round(statistics.median(clean), 4),
        "stdev": round(statistics.stdev(clean), 4) if len(clean) > 1 else 0.0,
    }


def pct_change(old: float, new: float) -> float | None:
    """Percentage change new vs old; None when old == 0 (never fabricate)."""
    if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
        return None
    if old == 0:
        return None
    return round((new - old) / abs(old) * 100.0, 2)


def compare_records(
    old: dict[str, Any], new: dict[str, Any], parameters: list[str] | None = None
) -> list[dict[str, Any]]:
    """
    Parameter-by-parameter comparison of two structured records.

    Returns absolute + percentage changes for shared numeric parameters and
    flags parameters missing on either side (explicit missing-data state).
    """
    keys = parameters or sorted(set(old) | set(new))
    rows: list[dict[str, Any]] = []
    for key in keys:
        a, b = old.get(key), new.get(key)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            if a is None or b is None:
                rows.append({"parameter": key, "status": "missing_data",
                             "old": a, "new": b})
            continue
        rows.append(
            {
                "parameter": key,
                "old": a,
                "new": b,
                "abs_change": round(b - a, 4),
                "pct_change": pct_change(a, b),
                "status": "compared",
            }
        )
    return rows


def threshold_violations(
    record: dict[str, Any], thresholds: dict[str, dict[str, float]]
) -> list[dict[str, Any]]:
    """
    Check a record against configured thresholds:
    thresholds = {"max_temp_c": {"max": 60}, "capacity_retention_pct": {"min": 90}}
    """
    violations = []
    for param, limits in thresholds.items():
        value = record.get(param)
        if not isinstance(value, (int, float)):
            continue
        if "max" in limits and value > limits["max"]:
            violations.append({"parameter": param, "value": value,
                               "limit": limits["max"], "kind": "above_max"})
        if "min" in limits and value < limits["min"]:
            violations.append({"parameter": param, "value": value,
                               "limit": limits["min"], "kind": "below_min"})
    return violations


def missing_fields(record: dict[str, Any], required: list[str]) -> list[str]:
    return [f for f in required if record.get(f) is None]


def zscore_anomalies(
    values: dict[str, float], history: dict[str, list[float]], z: float = 2.0
) -> list[dict[str, Any]]:
    """Flag values more than `z` standard deviations from their history."""
    anomalies = []
    for key, value in values.items():
        series = [v for v in history.get(key, []) if isinstance(v, (int, float))]
        if len(series) < 2 or not isinstance(value, (int, float)):
            continue
        mean = statistics.fmean(series)
        stdev = statistics.stdev(series)
        if stdev == 0:
            continue
        score = (value - mean) / stdev
        if abs(score) >= z:
            anomalies.append({"parameter": key, "value": value,
                              "mean": round(mean, 4), "zscore": round(score, 2)})
    return anomalies


def aggregate_by(
    records: list[dict[str, Any]], key: str, value_field: str | None = None
) -> dict[str, Any]:
    """Group records by a key; count and (optionally) sum a numeric field."""
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        group = str(record.get(key, "unknown"))
        entry = groups.setdefault(group, {"count": 0, "sum": 0.0})
        entry["count"] += 1
        if value_field and isinstance(record.get(value_field), (int, float)):
            entry["sum"] = round(entry["sum"] + record[value_field], 4)
    return groups


def facts_from_comparison(rows: list[dict[str, Any]], notable_pct: float = 10.0) -> list[Fact]:
    """Turn comparison rows into named computed facts + notable-change flags."""
    facts: list[Fact] = []
    notable = 0
    for row in rows:
        if row.get("status") != "compared":
            continue
        facts.append(
            Fact(
                name=f"{row['parameter']}_change",
                value=row["pct_change"],
                unit="%",
                detail=(
                    f"{row['parameter']}: {row['old']} -> {row['new']} "
                    f"({row['abs_change']:+g} abs, "
                    f"{row['pct_change']:+g}% )" if row["pct_change"] is not None
                    else f"{row['parameter']}: {row['old']} -> {row['new']}"
                ),
            )
        )
        if row["pct_change"] is not None and abs(row["pct_change"]) >= notable_pct:
            notable += 1
    facts.append(Fact(name="notable_changes", value=notable,
                      detail=f"parameters changing >= {notable_pct}%"))
    return facts
