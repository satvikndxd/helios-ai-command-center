"""
POLICY/EVIDENCE support — deterministic data classification.

Data classes (ascending sensitivity):

    PUBLIC < INTERNAL < CONFIDENTIAL < SENSITIVE < PII

Classification combines:
  * declared context (a system's declared data_classes, a request hint)
  * detected signals from Sentinel (PII patterns, secrets)

Deterministic and explainable — every classification carries the signals
that produced it. No ML, no guessing; more advanced detectors slot in
behind the same function later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from helios.sentinel import detect_pii
from helios.web.sanitize import scrub_secrets


DATA_CLASSES = ("public", "internal", "confidential", "sensitive", "pii")
_ORDER = {name: i for i, name in enumerate(DATA_CLASSES)}


def max_class(classes: list[str]) -> str:
    """The most sensitive class in a list (defaults to public)."""
    present = [c.lower() for c in classes if c.lower() in _ORDER]
    if not present:
        return "public"
    return max(present, key=lambda c: _ORDER[c])


def at_least(data_class: str, threshold: str) -> bool:
    return _ORDER.get(data_class.lower(), 0) >= _ORDER.get(threshold.lower(), 0)


@dataclass
class Classification:
    data_class: str
    classes: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "data_class": self.data_class,
            "classes": list(self.classes),
            "signals": list(self.signals),
        }


def classify_text(text: str, declared: list[str] | None = None) -> Classification:
    """
    Classify a piece of text plus any declared context classes.

    PII patterns force at least PII; secret material forces at least
    SENSITIVE. Declared classes are always honored (a system that declares
    it touches CONFIDENTIAL data does not get downgraded just because a
    particular string looks benign).
    """
    classes = {c.lower() for c in (declared or []) if c.lower() in _ORDER}
    signals: list[str] = []
    if declared:
        signals.append(f"declared: {sorted(classes)}")

    text = text or ""
    pii = detect_pii(text)
    if pii:
        classes.add("pii")
        signals.append(f"pii detected: {sorted(pii)}")

    _, secret_count = scrub_secrets(text)
    if secret_count:
        classes.add("sensitive")
        signals.append(f"secret material detected: {secret_count}")

    if not classes:
        classes.add("public")

    return Classification(
        data_class=max_class(list(classes)),
        classes=sorted(classes, key=lambda c: _ORDER[c]),
        signals=signals,
    )


def classify_args(args: dict, declared: list[str] | None = None) -> Classification:
    """Classify a tool-argument payload (values concatenated)."""
    blob = " ".join(str(v) for v in (args or {}).values())
    return classify_text(blob, declared)
