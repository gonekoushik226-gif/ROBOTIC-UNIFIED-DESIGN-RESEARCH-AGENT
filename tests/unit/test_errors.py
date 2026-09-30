"""Error model tests.

The specification is unusually specific about failures, so these tests check the
required vocabulary and the required content of a failure report rather than just
exercising the code.
"""

from __future__ import annotations

import pytest

from app.core.errors import (
    ConfigurationError,
    FailureCategory,
    FailureReport,
    RudraError,
    unexpected,
)

#: The exact list from master specification Part 4 section 135.
SPEC_CATEGORIES = {
    "INVALID_INPUT",
    "MISSING_INFORMATION",
    "MISSING_SOURCE",
    "EXTRACTION_FAILURE",
    "OCR_FAILURE",
    "DATABASE_FAILURE",
    "PARSER_FAILURE",
    "NETWORK_FAILURE",
    "AUTHORIZATION_FAILURE",
    "PERMISSION_FAILURE",
    "APPLICATION_NOT_FOUND",
    "APPLICATION_NOT_RESPONDING",
    "ACTION_FAILURE",
    "VERIFICATION_FAILURE",
    "CONFLICT",
    "STALE_INFORMATION",
    "RESOURCE_LIMIT",
    "TIMEOUT",
    "UNKNOWN_ERROR",
}


def test_every_specified_failure_category_exists():
    """No category from Part 4 section 135 may be missing."""
    defined = {member.value for member in FailureCategory}
    assert SPEC_CATEGORIES <= defined, SPEC_CATEGORIES - defined


def test_failure_report_answers_the_required_questions():
    """Part 4 section 136 and Part 6 section 38."""
    report = FailureReport(
        category=FailureCategory.MISSING_INFORMATION,
        summary="Unable to calculate ID.",
        reason="The applicable equation requires VGS.",
        stage="reasoning.dependencies",
        missing=("VGS",),
        available=("VT", "k", "VDS"),
        completed=("target identified",),
        not_completed=("calculation",),
        data_changed=False,
        retry_safe=True,
        next_options=("Provide VGS.", "Select another source.", "Cancel."),
    )
    text = report.to_text()
    assert "Unable to calculate ID." in text
    assert "Reason: The applicable equation requires VGS." in text
    assert "Stage: reasoning.dependencies" in text
    assert "VGS" in text
    assert "VT" in text
    assert "Data changed: no" in text
    assert "Retry safe: yes" in text
    assert "1. Provide VGS." in text


def test_unknown_booleans_are_reported_as_unknown_not_as_no():
    """An unknown value must never be presented as a definite 'no'."""
    report = FailureReport(
        category=FailureCategory.UNKNOWN_ERROR,
        summary="Something failed.",
        reason="Cause not established.",
    )
    text = report.to_text()
    assert "Data changed: unknown" in text
    assert "Retry safe: unknown" in text


def test_technical_detail_is_withheld_unless_requested():
    """Part 6 section 38: technical noise belongs in logs, not the normal interface."""
    report = FailureReport(
        category=FailureCategory.DATABASE_FAILURE,
        summary="Write failed.",
        reason="Disk error.",
        detail="errno=28",
        cause="OSError(28)",
    )
    assert "errno=28" not in report.to_text()
    assert "errno=28" in report.to_text(technical=True)
    assert "OSError(28)" in report.to_text(technical=True)


def test_report_is_json_serialisable():
    import json

    report = FailureReport(
        category=FailureCategory.TIMEOUT,
        summary="Timed out.",
        reason="No response.",
        missing=("answer",),
    )
    restored = json.loads(json.dumps(report.to_dict()))
    assert restored["category"] == "TIMEOUT"
    assert restored["missing"] == ["answer"]
    assert restored["data_changed"] is None


def test_report_is_immutable():
    """Part 1 section 24: avoid mutable shared state."""
    report = FailureReport(
        category=FailureCategory.CONFLICT, summary="s", reason="r"
    )
    with pytest.raises(AttributeError):
        report.summary = "changed"  # type: ignore[misc]


def test_error_carries_its_report_and_default_category():
    error = ConfigurationError.of("Bad config.", "Missing bracket.")
    assert isinstance(error, RudraError)
    assert error.report.category is FailureCategory.INVALID_INPUT
    assert "Bad config." in str(error)


def test_error_of_accepts_an_explicit_category():
    error = RudraError.of(
        "Ran out of room.", "Disk full.", category=FailureCategory.RESOURCE_LIMIT
    )
    assert error.report.category is FailureCategory.RESOURCE_LIMIT


def test_unexpected_preserves_the_cause_and_offers_next_steps():
    """An unforeseen error must be reported, never swallowed or guessed at."""
    try:
        raise ValueError("boom")
    except ValueError as exc:
        report = unexpected(exc, stage="cli.start")

    assert report.category is FailureCategory.UNKNOWN_ERROR
    assert "boom" in report.reason
    assert report.stage == "cli.start"
    assert report.cause is not None and "ValueError" in report.cause
    assert report.next_options
    # It must not claim to know whether data changed.
    assert report.data_changed is None
