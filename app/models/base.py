"""Shared model machinery: timestamps and validation.

Timestamps are ISO-8601 in **UTC with a `Z` suffix** (approved 2026-09-20):

    2026-09-20T04:46:14.030Z

Always UTC, so lexicographic order equals chronological order. Storing a local
offset would break `ORDER BY` as soon as two offsets appeared.

Validation follows ADR 0005 as settled for Phase 2: `validate()` returns
`Result[None]`, and an `Err` lists **every** problem found, not just the first, so
a user correcting a record sees the whole picture at once.
"""

import re
from datetime import datetime, timezone
from typing import Final

from app.core.errors import FailureCategory, FailureReport
from app.core.result import Err, Ok, Result

#: 2026-09-20T04:46:14.030Z
TIMESTAMP_PATTERN: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)


def utc_now() -> str:
    """The current instant, in RUDRA's canonical timestamp form."""
    moment = datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def parse_timestamp(value: str) -> datetime:
    """Parse a canonical timestamp back into an aware `datetime`."""
    if not is_valid_timestamp(value):
        raise ValueError(f"not a canonical RUDRA timestamp: {value!r}")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )


def is_valid_timestamp(value: object) -> bool:
    """True when `value` is a canonical timestamp string."""
    return isinstance(value, str) and TIMESTAMP_PATTERN.match(value) is not None


class Problems:
    """Collects every validation problem, so all of them can be reported together."""

    __slots__ = ("_items",)

    def __init__(self) -> None:
        self._items: list[str] = []

    def require(self, condition: bool, message: str) -> None:
        """Record `message` when `condition` is false."""
        if not condition:
            self._items.append(message)

    def require_text(self, value: object, field: str) -> None:
        """Require a non-empty, non-blank string."""
        self.require(
            isinstance(value, str) and bool(value.strip()),
            f"{field} must be a non-empty string",
        )

    def require_timestamp(self, value: object, field: str) -> None:
        self.require(
            is_valid_timestamp(value),
            f"{field} must be an ISO-8601 UTC timestamp such as "
            "2026-09-20T04:46:14.030Z",
        )

    def require_id(self, value: object, field: str, kind: object = None) -> None:
        from app.models.identifiers import is_valid_id

        expected = f" of kind {kind}" if kind is not None else ""
        self.require(
            is_valid_id(value, kind=kind),  # type: ignore[arg-type]
            f"{field} must be a RUDRA identifier{expected}, got {value!r}",
        )

    def require_non_negative(self, value: object, field: str) -> None:
        self.require(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0,
            f"{field} must be an integer of 0 or more",
        )

    @property
    def items(self) -> tuple[str, ...]:
        return tuple(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)


def validation_failure(entity_name: str, identifier: object, problems: Problems) -> Err:
    """Build the `Err` returned by a failed `validate()`."""
    count = len(problems.items)
    return Err(
        FailureReport(
            category=FailureCategory.INVALID_INPUT,
            summary=f"{entity_name} is not valid and was not accepted.",
            reason=(
                f"{count} field {'problem' if count == 1 else 'problems'} were found."
            ),
            stage="models.validate",
            missing=problems.items,
            data_changed=False,
            retry_safe=True,
            detail=f"id={identifier!r}",
            next_options=(
                "Correct the listed fields and try again.",
                "Run 'python -m app db' to inspect the current schema.",
            ),
        )
    )


def ok() -> Result[None]:
    """The successful result of `validate()`."""
    return Ok(None)
