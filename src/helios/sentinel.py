"""
Helios Sentinel — synchronous safety checks on the hot path.

Covers the Phase-1-scale versions of:
- PII detection & redaction (FR-GW-008, NFR-SEC-006)
- Prompt-injection detection, including in retrieved documents (FR-HD-008)
- Output data-leakage detection (NFR-SEC-006)

All heuristic/regex-based by design: deterministic, fast, zero-dependency.
Model-based classifiers slot in behind the same functions later.
"""

import re
from dataclasses import dataclass, field


# --- PII ---------------------------------------------------------------------

PII_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
}

# Leak-relevant PII: things that should never appear in model OUTPUT.
LEAK_TYPES = ("ssn", "credit_card")


def detect_pii(text: str) -> dict[str, int]:
    """Return {pii_type: count} for all PII found in text."""
    findings: dict[str, int] = {}
    for pii_type, pattern in PII_PATTERNS.items():
        n = len(pattern.findall(text or ""))
        if n:
            findings[pii_type] = n
    return findings


def redact_pii(text: str) -> tuple[str, dict[str, int]]:
    """Replace PII with [REDACTED:<type>] tokens. Returns (text, counts)."""
    counts: dict[str, int] = {}
    for pii_type, pattern in PII_PATTERNS.items():
        text, n = pattern.subn(f"[REDACTED:{pii_type}]", text)
        if n:
            counts[pii_type] = n
    return text, counts


# --- Prompt injection --------------------------------------------------------

INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore (all |any )?(previous|prior|above) (instructions|prompts)",
        r"disregard (the|all|your) (above|previous|prior|system)",
        r"reveal (the |your )?system prompt",
        r"you are now (unrestricted|jailbroken|free|dan)",
        r"pretend (you have no|there are no) (rules|restrictions|guidelines)",
        r"do not follow your (rules|instructions|guidelines)",
        r"new instructions supersede",
    ]
]


def detect_injection(text: str) -> list[str]:
    """Return the list of injection patterns matched in text."""
    return [p.pattern for p in INJECTION_PATTERNS if p.search(text or "")]


# --- Output leakage ----------------------------------------------------------

def scan_output_for_leaks(text: str) -> dict[str, int]:
    """Detect leak-class PII (SSN, credit card) in model output."""
    findings: dict[str, int] = {}
    for pii_type in LEAK_TYPES:
        n = len(PII_PATTERNS[pii_type].findall(text or ""))
        if n:
            findings[pii_type] = n
    return findings


# --- Aggregate ---------------------------------------------------------------

@dataclass
class SentinelReport:
    pii: dict[str, int] = field(default_factory=dict)
    injection_matches: list[str] = field(default_factory=list)
    dropped_chunks: list[str] = field(default_factory=list)  # chunk_ids
    output_leaks: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "pii": self.pii,
            "injection_matches": self.injection_matches,
            "dropped_chunks": self.dropped_chunks,
            "output_leaks": self.output_leaks,
        }
