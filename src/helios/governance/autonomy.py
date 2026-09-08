"""
Autonomy levels — a first-class governance primitive (POLICY plane).

Canonical scale:

    L0  informational only      the system surfaces information; no decisions
    L1  recommendations         proposes options; a human decides and acts
    L2  supervised actions      acts with a human supervising each action class
    L3  conditional autonomy    acts alone within policy bounds; gated actions
                                require approval
    L4  high autonomy           acts alone broadly; only critical gates remain
    L5  unrestricted autonomy   no systematic human gates (production use is
                                expected to be blocked by policy)

Compatibility: V1 sessions carry `autonomy: "supervised" | "autonomous"`.
Those strings remain valid everywhere; the mapping below is deterministic so
old sessions and old recorded traces replay identically:

    supervised  -> L2
    autonomous  -> L4

The mapping is one-way for legacy data; new registrations set the level
explicitly and MAY also carry the legacy string for old consumers.
"""

from __future__ import annotations

AUTONOMY_LEVELS: dict[int, str] = {
    0: "informational",
    1: "recommendations",
    2: "supervised actions",
    3: "conditional autonomy",
    4: "high autonomy",
    5: "unrestricted autonomy",
}

MIN_LEVEL = 0
MAX_LEVEL = 5

# Deterministic legacy-string mapping (V1 -> V1.5).
LEGACY_TO_LEVEL = {"supervised": 2, "autonomous": 4}
LEVEL_TO_LEGACY = {2: "supervised", 4: "autonomous"}


def label(level: int) -> str:
    """'L3 — conditional autonomy' style label for UIs."""
    level = normalize_level(level)
    return f"L{level} — {AUTONOMY_LEVELS[level].upper()}"


def normalize_level(value) -> int:
    """
    Coerce any autonomy representation to a canonical 0..5 int.

    Accepts ints, numeric strings, 'L3'-style strings, and the legacy
    'supervised'/'autonomous' vocabulary. Unknown values fail closed to the
    MOST restrictive level (L5 semantics are never granted by accident):
    an unparseable value maps to 5 so ceiling policies treat it as the highest
    autonomy and gate it. Callers validating registration should use
    `parse_level` which raises instead.
    """
    if isinstance(value, bool):
        raise ValueError("autonomy level must not be a bool")
    if isinstance(value, int):
        return min(max(value, MIN_LEVEL), MAX_LEVEL)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in LEGACY_TO_LEVEL:
            return LEGACY_TO_LEVEL[text]
        if text.startswith("l") and text[1:].isdigit():
            return min(max(int(text[1:]), MIN_LEVEL), MAX_LEVEL)
        if text.isdigit():
            return min(max(int(text), MIN_LEVEL), MAX_LEVEL)
    return MAX_LEVEL  # fail closed: unknown autonomy is treated as highest


def parse_level(value) -> int:
    """Strict parse for registration/updates: raises ValueError on garbage."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"autonomy_level must be an int 0..5 or 'L0'..'L5', got {value!r}")
    if isinstance(value, int):
        if not (MIN_LEVEL <= value <= MAX_LEVEL):
            raise ValueError(f"autonomy_level must be 0..5, got {value}")
        return value
    text = value.strip().lower()
    if text in LEGACY_TO_LEVEL:
        return LEGACY_TO_LEVEL[text]
    if text.startswith("l") and text[1:].isdigit():
        text = text[1:]
    if text.isdigit() and MIN_LEVEL <= int(text) <= MAX_LEVEL:
        return int(text)
    raise ValueError(f"autonomy_level must be 0..5 (or 'supervised'/'autonomous'), got {value!r}")


def legacy_string(level: int) -> str:
    """Best-effort legacy vocabulary for V1 consumers ('supervised'|'autonomous')."""
    return "autonomous" if normalize_level(level) >= 3 else "supervised"
