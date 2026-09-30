"""Constructing a documented workflow for a request (ADR 0049 P17-9; sections 112, 215).

A workflow is constructed step by step from the manual - never invented, never reordered,
never extended (sections 108, 111):

    MENU     a menu path's first element
    INPUT    a step that asks for an input ("Enter Name"): bound from the request when the
             request gives it, otherwise reported "Missing information:" (section 112)
    COMMAND  every other step

Nothing is executed: a menu cannot be opened or a dialog verified without UI inspection.
"""

from dataclasses import dataclass

from app.manuals import WorkflowMatch
from app.manuals.detectors import step_input

#: The request's project name answers these inputs.
NAME_INPUTS = ("name", "project name")

NOT_EXECUTED_NOTE = ("Nothing was executed: its menu steps need UI inspection, which RUDRA does not have yet "
                     "(ADR 0049 P17-1).")


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    number: int
    text: str
    role: str
    #: The input the step asks for, or None.
    input: str | None = None
    #: Its value, from the request, or None when missing.
    value: str | None = None


@dataclass(frozen=True, slots=True)
class ConstructedWorkflow:
    procedure_id: str
    name: str
    application: str
    document_id: str
    page: int | None
    labels: tuple[str, ...]
    steps: tuple[WorkflowStep, ...]
    #: Inputs the request does not give, in step order.
    missing: tuple[str, ...]


def construct(match: WorkflowMatch, project_name: str | None) -> ConstructedWorkflow:
    """The workflow, each step as the manual states it, with the request's values bound."""
    given = {} if project_name is None else {key: project_name for key in NAME_INPUTS}
    steps, missing = [], []
    for number, text in enumerate(match.procedure.steps, 1):
        asked = step_input(text)
        if asked is not None:
            value = given.get(asked)
            if value is None and asked not in missing:
                missing.append(asked)
            steps.append(WorkflowStep(number, text, "INPUT", asked, value))
        elif match.menu_path and number == 1:
            steps.append(WorkflowStep(number, text, "MENU"))
        else:
            steps.append(WorkflowStep(number, text, "COMMAND"))
    p = match.procedure
    return ConstructedWorkflow(p.id, p.name, match.application, match.document_id, match.page, p.labels,
                               tuple(steps), tuple(missing))


def describe(workflow: ConstructedWorkflow) -> str:
    """The workflow on one line: File -> New Project -> Select Template [template: MISSING] -> ..."""
    parts = []
    for step in workflow.steps:
        if step.role == "INPUT":
            parts.append(f"{step.text} [{step.input}: " + ("MISSING" if step.value is None else f"{step.value!r}") + "]")
        else:
            parts.append(step.text)
    return " → ".join(parts)


def where(workflow: ConstructedWorkflow) -> str:
    page = "" if workflow.page is None else f" p.{workflow.page}"
    return f"{workflow.procedure_id}, the {workflow.application} manual ({workflow.document_id}{page})"
