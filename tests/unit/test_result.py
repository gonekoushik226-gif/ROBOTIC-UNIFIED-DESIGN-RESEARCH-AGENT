"""Result type tests."""

from __future__ import annotations

import pytest

from app.core.errors import FailureCategory, FailureReport, RudraError
from app.core.result import Err, Ok


def _report() -> FailureReport:
    return FailureReport(
        category=FailureCategory.MISSING_INFORMATION,
        summary="Nothing to return.",
        reason="The value was never computed.",
    )


def test_ok_carries_its_value():
    result = Ok(42)
    assert result.is_ok() and not result.is_err()
    assert result.unwrap() == 42
    assert result.unwrap_or(0) == 42


def test_err_reports_failure_and_refuses_to_invent_a_value():
    result = Err(_report())
    assert result.is_err() and not result.is_ok()
    assert result.unwrap_or("fallback") == "fallback"
    with pytest.raises(RudraError) as caught:
        result.unwrap()
    assert caught.value.report.summary == "Nothing to return."


def test_map_transforms_success_and_leaves_failure_untouched():
    assert Ok(2).map(lambda v: v * 3).unwrap() == 6
    failure = Err(_report())
    assert failure.map(lambda v: v * 3) is failure


def test_results_are_immutable():
    result = Ok(1)
    with pytest.raises(AttributeError):
        result.value = 2  # type: ignore[misc]
