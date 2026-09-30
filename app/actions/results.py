"""Read models of plans and executions (ADR 0046 P14-5, P14-8; sections 103, 114, 116).

A `PlanStep` carries section 103's fields; a `StepResult` carries section 114's failure
record whatever the outcome; an `ExecutionReport` is the whole trace of section 116 for one
plan. Returned; the one side effect of an execution is the audit line (P14-11).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum

from app.models.enums import RiskLevel


class StepStatus(StrEnum):
    #: The postcondition was observed.
    VERIFIED = "VERIFIED"
    #: The postcondition was checked and does not hold.
    FAILED = "FAILED"
    #: Executed, but the outcome cannot be observed; never reported as success.
    INCONCLUSIVE = "INCONCLUSIVE"
    #: A precondition failed; the step was not executed (section 104).
    BLOCKED = "BLOCKED"
    #: An earlier step did not succeed, so this one was not attempted.
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    #: The permission check refused the step (Phase 15).
    REFUSED = "REFUSED"


@dataclass(frozen=True, slots=True)
class Condition:
    """A precondition checked before execution (section 104; P6 section 29)."""

    text: str
    satisfied: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Attempt:
    """One execution attempt: how it was made, and what the call returned."""

    method: str
    returned: str


@dataclass(frozen=True, slots=True)
class PlanStep:
    """Section 103: action, parameters, preconditions, risk, expected result, verification."""

    number: int
    action: str
    parameters: tuple[tuple[str, str], ...]
    risk_level: RiskLevel
    preconditions: tuple[str, ...]
    expected: str
    verification: str
    #: What the registry resolved for the step (for OPEN_APPLICATION, its launch data).
    resolved: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    steps: tuple[PlanStep, ...]
    #: The platform the plan will run on, and whether it is live.
    platform: str
    live: bool


@dataclass(frozen=True, slots=True)
class StepResult:
    """One step's record (section 114), whatever its outcome."""

    number: int
    action: str
    parameters: tuple[tuple[str, str], ...]
    risk_level: RiskLevel
    status: StepStatus
    conditions: tuple[Condition, ...]
    executed: bool
    attempts: tuple[Attempt, ...]
    expected: str
    observed: str
    detail: str
    #: The failure stage: PRECONDITION, EXECUTION or VERIFICATION; None on success.
    failure_stage: str | None = None
    error: str | None = None
    started: str = ""
    finished: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """The whole trace of one plan (section 116). DRY RUN when the platform is not live."""

    plan: ExecutionPlan
    steps: tuple[StepResult, ...]
    status: StepStatus
    message: str
    dry_run: bool
    notes: tuple[str, ...] = ()


def overall(statuses: tuple[StepStatus, ...]) -> StepStatus:
    """The plan's status: every step VERIFIED is VERIFIED; otherwise the first problem."""
    for status in (StepStatus.REFUSED, StepStatus.BLOCKED, StepStatus.FAILED, StepStatus.INCONCLUSIVE):
        if status in statuses:
            return status
    return StepStatus.VERIFIED if statuses else StepStatus.NOT_ATTEMPTED


def as_plain(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: as_plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (tuple, list)):
        return [as_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): as_plain(item) for key, item in value.items()}
    return value


def to_json(result: object) -> str:
    return json.dumps(as_plain(result), sort_keys=True, ensure_ascii=False, indent=2)
