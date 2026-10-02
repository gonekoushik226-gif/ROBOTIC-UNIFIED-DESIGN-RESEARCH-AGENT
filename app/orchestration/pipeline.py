"""The request pipeline: text to a verified report (ADR 0047 P15-3 ... P15-7; sections 17, 211).

    INTERPRET   the Phase 13 interpreter; anything but INTERPRETED executes nothing
    PLAN        each action intent becomes exactly its catalogue step (section 108)
    VALIDATE    the catalogue's parameter checks, before anything runs
    PERMISSION  the permission engine; a plan with a refused step runs nothing
    EXECUTE     the action engine through the platform port
    VERIFY      each step's postcondition, observed on the machine (section 18)
    REPORT      every stage, recorded, and one audit line for the request (section 116)

Knowledge, reasoning, calculation and provenance intents are **not executed** here in
Phase 15: their command is reported for the user to run (P15-4). From Phase 17, a request to
create a project is answered with the documented workflow from the user's declared manuals,
constructed and never executed (ADR 0049 P17-9), when the pipeline is given the lookup. Actions with no Phase 15
implementation - deletion, web search, projects, images - make the request not executable,
and the report names the phase that provides them.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from app.actions import ActionEngine, ExecutionPlan, ExecutionReport, Platform, StepStatus
from app.applications import find as find_application
from app.core.logging_setup import get_logger
from app.nlu import Interpretation, InterpretationStatus, StructuredIntent, command_text, interpret
from app.orchestration.workflows import NOT_EXECUTED_NOTE, ConstructedWorkflow, construct, describe, where
from app.security import Decision, PermissionDecision, all_permitted, decide

_audit = get_logger("app.orchestration")
_audit.addHandler(logging.NullHandler())  # without configured logging, nowhere - never stderr


class Outcome(StrEnum):
    #: Every step ran and was VERIFIED or INCONCLUSIVE (both reported).
    DONE = "DONE"
    #: A step failed verification, was blocked by a precondition, or was not attempted.
    FAILED = "FAILED"
    #: The permission engine refused a step; nothing ran.
    REFUSED = "REFUSED"
    #: The request needs more information, or holds no action; nothing ran.
    NOT_EXECUTED = "NOT_EXECUTED"


#: Intents the action path does not carry out, and where each is answered instead.
UNAVAILABLE = {
    "DELETE_FILE": "deletion is HIGH risk and not enabled",
    "WEB_SEARCH": ('an Internet search needs your authorization of one website for the request: '
                   'python -m app research "QUESTION" --site URL'),
    "CREATE_PROJECT": ("a project is created only by a documented workflow from a manual you declared; "
                       "none could be looked up"),
    "IMAGE_REQUEST": 'a diagram is drawn from stored knowledge: python -m app diagram "REQUEST"',
    "UNRESOLVED_REFERENCE": "the object must be named first",
}


@dataclass(frozen=True, slots=True)
class Stage:
    name: str
    outcome: str
    detail: str


@dataclass(frozen=True, slots=True)
class PipelineReport:
    """Section 116's trace for one request: request, intent, command, plan, permission,
    action, observed state, verification, result."""

    text: str
    outcome: Outcome
    message: str
    interpretation: Interpretation
    stages: tuple[Stage, ...]
    plan: ExecutionPlan | None
    permissions: tuple[PermissionDecision, ...]
    execution: ExecutionReport | None
    #: Commands of non-action intents, reported for the user to run (not executed here).
    reported_commands: tuple[str, ...]
    live: bool
    #: Documented workflows constructed for the request (Phase 17, P17-9); never executed.
    workflows: tuple[ConstructedWorkflow, ...] = ()


def to_request(intent: StructuredIntent, screenshots: Path, stamp: str) -> tuple[str, dict[str, str]]:
    """The catalogue request for an action intent (P15-5): fixed, explicit translations."""
    action = intent.action
    params = dict(action.parameters)
    name = action.action
    if name == "SAVE_FILE" and "name" in params:
        params = {"path": params["name"]}
    elif name == "PRESS_KEY":
        params = {"key": params.get("keys", "")}
    elif name == "TAKE_SCREENSHOT":
        params = {"path": str(screenshots / f"screenshot-{stamp}.png")}
    return name, params


class Pipeline:
    """Interpret, plan, permit, execute, verify and report one request."""

    def __init__(self, platform: Platform, *, screenshots: Path,
                 clock: Callable[[], datetime] | None = None,
                 workflows: Callable[[tuple[str, ...]], tuple] | None = None) -> None:
        self.platform = platform
        self.screenshots = screenshots
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        #: The declared manuals' create-project workflows, for the named application
        #: (`ManualLibrary.project_workflows`); None when no lookup is available.
        self.workflows = workflows

    def run(self, text: str, *, confirmed: bool = False) -> PipelineReport:
        interpretation = interpret(text)
        stages = [Stage("INTERPRET", interpretation.status.value, interpretation.message)]
        commands = tuple(command_text(i.command) for i in interpretation.intents if i.command)
        if interpretation.status is not InterpretationStatus.INTERPRETED:
            return self._finish(text, interpretation, stages, Outcome.NOT_EXECUTED,
                                "nothing was executed: the request needs more information (see the interpretation)",
                                commands=commands)
        projects = [i for i in interpretation.intents if i.intent_type == "CREATE_PROJECT"]
        if projects and self.workflows is not None:
            return self._documented(text, interpretation, stages, projects[0], commands)
        actions = [i for i in interpretation.intents if i.action is not None or i.intent_type in UNAVAILABLE]
        missing = [f"{i.intent_type}: {UNAVAILABLE[i.intent_type]}" for i in actions if i.intent_type in UNAVAILABLE]
        if missing:
            stages.append(Stage("PLAN", "NOT_AVAILABLE", "; ".join(missing)))
            return self._finish(text, interpretation, stages, Outcome.NOT_EXECUTED,
                                "nothing was executed: " + "; ".join(missing), commands=commands)
        if not actions:
            stages.append(Stage("PLAN", "NO_ACTION", "the request holds no computer action"))
            return self._finish(text, interpretation, stages, Outcome.NOT_EXECUTED,
                                "nothing was executed: the request holds no computer action; its command is "
                                "reported for you to run" if commands else "nothing was executed",
                                commands=commands)
        stamp = self._clock().strftime("%Y%m%dT%H%M%SZ")
        requests = [to_request(i, self.screenshots, stamp) for i in actions]
        engine = ActionEngine(self.platform)
        plan = engine.plan(requests)  # validation: raises InvalidInputError before anything runs
        stages.append(Stage("PLAN", "PLANNED", f"{len(plan.steps)} step(s): "
                            + ", ".join(f"{s.action}({', '.join(f'{k}={v}' for k, v in s.parameters)})" for s in plan.steps)))
        stages.append(Stage("VALIDATE", "VALID", "every parameter passed the catalogue's checks"))
        permissions = decide(plan, confirmed=confirmed)
        stages.append(Stage("PERMISSION", "PERMITTED" if all_permitted(permissions) else "REFUSED",
                            "; ".join(f"step {d.step} {d.action}: {d.decision.value} - {d.reason}" for d in permissions)))
        if not all_permitted(permissions):
            refused = next(d for d in permissions if d.decision is Decision.REFUSED)
            return self._finish(text, interpretation, stages, Outcome.REFUSED,
                                f"nothing was executed: step {refused.step} {refused.action} was refused - {refused.reason}",
                                plan=plan, permissions=permissions, commands=commands)
        execution = engine.run(plan)
        stages.append(Stage("EXECUTE", "RAN", "; ".join(
            f"step {s.number} {s.action}: " + (", ".join(f"{a.method} -> {a.returned}" for a in s.attempts) or "not executed")
            for s in execution.steps)))
        stages.append(Stage("VERIFY", execution.status.value, "; ".join(
            f"step {s.number} {s.action}: {s.status.value} - {s.observed}" for s in execution.steps)))
        good = all(s.status in (StepStatus.VERIFIED, StepStatus.INCONCLUSIVE) for s in execution.steps)
        if good:
            outcome = Outcome.DONE
            message = execution.message
        else:
            outcome = Outcome.FAILED
            failed = next(s for s in execution.steps if s.status not in (StepStatus.VERIFIED, StepStatus.INCONCLUSIVE))
            message = f"ERROR: {failed.detail}"
        return self._finish(text, interpretation, stages, outcome, message, plan=plan,
                            permissions=permissions, execution=execution, commands=commands)

    def _documented(self, text, interpretation, stages, intent: StructuredIntent, commands) -> PipelineReport:
        """Section 215: the documented workflow for a project, constructed from a declared
        manual; nothing is executed and nothing is invented (ADR 0049 P17-9)."""
        given = dict(intent.parameters)
        application = given.get("application")
        wanted = "" if application is None else f" for {application}"
        matches = self.workflows(() if application is None else _names(application))
        if not matches:
            stages.append(Stage("PLAN", "NO_DOCUMENTED_WORKFLOW", f"no declared manual{wanted} documents creating a project"))
            return self._finish(text, interpretation, stages, Outcome.NOT_EXECUTED,
                                f"nothing was executed: no documented workflow for creating a project{wanted} was "
                                "found in your declared manuals; RUDRA does not invent one",
                                commands=commands)
        built = tuple(construct(match, given.get("name")) for match in matches)
        if len(built) > 1:
            listing = "; ".join(f"{where(w)}: {describe(w)}" for w in built)
            stages.append(Stage("PLAN", "AMBIGUOUS", listing))
            return self._finish(text, interpretation, stages, Outcome.NOT_EXECUTED,
                                f"nothing was executed: {len(built)} documented workflows create a project - {listing}. "
                                f"None was chosen: name the application (\"... in {built[0].application}\").",
                                commands=commands, workflows=built)
        (workflow,) = built
        stages.append(Stage("PLAN", "DOCUMENTED_WORKFLOW", f"{where(workflow)}: {describe(workflow)}"))
        message = f"the documented workflow was constructed from {where(workflow)}: {describe(workflow)}."
        if workflow.missing:
            message += "\nMissing information:\n" + "\n".join(workflow.missing)
        message += "\n" + NOT_EXECUTED_NOTE
        return self._finish(text, interpretation, stages, Outcome.NOT_EXECUTED, message, commands=commands,
                            workflows=built)

    def _finish(self, text, interpretation, stages, outcome, message, *, plan=None, permissions=(),
                execution=None, commands=(), workflows=()) -> PipelineReport:
        stages.append(Stage("REPORT", outcome.value, message))
        report = PipelineReport(text, outcome, message, interpretation, tuple(stages), plan, tuple(permissions),
                                execution, tuple(commands), self.platform.live, tuple(workflows))
        _audit.audit(json.dumps({"event": "request", "text": text, "outcome": outcome.value, "live": self.platform.live,
                                 "stages": [[s.name, s.outcome] for s in stages]}, ensure_ascii=False, sort_keys=True))
        return report


def _names(application: str) -> tuple[str, ...]:
    """The names the request's application goes by: as given, and the registry's name and
    aliases when it is a registered application."""
    registered = find_application(application)
    if registered is None:
        return (application,)
    return tuple(dict.fromkeys((application, registered.name, *registered.aliases)))
