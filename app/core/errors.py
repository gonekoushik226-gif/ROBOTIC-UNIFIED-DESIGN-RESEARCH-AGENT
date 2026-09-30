"""Error model.

Implements the specification's failure requirements:

* Part 4 section 134 - failures are detected, classified, logged and returned. A
  bare `except:` or a swallowed exception is forbidden anywhere in the codebase
  (mechanically enforced by tests/unit/test_code_rules.py).
* Part 4 section 135 - the failure category vocabulary.
* Part 4 section 136 - a failure must say what failed, why, at which stage, what
  information is missing, whether it can be retried, and what to do next.
* Part 6 section 38 - a failure must also say what was already completed, what was
  not completed, and whether data was changed.

Design note
-----------
`data_changed` and `retry_safe` are `bool | None`, where None means "not known".
An unknown value must never be rendered as "no"; see `_tristate`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FailureCategory(StrEnum):
    """Classification of a failure (master specification Part 4 section 135).

    The specification says "categories such as", so the list may grow, but no
    listed category may be removed.
    """

    INVALID_INPUT = "INVALID_INPUT"
    MISSING_INFORMATION = "MISSING_INFORMATION"
    MISSING_SOURCE = "MISSING_SOURCE"
    EXTRACTION_FAILURE = "EXTRACTION_FAILURE"
    OCR_FAILURE = "OCR_FAILURE"
    DATABASE_FAILURE = "DATABASE_FAILURE"
    PARSER_FAILURE = "PARSER_FAILURE"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    AUTHORIZATION_FAILURE = "AUTHORIZATION_FAILURE"
    PERMISSION_FAILURE = "PERMISSION_FAILURE"
    APPLICATION_NOT_FOUND = "APPLICATION_NOT_FOUND"
    APPLICATION_NOT_RESPONDING = "APPLICATION_NOT_RESPONDING"
    ACTION_FAILURE = "ACTION_FAILURE"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    CONFLICT = "CONFLICT"
    STALE_INFORMATION = "STALE_INFORMATION"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    TIMEOUT = "TIMEOUT"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


def _tristate(value: bool | None) -> str:
    """Render an unknown boolean as "unknown" rather than silently as "no"."""
    if value is None:
        return "unknown"
    return "yes" if value else "no"


@dataclass(frozen=True, slots=True)
class FailureReport:
    """A complete, actionable description of one failure.

    Each field answers a question the specification requires a failure to answer.
    Only category, summary and reason are mandatory.
    """

    category: FailureCategory
    #: What failed, in one line, in the user's own terms.
    summary: str
    #: Why it failed.
    reason: str
    #: Which stage was running, e.g. "config.load" or "startup.directories".
    stage: str | None = None
    #: Information that is required but absent.
    missing: tuple[str, ...] = ()
    #: Information that is present, so the user can see the gap.
    available: tuple[str, ...] = ()
    #: Steps that completed before the failure (Part 6 section 38).
    completed: tuple[str, ...] = ()
    #: Steps that did not run because of the failure.
    not_completed: tuple[str, ...] = ()
    #: Whether persistent data was modified. None = not known.
    data_changed: bool | None = None
    #: Whether retrying is safe. None = not known.
    retry_safe: bool | None = None
    #: Concrete next actions offered to the user.
    next_options: tuple[str, ...] = ()
    #: Technical detail, for logs rather than the normal interface.
    detail: str | None = None
    #: Repr of the underlying exception, if there was one.
    cause: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Machine-readable form, safe to serialise as JSON."""
        return {
            "category": str(self.category),
            "summary": self.summary,
            "reason": self.reason,
            "stage": self.stage,
            "missing": list(self.missing),
            "available": list(self.available),
            "completed": list(self.completed),
            "not_completed": list(self.not_completed),
            "data_changed": self.data_changed,
            "retry_safe": self.retry_safe,
            "next_options": list(self.next_options),
            "detail": self.detail,
            "cause": self.cause,
        }

    def to_text(self, *, technical: bool = False) -> str:
        """Human-readable form, following the layout of Part 4 section 136.

        technical=True adds the detail and cause lines, which belong in logs
        rather than in the normal user interface (Part 6 section 38).
        """
        lines = [self.summary, f"Reason: {self.reason}"]
        if self.stage:
            lines.append(f"Stage: {self.stage}")
        if self.missing:
            lines.append("Missing:")
            lines.extend(f"  {item}" for item in self.missing)
        if self.available:
            lines.append("Available:")
            lines.extend(f"  {item}" for item in self.available)
        if self.completed:
            lines.append("Already completed:")
            lines.extend(f"  {item}" for item in self.completed)
        if self.not_completed:
            lines.append("Not completed:")
            lines.extend(f"  {item}" for item in self.not_completed)
        lines.append(f"Data changed: {_tristate(self.data_changed)}")
        lines.append(f"Retry safe: {_tristate(self.retry_safe)}")
        if self.next_options:
            lines.append("Next options:")
            lines.extend(f"  {i}. {opt}" for i, opt in enumerate(self.next_options, 1))
        if technical:
            if self.detail:
                lines.append(f"Detail: {self.detail}")
            if self.cause:
                lines.append(f"Cause: {self.cause}")
        return "\n".join(lines)


class RudraError(Exception):
    """Base class for a RUDRA failure that is raised rather than returned.

    The exception always carries a full FailureReport, so no caller has to
    reconstruct context from a bare message string.
    """

    #: Category used when a caller does not give one.
    default_category: FailureCategory = FailureCategory.UNKNOWN_ERROR

    def __init__(self, report: FailureReport) -> None:
        super().__init__(report.summary)
        self.report = report

    @classmethod
    def of(cls, summary: str, reason: str, **kwargs: object) -> RudraError:
        """Build the error and its report in one call.

        Any FailureReport field may be passed as a keyword argument. `category`
        defaults to the subclass default.
        """
        fields: dict[str, object] = dict(kwargs)
        fields.setdefault("category", cls.default_category)
        return cls(FailureReport(summary=summary, reason=reason, **fields))  # type: ignore[arg-type]

    def __str__(self) -> str:
        return self.report.to_text()


class ConfigurationError(RudraError):
    """Configuration is absent, malformed, or holds an unusable value."""

    default_category = FailureCategory.INVALID_INPUT


class InvalidInputError(RudraError):
    """A caller supplied an argument that cannot be used."""

    default_category = FailureCategory.INVALID_INPUT


class StorageError(RudraError):
    """A filesystem operation needed for startup failed."""

    default_category = FailureCategory.DATABASE_FAILURE


class ResourceLimitError(RudraError):
    """An operation was refused because a resource budget would be exceeded."""

    default_category = FailureCategory.RESOURCE_LIMIT


def unexpected(exc: BaseException, *, stage: str) -> FailureReport:
    """Wrap an unforeseen exception without hiding it and without guessing a cause."""
    return FailureReport(
        category=FailureCategory.UNKNOWN_ERROR,
        summary="RUDRA stopped because of an unexpected internal error.",
        reason=f"{type(exc).__name__}: {exc}",
        stage=stage,
        data_changed=None,
        retry_safe=None,
        next_options=(
            "Run the command again to see whether the error repeats.",
            "Read the technical detail in logs/rudra.log.",
            "Report the problem together with that log extract.",
        ),
        cause=repr(exc),
    )
