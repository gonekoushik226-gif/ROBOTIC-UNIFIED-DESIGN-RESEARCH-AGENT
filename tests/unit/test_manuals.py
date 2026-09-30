"""Phase 17: application documentation (ADR 0049).

What every test holds Phase 17 to: only a document the user declared a manual is read as
one; the seven kinds are found by narrow rules, and a miss is preferred to an invention
(*x → 0* is no menu); every item keeps its page, span and verbatim text; a workflow is
constructed exactly as the manual states it, with the request's values bound and every
other input reported *"Missing information"*; only declared manuals answer a request;
nothing is executed; Phase 5's own extraction is unchanged.

The generated PDFs write arrows as `->`: the test font cannot carry `→`, whose extraction
would be mojibake; `→` is tested on text, where the detectors see it as a real manual's.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.core.errors import InvalidInputError
from app.manuals import ManualLibrary, ManualStage, declare, declarations
from app.manuals.detectors import detect, menu_paths, sentence_items, step_input
from app.orchestration import Outcome, Pipeline
from app.orchestration.workflows import construct, describe
from app.procedures import ProcedureMemory
from app.procedures.steps import menu_elements, normalize_combo, recovered_steps
from app.provenance import ProvenanceService
from app.storage import Repository, connect
from app.actions import SimulatedPlatform
from app.ui.cli.main import main
from tests.unit.pdf_fixtures import make_pdf

MANUAL = (
    "Circuit Studio User Guide\n"
    "Creating a project\n"
    "To create a project, use the File menu:\n"
    "File\n"
    "-> New Project\n"
    "-> Select Template\n"
    "-> Enter Name\n"
    "-> Create\n"
    "Press Ctrl+N to open the New Project dialog directly.\n"
    "Project names must not contain spaces.\n"
    "Projects are saved as .cstudio files."
)
SECOND = (
    "Saving\n"
    "Edit -> Preferences -> Autosave\n"
    "Press ctrl + shift + s to save a copy.\n"
    "Project names must not contain spaces."
)
TEXTBOOK = (
    "Setting up an experiment\n"
    "Step 1: Choose New Project.\n"
    "Step 2: Enter Name.\n"
    "As x -> 0 the ratio tends to one."
)
WORKFLOW = ("File", "New Project", "Select Template", "Enter Name", "Create")


@pytest.fixture(scope="module")
def library_project(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("manuals")
    root = base / "project"
    manual = base / "circuit-studio.pdf"
    manual.write_bytes(make_pdf([MANUAL, SECOND]))
    assert main(["extract", str(manual), "--project-root", str(root), "--manual", "Circuit Studio"]) == 0
    textbook = base / "textbook.pdf"
    textbook.write_bytes(make_pdf([TEXTBOOK]))
    assert main(["extract", str(textbook), "--project-root", str(root)]) == 0
    return root


@pytest.fixture
def repository(library_project, tmp_path):
    root = tmp_path / "project"
    shutil.copytree(library_project, root)
    connection = connect(root / "data" / "database" / "knowledge.db")
    yield Repository(connection)
    connection.close()


def _manual_id(repository) -> str:
    (declaration,) = declarations(repository)
    return declaration.document_id


# ------------------------------------------------------------ detectors (P17-4 ... P17-7)


def test_section_215s_layout_is_one_menu_path_with_its_exact_span():
    text = "Intro line.\nFile\n→ New Project\n→ Select Template\n→ Enter Name\n→ Create\nAfter."
    (path,) = menu_paths(3, text)
    assert path.steps == WORKFLOW and path.statement == " / ".join(WORKFLOW)
    assert text[path.start:path.end] == "File\n→ New Project\n→ Select Template\n→ Enter Name\n→ Create"
    assert path.text == "File → New Project → Select Template → Enter Name → Create"
    assert path.name == "Menu path stated on page 3"


@pytest.mark.parametrize("line", ["File → New Project", "File -> New Project -> Create.", "Tools → Options..."])
def test_a_whole_line_path_is_a_menu_path(line):
    (path,) = menu_paths(1, line)
    assert len(path.steps) >= 2


@pytest.mark.parametrize("text", ["As x → 0 the ratio tends to one.", "x → 0", "the file → the folder",
                                  "Click File → New Project to begin.", "File\nNew Project", "→ Create"])
def test_arrows_that_are_not_a_menu_path_are_not_one(text):
    assert menu_paths(1, text) == ()


def test_shortcuts_are_normalised_and_keep_their_sentence():
    (item,) = [f for f in detect(2, "Other text.\nPress ctrl + shift + s to save a copy.") if f.kind == "SHORTCUT"]
    assert item.statement == "Press Ctrl+Shift+S" and item.text == "Press ctrl + shift + s to save a copy."
    assert normalize_combo("Control+F5") == "Ctrl+F5" and normalize_combo("alt+esc") == "Alt+Esc"


@pytest.mark.parametrize("sentence", ["Press Ctrl alone.", "Shift the value by one.", "Use CtrlN."])
def test_text_without_a_key_combination_is_no_shortcut(sentence):
    assert [f for f in detect(1, sentence) if f.kind == "SHORTCUT"] == []


def test_constraints_and_file_formats():
    found = sentence_items(1, "Names must not contain spaces. Files are saved as .cstudio files. "
                              "See e.g. the index. A value of 3.5 is typical.", ())
    kinds = [(f.kind, f.name) for f in found]
    assert ("CONSTRAINT", "Constraint stated on page 1") in kinds
    assert ("FILE_FORMAT", "File format .cstudio") in kinds
    assert len(kinds) == 2  # "e.g." and "3.5" are no extensions


def test_a_sentence_never_crosses_a_menu_path():
    items = detect(1, MANUAL.replace("->", "→"))
    shortcut = next(f for f in items if f.kind == "SHORTCUT")
    assert shortcut.text == "Press Ctrl+N to open the New Project dialog directly."


@pytest.mark.parametrize(("step", "asked"), [("Select Template", "template"), ("Enter Name", "name"),
                                             ("Enter the project name.", "project name"),
                                             ("New Project", None), ("Create", None), ("File", None)])
def test_inputs_are_what_a_step_asks_for(step, asked):
    assert step_input(step) == asked


def test_menu_path_and_shortcut_steps_are_read_back_from_their_quotes():
    assert menu_elements("File → New Project → Create") == ("File", "New Project", "Create")
    assert recovered_steps("File / New Project", ("File -> New Project",)) == (("File", "New Project"), "")
    assert recovered_steps("Press Ctrl+N", ("Press ctrl+n to begin.",))[1] == ""
    assert "disagree" in recovered_steps("File / Open", ("File -> New Project",))[1]
    assert "does not split" in recovered_steps("Press Ctrl+N", ("Press Ctrl+O to begin.",))[1]


# ------------------------------------------------------------ declaration (P17-2)


def test_the_declaration_is_recorded_once_and_never_rewritten(repository):
    document_id = _manual_id(repository)
    again, new = declare(repository, document_id, "circuit  studio")
    assert not new and again.application == "Circuit Studio"
    with pytest.raises(InvalidInputError):
        declare(repository, document_id, "Word")
    with pytest.raises(InvalidInputError):
        declare(repository, "DOC-00000099", "Word")
    with pytest.raises(InvalidInputError):
        declare(repository, document_id, "   ")


def test_only_the_declared_document_is_a_manual(repository):
    assert [d.application for d in declarations(repository)] == ["Circuit Studio"]
    library = ManualLibrary(repository)
    textbook = next(d for d in ("DOC-00000001", "DOC-00000002") if d != _manual_id(repository))
    assert library.manual(textbook) is None


# ------------------------------------------------------------ the stage (P17-3)


def test_the_stage_runs_once(repository):
    report = ManualStage(repository).run(_manual_id(repository))
    assert not report.ran and "already run" in report.message


def test_the_stage_refuses_an_undeclared_document(repository):
    textbook = next(d for d in ("DOC-00000001", "DOC-00000002") if d != _manual_id(repository))
    with pytest.raises(InvalidInputError):
        ManualStage(repository).run(textbook)


def test_the_seven_kinds(repository):
    manual = ManualLibrary(repository).manual(_manual_id(repository))
    assert manual.stage_ran and manual.declaration.application == "Circuit Studio"
    assert manual.menus == ("File", "Edit")
    assert manual.commands == ("New Project", "Create", "Preferences", "Autosave")
    assert [w.steps for w in manual.workflows] == [WORKFLOW, ("Edit", "Preferences", "Autosave")]
    assert [s.statement for s in manual.shortcuts] == ["Press Ctrl+N", "Press Ctrl+Shift+S"]
    assert manual.parameters == ("template", "name")
    # The same constraint on two pages is one object with two occurrences.
    (constraint,) = manual.constraints
    assert constraint.statement == "Project names must not contain spaces." and constraint.page == 1
    (file_format,) = manual.file_formats
    assert file_format.name == "File format .cstudio"


def test_every_item_is_cited_and_its_quote_verified(repository):
    manual = ManualLibrary(repository).manual(_manual_id(repository))
    service = ProvenanceService(repository)
    identifiers = [w.id for w in manual.workflows] + [i.knowledge_id for i in
                                                      (*manual.shortcuts, *manual.constraints, *manual.file_formats)]
    for identifier in identifiers:
        provenance = service.of_item(identifier)
        assert provenance.status.value == "AVAILABLE", identifier
        assert {c.quote.status.value for c in provenance.citations} == {"VERIFIED"}, identifier


def test_the_workflow_is_a_documented_procedure_shown_but_not_executable(repository):
    memory = ProcedureMemory(repository)
    workflow = ManualLibrary(repository).manual(_manual_id(repository)).workflows[0]
    stored = memory.find(workflow.id).procedure
    assert stored.labels == ("DOCUMENTED_PROCEDURE",) and stored.steps == WORKFLOW and stored.executable


def test_ordinary_extraction_is_unchanged(repository):
    textbook = next(d for d in ("DOC-00000001", "DOC-00000002") if d != _manual_id(repository))
    from app.storage import queries

    assert queries.occurrences_by_method(repository.connection, textbook, "deterministic/manual.") == ()


# ------------------------------------------------------------ requests (P17-9, P17-10)


def test_the_documented_workflow_is_constructed_not_invented(repository):
    (match,) = ManualLibrary(repository).project_workflows()
    workflow = construct(match, "amplifier")
    assert [s.text for s in workflow.steps] == list(WORKFLOW)
    assert [s.role for s in workflow.steps] == ["MENU", "COMMAND", "INPUT", "INPUT", "COMMAND"]
    assert workflow.steps[3].value == "amplifier" and workflow.missing == ("template",)
    assert describe(workflow) == ("File → New Project → Select Template [template: MISSING] → "
                                  "Enter Name [name: 'amplifier'] → Create")


def test_a_textbook_step_block_is_never_used(repository):
    matches = ManualLibrary(repository).project_workflows()
    assert [m.procedure.steps for m in matches] == [WORKFLOW]  # not the textbook's Step 1 / Step 2


@pytest.mark.parametrize(("names", "count"), [((), 1), (("Circuit Studio",), 1), (("circuit studio",), 1),
                                              (("Word", "microsoft word"), 0)])
def test_the_named_applications_manual_only(repository, names, count):
    assert len(ManualLibrary(repository).project_workflows(names)) == count


def _pipeline(repository, tmp_path) -> Pipeline:
    library = ManualLibrary(repository)
    return Pipeline(SimulatedPlatform(), screenshots=tmp_path, workflows=library.project_workflows)


def test_do_constructs_the_workflow_and_executes_nothing(repository, tmp_path):
    platform = SimulatedPlatform()
    report = Pipeline(platform, screenshots=tmp_path,
                      workflows=ManualLibrary(repository).project_workflows).run("Create a project called amplifier.")
    assert report.outcome is Outcome.NOT_EXECUTED and report.execution is None and platform.log == []
    (workflow,) = report.workflows
    assert [s.text for s in workflow.steps] == list(WORKFLOW)
    assert "Missing information:\ntemplate" in report.message and "Nothing was executed" in report.message
    assert [s.name for s in report.stages] == ["INTERPRET", "PLAN", "REPORT"]
    assert report.stages[1].outcome == "DOCUMENTED_WORKFLOW"


def test_do_with_another_application_or_no_manual_invents_nothing(repository, tmp_path):
    report = _pipeline(repository, tmp_path).run("Create a project called amplifier in Word.")
    assert report.outcome is Outcome.NOT_EXECUTED and report.workflows == ()
    assert "RUDRA does not invent one" in report.message
    empty = Pipeline(SimulatedPlatform(), screenshots=tmp_path, workflows=lambda names: ()).run(
        "Create a project called amplifier.")
    assert empty.workflows == () and "no documented workflow" in empty.message


def test_several_documented_workflows_are_listed_and_none_is_chosen(repository, tmp_path):
    match = ManualLibrary(repository).project_workflows()[0]
    report = Pipeline(SimulatedPlatform(), screenshots=tmp_path, workflows=lambda names: (match, match)).run(
        "Create a project called amplifier.")
    assert report.outcome is Outcome.NOT_EXECUTED and len(report.workflows) == 2
    assert report.stages[1].outcome == "AMBIGUOUS" and "None was chosen" in report.message
