"""
Governance vocabulary unit tests: autonomy levels (L0–L5) and data classes.

These vocabularies are governance primitives: the autonomy module includes the
deterministic legacy mapping (supervised→L2, autonomous→L4) that keeps V1
sessions and recorded traces replaying identically, and fail-closed behavior
for unknown values.
"""

import pytest

from helios.governance import autonomy
from helios.governance import data as dataclasses


# --- autonomy ---------------------------------------------------------------


def test_autonomy_labels():
    assert autonomy.label(0) == "L0 — INFORMATIONAL"
    assert autonomy.label(3) == "L3 — CONDITIONAL AUTONOMY"
    assert autonomy.label(5) == "L5 — UNRESTRICTED AUTONOMY"
    assert len(autonomy.AUTONOMY_LEVELS) == 6


def test_autonomy_parse_strict():
    assert autonomy.parse_level(3) == 3
    assert autonomy.parse_level("L4") == 4
    assert autonomy.parse_level("2") == 2
    assert autonomy.parse_level("supervised") == 2
    assert autonomy.parse_level("autonomous") == 4
    with pytest.raises(ValueError):
        autonomy.parse_level(7)
    with pytest.raises(ValueError):
        autonomy.parse_level(-1)
    with pytest.raises(ValueError):
        autonomy.parse_level("wild")
    with pytest.raises(ValueError):
        autonomy.parse_level(True)


def test_autonomy_normalize_fails_closed():
    # Unknown autonomy must never be mistaken for LOW autonomy: it normalizes
    # to the most restrictive end so ceiling policies gate it.
    assert autonomy.normalize_level("garbage") == 5
    assert autonomy.normalize_level(None) == 5
    assert autonomy.normalize_level("L2") == 2
    assert autonomy.normalize_level(99) == 5
    assert autonomy.normalize_level(-3) == 0


def test_autonomy_legacy_mapping_is_deterministic():
    assert autonomy.LEGACY_TO_LEVEL == {"supervised": 2, "autonomous": 4}
    assert autonomy.legacy_string(2) == "supervised"
    assert autonomy.legacy_string(4) == "autonomous"
    assert autonomy.legacy_string(3) == "autonomous"
    assert autonomy.legacy_string(1) == "supervised"


# --- data classes -------------------------------------------------------------


def test_data_class_ordering():
    assert dataclasses.DATA_CLASSES == (
        "PUBLIC", "INTERNAL", "CONFIDENTIAL", "SENSITIVE", "PII")
    assert dataclasses.at_least("PII", "CONFIDENTIAL")
    assert dataclasses.at_least("CONFIDENTIAL", "CONFIDENTIAL")
    assert not dataclasses.at_least("INTERNAL", "CONFIDENTIAL")


def test_data_class_parsing():
    assert dataclasses.parse_classes(["public", "PII", "internal"]) == [
        "PII", "INTERNAL", "PUBLIC"]
    assert dataclasses.max_class(["PUBLIC", "confidential"]) == "CONFIDENTIAL"
    assert dataclasses.max_class([]) == "PUBLIC"
    with pytest.raises(ValueError):
        dataclasses.parse_class("TOP_SECRET")
    # evaluation-time normalization fails closed to SENSITIVE
    assert dataclasses.normalize_class("TOP_SECRET") == "SENSITIVE"


def test_classify_text_detects_pii_and_sensitive():
    found = dataclasses.classify_text(
        "customer jane.doe@example.com card 4111 1111 1111 1111")
    assert "PII" in found["classes"]
    assert found["pii"]["email"] == 1
    assert found["pii"]["credit_card"] == 1

    found = dataclasses.classify_text("the api_key is configured in vault")
    assert "SENSITIVE" in found["classes"]
    assert found["sensitive_markers"] >= 1

    found = dataclasses.classify_text("the quarterly blog post draft")
    assert found["classes"] == []


def test_classify_args_effective_is_max_severity():
    # declared INTERNAL + detected PII -> effective PII
    result = dataclasses.classify_args(
        {"body": "contact john@corp.com for details"},
        declared=["INTERNAL"],
    )
    assert result["declared"] == ["INTERNAL"]
    assert result["detected"] == ["PII"]
    assert result["effective"] == "PII"
    assert result["pii_counts"]["email"] == 1

    # declared only
    result = dataclasses.classify_args({"path": "/src/main.py"},
                                       declared=["CONFIDENTIAL"])
    assert result["effective"] == "CONFIDENTIAL"

    # nothing declared, nothing detected -> PUBLIC
    result = dataclasses.classify_args({"path": "/src/main.py"})
    assert result["effective"] == "PUBLIC"


def test_classify_args_never_echoes_secret_content():
    """Classification returns classes/counts only — not matched material."""
    payload = {"creds": "password=hunter2 ssn 123-45-6789"}
    result = dataclasses.classify_args(payload)
    assert result["effective"] in ("PII", "SENSITIVE")
    blob = repr(result)
    assert "hunter2" not in blob
    assert "123-45-6789" not in blob
