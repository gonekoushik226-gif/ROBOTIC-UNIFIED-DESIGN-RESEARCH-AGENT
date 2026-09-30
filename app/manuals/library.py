"""A declared manual's application knowledge, and its workflows for a request (ADR 0049
P17-4 ... P17-11). Reads only.

Section 214's seven kinds, for one declared manual:

    menus         the first element of each menu path          (derived)
    commands      a menu path's later elements that are not inputs (derived)
    workflows     the manual's documented procedures: menu paths and Step N blocks
    shortcuts     the manual stage's SHORTCUT items
    parameters    the inputs the workflows' steps ask for       (derived, P17-7)
    constraints   the manual stage's CONSTRAINT items
    file formats  the manual stage's FILE_FORMAT items

Only declared manuals are ever used for a request (P17-10).
"""

import re
from dataclasses import dataclass

from app.manuals.declarations import Declaration, declaration_of, declarations, same_application
from app.manuals.detectors import step_input
from app.manuals.stage import METHOD_PREFIX
from app.models.entities import Document, KnowledgeObject
from app.procedures import LookupStatus, ProcedureMemory, StoredProcedure
from app.provenance import ProvenanceScope, ProvenanceStatus
from app.provenance.scope import lifecycle_excluded
from app.storage import queries
from app.storage.repository import Repository

#: A step that creates a project (P17-9): "New Project", "New Project...", "Create Project",
#: "Create a new project".
_PROJECT = re.compile(r"(?:new|create)(?:\s+a)?(?:\s+new)?\s+project(?:\s*(?:\.\.\.|…))?", re.IGNORECASE)
MENU_PATH_NAME = "Menu path stated on page"
SHORTCUT_NAME = "Keyboard shortcut"


@dataclass(frozen=True, slots=True)
class ManualItem:
    """One item the manual stage stored, at its first occurrence."""

    kind: str
    knowledge_id: str
    name: str
    statement: str
    page: int | None
    occurrence_id: str
    #: The page's text for it, verbatim (flattened): a shortcut's or format's sentence.
    quote: str
    procedure_id: str | None = None


@dataclass(frozen=True, slots=True)
class Manual:
    declaration: Declaration
    document: str
    stage_ran: bool
    menus: tuple[str, ...]
    commands: tuple[str, ...]
    workflows: tuple[StoredProcedure, ...]
    shortcuts: tuple[ManualItem, ...]
    parameters: tuple[str, ...]
    constraints: tuple[ManualItem, ...]
    file_formats: tuple[ManualItem, ...]


@dataclass(frozen=True, slots=True)
class WorkflowMatch:
    """A documented workflow that answers a request, with where it is documented."""

    procedure: StoredProcedure
    application: str
    document_id: str
    page: int | None
    menu_path: bool


def is_menu_path(procedure: StoredProcedure) -> bool:
    return procedure.name.startswith(MENU_PATH_NAME)


def workflow_inputs(procedure: StoredProcedure) -> tuple[str, ...]:
    """The inputs a workflow's steps ask for, and its `<name>` placeholders (P17-7)."""
    found = [name for step in procedure.steps if (name := step_input(step)) is not None]
    return tuple(dict.fromkeys([*found, *(p.casefold() for p in procedure.parameters)]))


class ManualLibrary:
    def __init__(self, repository: Repository, scope: ProvenanceScope = ProvenanceScope.MY_BOOKS) -> None:
        self.repository = repository
        self.memory = ProcedureMemory(repository, scope)

    def declared(self) -> tuple[Declaration, ...]:
        self.memory.require_schema()
        return declarations(self.repository)

    def manual(self, document_id: str) -> Manual | None:
        """The seven kinds for one declared manual, or None when it is not declared."""
        self.memory.require_schema()
        declaration = declaration_of(self.repository, document_id)
        if declaration is None:
            return None
        document = self.repository.get(Document, document_id)
        items = self._items(document_id)
        workflows = tuple(p for p in self._procedures(document_id) if not p.name.startswith(SHORTCUT_NAME))
        menus, commands, inputs = [], [], []
        for workflow in workflows:
            asked = workflow_inputs(workflow)
            inputs.extend(asked)
            if is_menu_path(workflow):
                menus.append(workflow.steps[0])
                commands.extend(s for s in workflow.steps[1:] if step_input(s) is None)
        return Manual(
            declaration=declaration,
            document=document.original_filename if document is not None else document_id,
            stage_ran=bool(items),
            menus=tuple(dict.fromkeys(menus)), commands=tuple(dict.fromkeys(commands)),
            workflows=workflows,
            shortcuts=tuple(i for i in items if i.kind == "SHORTCUT"),
            parameters=tuple(dict.fromkeys(inputs)),
            constraints=tuple(i for i in items if i.kind == "CONSTRAINT"),
            file_formats=tuple(i for i in items if i.kind == "FILE_FORMAT"),
        )

    def project_workflows(self, names: tuple[str, ...] = ()) -> tuple[WorkflowMatch, ...]:
        """The declared manuals' workflows with a create-project step; only the named
        application's manual when `names` is given (P17-9, P17-10)."""
        self.memory.require_schema()
        matches = []
        for declaration in declarations(self.repository):
            if names and not any(same_application(declaration.application, n) for n in names):
                continue
            for procedure in self._procedures(declaration.document_id):
                if procedure.source.status is not ProvenanceStatus.AVAILABLE:
                    continue
                if not any(_PROJECT.fullmatch(step.rstrip(". ")) for step in procedure.steps):
                    continue
                citation = next((c for c in procedure.source.citations
                                 if c.evidence.document_id == declaration.document_id), None)
                matches.append(WorkflowMatch(
                    procedure, declaration.application, declaration.document_id,
                    None if citation is None else citation.evidence.page_number, is_menu_path(procedure)))
        return tuple(matches)

    # ------------------------------------------------------------ the parts

    def _procedures(self, document_id: str) -> tuple[StoredProcedure, ...]:
        found = []
        for row in queries.procedures_in_document(self.repository.connection, document_id):
            lookup = self.memory.find(row.id)
            if lookup.status is LookupStatus.FOUND:
                found.append(lookup.procedure)
        return tuple(found)

    def _items(self, document_id: str) -> tuple[ManualItem, ...]:
        procedures = {row.knowledge_id: row.id
                      for row in queries.procedures_in_document(self.repository.connection, document_id)}
        items: dict[str, ManualItem] = {}
        for occurrence in queries.occurrences_by_method(self.repository.connection, document_id, METHOD_PREFIX):
            if occurrence.knowledge_id in items:
                continue
            knowledge = self.repository.get(KnowledgeObject, occurrence.knowledge_id)
            if knowledge is None or lifecycle_excluded(knowledge.lifecycle_status):
                continue
            kind = occurrence.extraction_method.removeprefix(METHOD_PREFIX).split("@", 1)[0]
            items[knowledge.id] = ManualItem(kind, knowledge.id, knowledge.canonical_name, knowledge.statement,
                                             occurrence.page_number, occurrence.id, occurrence.original_text,
                                             procedures.get(knowledge.id))
        return tuple(items.values())
