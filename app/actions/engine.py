"""The action engine: requests to a plan, a plan to a verified report (ADR 0046 P14-5 ... P14-11).

`ActionEngine(platform).plan(requests)` validates each requested action's parameters
against the catalogue and builds an `ExecutionPlan` with section 103's fields - nothing
more than was asked (section 108; P6 sections 31-32). `run(plan)` carries out the steps
in order through the platform port; each step checks its preconditions, performs its
operation, inspects the state and verifies its postcondition. A step that does not
succeed stops the plan: later steps are `NOT_ATTEMPTED`, because they were planned on the
assumption that it would. Every step writes one line to the audit channel (P14-11).

The engine never decides whether a step is **allowed**: callers run a plan only after the
permission check (Phase 15). Against a platform that is not live, every report is a
**DRY RUN** and says that nothing was changed (P6 section 28).
"""

import json
import logging
from collections.abc import Callable, Iterable, Mapping

from app.actions.catalogue import CATALOGUE, Context, Outcome, shown, validate
from app.actions.ports import Platform
from app.actions.results import (
    ExecutionPlan,
    ExecutionReport,
    PlanStep,
    StepResult,
    StepStatus,
    overall,
)
from app.actions.safety import UnsafeValue
from app.applications import Application
from app.core.errors import InvalidInputError
from app.core.logging_setup import get_logger

DRY_RUN_NOTE = "DRY RUN - no changes have been made: the plan ran on a simulated computer (P6 section 28)."

_audit = get_logger("app.actions")
# Without configured logging (a library call, a test) the audit line goes nowhere, rather
# than to the interpreter's last-resort stderr handler.
_audit.addHandler(logging.NullHandler())


def refuse(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="actions.request", data_changed=False, retry_safe=True,
        next_options=("python -m app act OPEN_APPLICATION --param application=Notepad",
                      "python -m app act CREATE_FILE --param path=C:\\temp\\notes.txt --param content=hello"),
    )


class ActionEngine:
    """Plans and runs parameterised actions through one platform port."""

    def __init__(self, platform: Platform, *, clock: Callable[[], str] | None = None) -> None:
        self.platform = platform
        self._clock = clock or _counter()

    def plan(self, requests: Iterable[tuple[str, Mapping[str, str]]]) -> ExecutionPlan:
        """Validate every request and build the plan; raise `InvalidInputError` first."""
        steps = []
        for number, (name, given) in enumerate(requests, start=1):
            definition = CATALOGUE.get(str(name).strip().upper())
            if definition is None:
                raise refuse(f"{name!r} is not an action RUDRA has.",
                             "Actions: " + ", ".join(sorted(CATALOGUE)) + ".")
            try:
                values = validate(definition, dict(given))
            except UnsafeValue as exc:
                raise refuse(f"{definition.name}: {exc}.", "Nothing was planned or done.") from exc
            steps.append(PlanStep(
                number=number, action=definition.name, parameters=shown(values),
                risk_level=definition.risk, preconditions=definition.preconditions,
                expected=definition.expected, verification=definition.verification,
                resolved=_resolved(values),
            ))
        if not steps:
            raise refuse("There is nothing to plan.", "Name at least one action.")
        return ExecutionPlan(tuple(steps), self.platform.name, self.platform.live)

    def run(self, plan: ExecutionPlan) -> ExecutionReport:
        """Carry out a plan built by `plan`; each step's parameters are validated again."""
        results: list[StepResult] = []
        stopped = False
        for step in plan.steps:
            if stopped:
                results.append(_not_attempted(step))
                continue
            definition = CATALOGUE[step.action]
            values = validate(definition, dict(step.parameters))
            started = self._clock()
            try:
                outcome = definition.run(Context(self.platform), values)
            except OSError as exc:
                outcome = Outcome(StepStatus.FAILED, "the operation raised an error", str(exc),
                                  executed=True, failure_stage="EXECUTION", error=f"{type(exc).__name__}: {exc}")
            result = StepResult(
                number=step.number, action=step.action, parameters=step.parameters,
                risk_level=step.risk_level, status=outcome.status, conditions=tuple(outcome.conditions),
                executed=outcome.executed, attempts=tuple(outcome.attempts), expected=step.expected,
                observed=outcome.observed, detail=outcome.detail, failure_stage=outcome.failure_stage,
                error=outcome.error, started=started, finished=self._clock(),
            )
            results.append(result)
            _audit.audit(json.dumps({
                "event": "action", "platform": plan.platform, "live": plan.live, "step": step.number,
                "action": step.action, "parameters": dict(step.parameters), "risk": step.risk_level.value,
                "status": result.status.value, "observed": result.observed,
            }, ensure_ascii=False, sort_keys=True))
            stopped = result.status not in (StepStatus.VERIFIED, StepStatus.INCONCLUSIVE)
        status = overall(tuple(r.status for r in results))
        return ExecutionReport(
            plan=plan, steps=tuple(results), status=status, message=_message(status, results, plan.live),
            dry_run=not plan.live, notes=() if plan.live else (DRY_RUN_NOTE,),
        )


def _resolved(values: dict) -> tuple[tuple[str, str], ...]:
    app = next((v for v in values.values() if isinstance(v, Application)), None)
    if app is None:
        return ()
    methods = "; ".join(f"{m.kind.value} {m.value}" for m in app.launch)
    return (("application", app.name), ("kind", app.kind), ("launch methods, in order", methods),
            ("detection", f"process {', '.join(app.detection.process_images)}; title /{app.detection.title_pattern}/"))


def _not_attempted(step: PlanStep) -> StepResult:
    return StepResult(
        number=step.number, action=step.action, parameters=step.parameters, risk_level=step.risk_level,
        status=StepStatus.NOT_ATTEMPTED, conditions=(), executed=False, attempts=(), expected=step.expected,
        observed="not attempted", detail="an earlier step did not succeed, so this one was not attempted",
    )


def _message(status: StepStatus, results: list[StepResult], live: bool) -> str:
    prefix = "" if live else "DRY RUN: "
    if status is StepStatus.VERIFIED:
        return prefix + f"{len(results)} step(s) done and verified."
    first = next((r for r in results if r.status is status), results[-1])
    return prefix + f"step {first.number} {first.action} {status.value}: {first.detail}."


def _counter() -> Callable[[], str]:
    ticks = iter(range(1, 1_000_000))
    return lambda: f"step-clock {next(ticks)}"
