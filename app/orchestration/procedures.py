"""Running a documented procedure the user named (ADR 0048 P16-4 ... P16-8; section 112).

    1. retrieve     the stored procedure, with steps checked against its evidence
    2. parameters   each `<name>` supplied by the user; a missing one is "Missing
                    information:" and nothing runs (section 112: never invented)
    3. steps        each step, parameters substituted, interpreted as exactly one catalogue
                    action - otherwise the procedure is not executable and nothing runs
    4. plan         one execution plan for every step (section 103)
    5. permission   `decide_documented`: live only with --confirm, and only LOW steps
    6. execute      the Phase 14 engine on the live or simulated computer
    7. verify       each step's postcondition
    8. record       a live run that executed: its verification and the counters

Text is never executed as a command (section 17), and nothing is added to the documented
steps (section 108). A dry run and a refused run change nothing, the database included.
"""

import json
import logging
import platform as host
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from app.actions import ActionEngine, ExecutionPlan, ExecutionReport, Platform, StepStatus
from app.core.errors import InvalidInputError, RudraError
from app.core.logging_setup import get_logger
from app.models.enums import VerificationStatus
from app.nlu import InterpretationStatus, interpret
from app.orchestration.pipeline import UNAVAILABLE, to_request
from app.procedures import StoredProcedure, parameter_key, substitute, value_problem
from app.security import Decision, PermissionDecision, all_permitted, decide_documented

_audit = get_logger("app.orchestration.procedures")
_audit.addHandler(logging.NullHandler())  # without configured logging, nowhere - never stderr


class ProcedureOutcome(StrEnum):
    #: Every step ran and was VERIFIED or INCONCLUSIVE (both reported).
    DONE = "DONE"
    #: A step failed verification, was blocked by a precondition, or was not attempted.
    FAILED = "FAILED"
    #: The permission engine refused a step; nothing ran.
    REFUSED = "REFUSED"
    #: A parameter the procedure needs was not given; nothing ran (section 112).
    MISSING_INFORMATION = "MISSING_INFORMATION"
    #: The procedure cannot be executed as documented; nothing ran.
    NOT_EXECUTABLE = "NOT_EXECUTABLE"


@dataclass(frozen=True, slots=True)
class StepMapping:
    """One documented step and the catalogue action it becomes."""

    number: int
    documented: str
    #: The step with the user's parameters substituted (the documented text for a preview).
    text: str
    action: str | None
    parameters: tuple[tuple[str, str], ...]
    #: Why the step cannot be executed, or "".
    problem: str


@dataclass(frozen=True, slots=True)
class ProcedureRun:
    procedure: StoredProcedure
    outcome: ProcedureOutcome
    message: str
    live: bool
    parameters: tuple[tuple[str, str], ...]
    missing: tuple[str, ...]
    steps: tuple[StepMapping, ...]
    plan: ExecutionPlan | None
    permissions: tuple[PermissionDecision, ...]
    execution: ExecutionReport | None
    #: The `verification` row recorded for this run, or None (a dry run, or nothing ran).
    recorded: str | None
    #: Why a live run that executed could not be recorded, or None. The run's effects are
    #: reported all the same: never hidden behind the storage failure (sections 114, 137).
    record_error: str | None = None


#: Records one live run: `ProcedureMemory.record`, or a function that opens the database
#: for writing only at that moment, so a refused or dry run never opens it for writing.
Recorder = Callable[..., object]


