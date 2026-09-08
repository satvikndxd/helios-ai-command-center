"""
Data classification vocabulary + classifier (POLICY plane).

Canonical data classes, ordered by severity:

    PUBLIC         openly shareable
    INTERNAL       fine inside the org, not for external models/destinations
    CONFIDENTIAL   business-sensitive; external routing must be policy-approved
    SENSITIVE      high-impact material (financial, health, credentials-adjacent)
    PII            personal data — detected content always raises to at least PII

Rules:
* The classifier only ever returns classes and counts. Raw sensitive content
  is never copied into evidence — V1's scrub-invariants stand.
* Effective class of a payload = max severity of (declared classes ∪ detected
  classes). Detection reuses the battle-tested `sentinel.detect_pii` regexes —
  deterministic, zero-dependency.
* Unknown class strings are rejected at registration (`parse_class`) and
  treated as SENSITIVE at evaluation time (`normalize_class`, fail-closed).
"""

from __future__ import annotations

import re

from helios.sentinel import detect_pii

DATA_CLASSES = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "SENSITIVE", "PII")
SEVERITY = {name: i for i, name in enumerate(DATA_CLASSES)}

# Argument/text patterns that mark material as at least SENSITIVE even when
# the declared class is lower. Deliberately conservative and deterministic:
# secret-shaped tokens and financial/health vocabulary.
_SENSITIVE_PATTERN = re.compile(
    r"(api[_-]?key|secret|token|passw(or)?d|private[_-]?key|-----BEGIN|"
    r"credit[_-]?card|bank[_-]?account|routing[_-]?number|diagnos|medical[_-]?record|"
    r"social[_-]?security)",
    re.IGNORECASE,
)


def parse_class(value: str) -> str:
    """Strict parse for registration: raises ValueError on unknown classes."""
    if not isinstance(value, str):
        raise ValueError(f"data class must be a string, got {type(value).__name__}")
    upper = value.strip().upper()
    if upper not in SEVERITY:
        raise ValueError(f"unknown data class {value!r} (expected one of {DATA_CLASSES})")
    return upper


def parse_classes(values) -> list[str]:
    """Strict parse of a list; de-duplicated, sorted by severity (desc)."""
    parsed = {parse_class(v) for v in (values or [])}
    return sort_classes(parsed)


def normalize_class(value: str) -> str:
    """Fail-closed parse for evaluation paths: unknown -> SENSITIVE."""
    try:
        return parse_class(value)
    except ValueError:
        return "SENSITIVE"


def sort_classes(classes) -> list[str]:
    """Most severe first."""
    return sorted({normalize_class(c) for c in classes}, key=lambda c: -SEVERITY[c])


def max_class(classes) -> str:
    """Highest-severity class of a collection; empty -> PUBLIC."""
    ordered = sort_classes(classes)
    return ordered[0] if ordered else "PUBLIC"


def at_least(candidate: str, threshold: str) -> bool:
    """True if candidate is at or above threshold in severity."""
    return SEVERITY[normalize_class(candidate)] >= SEVERITY[normalize_class(threshold)]


def classify_text(text: str) -> dict:
    """
    Detect data classes present in a text payload.

    Returns {"classes": [...], "pii": {type: count}, "sensitive_markers": int}.
    Only classes/counts — never the matched content itself.
    """
    findings: dict = {"classes": [], "pii": {}, "sensitive_markers": 0}
    if not text:
        return findings
    pii = detect_pii(text)
    markers = len(_SENSITIVE_PATTERN.findall(text))
    classes: set[str] = set()
    if pii:
        classes.add("PII")
    if markers:
        classes.add("SENSITIVE")
    findings["classes"] = sort_classes(classes)
    findings["pii"] = pii
    findings["sensitive_markers"] = markers
    return findings


def classify_args(args: dict, declared: list[str] | None = None) -> dict:
    """
    Effective classification for a tool-call payload:

        effective = max(severity(declared) ∪ severity(detected-in-args))

    Detection walks string leaves of the args (bounded — large payloads are
    truncated per-leaf; classification is not a full-text indexer).
    """
    declared_classes = parse_classes(declared or [])
    detected: set[str] = set()
    pii_total: dict[str, int] = {}
    markers = 0

    def _walk(value, depth: int = 0) -> None:
        nonlocal markers
        if depth > 6:
            return
        if isinstance(value, str):
            found = classify_text(value[:8192])
            detected.update(found["classes"])
            markers += found["sensitive_markers"]
            for kind, count in found["pii"].items():
                pii_total[kind] = pii_total.get(kind, 0) + count
        elif isinstance(value, dict):
            for v in value.values():
                _walk(v, depth + 1)
        elif isinstance(value, list):
            for v in value[:64]:
                _walk(v, depth + 1)

    _walk(args or {})
    effective = max_class(set(declared_classes) | detected)
    return {
        "declared": declared_classes,
        "detected": sort_classes(detected),
        "effective": effective,
        "pii_counts": pii_total,
        "sensitive_markers": markers,
    }