class ProcedureRunner:
    """Section 112's sequence for one stored procedure."""

    def __init__(self, platform: Platform, *, record: Recorder, screenshots: Path,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.platform = platform
        self.record = record
        self.screenshots = screenshots
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def preview(self, procedure: StoredProcedure) -> tuple[StepMapping, ...]:
        """Each documented step and the action it names, parameters left as written."""
        return self._map(procedure.steps, None)

    def run(self, procedure: StoredProcedure, supplied: tuple[tuple[str, str], ...], *,
            confirmed: bool) -> ProcedureRun:
        values, given = self._values(procedure, supplied)
        if not procedure.executable:
            return self._finish(procedure, ProcedureOutcome.NOT_EXECUTABLE,
                                f"nothing was executed: the procedure cannot be executed as documented - "
                                f"{procedure.reason}", given)
        missing = tuple(name for name in procedure.parameters if parameter_key(name) not in values)
        if missing:
            return self._finish(procedure, ProcedureOutcome.MISSING_INFORMATION,
                                "Missing information:\n" + "\n".join(missing), given, missing=missing)
        steps = self._map(procedure.steps, values)
        problems = [s for s in steps if s.problem]
        if problems:
            return self._finish(procedure, ProcedureOutcome.NOT_EXECUTABLE,
                                "nothing was executed: " + "; ".join(f"step {s.number} \"{s.text}\" {s.problem}"
                                                                     for s in problems), given, steps=steps)
        engine = ActionEngine(self.platform)
        plan = engine.plan([(s.action, dict(s.parameters)) for s in steps])  # raises before anything runs
        permissions = decide_documented(plan, confirmed=confirmed)
        if not all_permitted(permissions):
            refused = next(d for d in permissions if d.decision is Decision.REFUSED)
            return self._finish(procedure, ProcedureOutcome.REFUSED,
                                f"nothing was executed: step {refused.step} {refused.action} was refused - "
                                f"{refused.reason}", given, steps=steps, plan=plan, permissions=permissions)
        execution = engine.run(plan)
        good = all(s.status in (StepStatus.VERIFIED, StepStatus.INCONCLUSIVE) for s in execution.steps)
        if good:
            outcome, message = ProcedureOutcome.DONE, execution.message
        else:
            failed = next(s for s in execution.steps if s.status not in (StepStatus.VERIFIED, StepStatus.INCONCLUSIVE))
            outcome = ProcedureOutcome.FAILED
            message = f"ERROR: step {failed.number} {failed.action}: {failed.detail}{_unmet(failed)}"
        recorded = record_error = None
        if self.platform.live:
            try:
                recorded = self._record(procedure, given, execution).id
                message += f" Recorded as {recorded}."
            except RudraError as exc:
                record_error = f"{exc.report.summary} {exc.report.reason}"
                message += f" NOT RECORDED in the verification history: {record_error}"
        return self._finish(procedure, outcome, message, given, steps=steps, plan=plan,
                            permissions=permissions, execution=execution, recorded=recorded,
                            record_error=record_error)

    # ------------------------------------------------------------ the parts

    @staticmethod
    def _values(procedure: StoredProcedure, supplied) -> tuple[dict[str, str], tuple[tuple[str, str], ...]]:
        known = {parameter_key(name): name for name in procedure.parameters}
        values: dict[str, str] = {}
        for name, value in supplied:
            key = parameter_key(name)
            if key not in known:
                raise InvalidInputError.of(
                    f"{procedure.id} has no parameter {name!r}.",
                    "Its parameters: " + (", ".join(procedure.parameters) or "none") + ".",
                    stage="orchestration.procedures", data_changed=False, retry_safe=True,
                    next_options=(f"python -m app procedure {procedure.id}",),
                )
            if key in values:
                raise InvalidInputError.of(f"The parameter {known[key]!r} is given twice.", "Give each once.",
                                           stage="orchestration.procedures", data_changed=False, retry_safe=True)
            problem = value_problem(value)
            if problem:
                raise InvalidInputError.of(f"The value of {known[key]!r} {problem}.",
                                           "A value is one line of text without < or >.",
                                           stage="orchestration.procedures", data_changed=False, retry_safe=True)
            values[key] = value
        return values, tuple((known[k], v) for k, v in values.items())

    def _map(self, steps: tuple[str, ...], values: dict[str, str] | None) -> tuple[StepMapping, ...]:
        stamp = self._clock().strftime("%Y%m%dT%H%M%SZ")
        mapped = []
        for number, documented in enumerate(steps, 1):
            text = documented if values is None else substitute(documented, values)
            interpretation = interpret(text)
            intents = interpretation.intents
            action, params, problem = None, (), ""
            if interpretation.status is not InterpretationStatus.INTERPRETED:
                problem = f"is {interpretation.status.value} to the interpreter: {interpretation.message}"
            elif len(intents) != 1:
                problem = f"is {len(intents)} requests, not exactly one action"
            elif intents[0].intent_type in UNAVAILABLE:
                problem = f"names {intents[0].intent_type}, which is not available: {UNAVAILABLE[intents[0].intent_type]}"
            elif intents[0].action is None:
                problem = "holds no computer action"
            else:
                action, request = to_request(intents[0], self.screenshots, stamp)
                params = tuple(request.items())
                if values is not None:
                    # A step the catalogue cannot plan (e.g. "Open the File menu." read as a
                    # file named "menu") is the document's problem, reported per step.
                    try:
                        ActionEngine(self.platform).plan([(action, request)])
                    except InvalidInputError as exc:
                        problem = f"cannot be planned as {action}: {exc.report.summary} {exc.report.reason}"
            mapped.append(StepMapping(number, documented, text, action, params, problem))
        return tuple(mapped)

    def _record(self, procedure: StoredProcedure, given, execution: ExecutionReport):
        statuses = [s.status for s in execution.steps]
        if all(s is StepStatus.VERIFIED for s in statuses):
            status = VerificationStatus.VERIFIED
        elif any(s not in (StepStatus.VERIFIED, StepStatus.INCONCLUSIVE) for s in statuses):
            status = VerificationStatus.FAILED
        else:
            status = VerificationStatus.INCONCLUSIVE
        expected = "; ".join(
            f"step {s.number} {s.action}({', '.join(f'{k}={v}' for k, v in s.parameters)}): {s.expected}"
            for s in execution.plan.steps)
        observed = "; ".join(
            [f"environment {self.platform.name} on {host.platform()}",
             "parameters " + (", ".join(f"{k}={v}" for k, v in given) or "none")]
            + [f"step {s.number} {s.status.value}: {s.observed}{_unmet(s)}" for s in execution.steps])
        return self.record(procedure.id, status, expected=expected, observed=observed)

    def _finish(self, procedure, outcome, message, given, *, missing=(), steps=(), plan=None, permissions=(),
                execution=None, recorded=None, record_error=None) -> ProcedureRun:
        run = ProcedureRun(procedure, outcome, message, self.platform.live, given, tuple(missing), tuple(steps),
                           plan, tuple(permissions), execution, recorded, record_error)
        _audit.audit(json.dumps({"event": "procedure", "procedure": procedure.id, "outcome": outcome.value,
                                 "live": self.platform.live, "recorded": recorded,
                                 "record_error": record_error}, sort_keys=True, ensure_ascii=False))
        return run


def _unmet(step) -> str:
    """The preconditions a blocked step did not meet, for the report and the record."""
    unmet = [c.text + (f" ({c.detail})" if c.detail else "") for c in step.conditions if not c.satisfied]
    return "" if not unmet else "; unmet: " + ", ".join(unmet)
