"""Phase 16: procedural memory and running a documented procedure (ADR 0048).

What every test holds Phase 16 to: the steps RUDRA runs are the document's own (checked
against the evidence quote); parameters are the user's, never invented ("Missing
information:"); each step is exactly one catalogue action; a documented procedure runs
live only when the user names it with --confirm, and only LOW steps; only a live run that
executed is recorded, in one transaction, and "VERIFIED" is never recorded for a run that
was not fully verified; VERIFIED_PROCEDURE is an additional label; DELETED and ARCHIVED
procedures and those without a source in scope are not run.

The procedures come from a generated three-page manual through the real Phase 4-5
`extract`, once per module; each test works on its own copy of that project. Nothing acts
on the live desktop (P15-11): runs use the simulated computer, one flagged live.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.actions import SimulatedPlatform
from app.core.errors import InvalidInputError
from app.models.entities import Procedure, Source
from app.models.enums import Authorization, LifecycleStatus, VerificationStatus
from app.orchestration import ProcedureOutcome, ProcedureRunner
from app.procedures import LookupStatus, ProcedureMemory
from app.procedures.steps import parameters, quoted_steps, recovered_steps, stored_steps, substitute, value_problem
from app.provenance import ProvenanceStatus
from app.security import Decision, decide_documented
from app.storage import Repository, connect
from app.ui.cli.main import main
from tests.unit.pdf_fixtures import make_pdf

FIXED = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

WORKFLOW = (
    "Preparing a work folder\n"
    "Use this procedure to prepare a folder for a new measurement.\n"
    "Step 1: Create a folder called <folder>.\n"
    "Step 2: Create a file called <folder>/notes.txt.\n"
    "Step 3: Copy <folder>/notes.txt to <folder>/notes-copy.txt.\n"
    "The copy keeps the original unchanged."
)
ARCHIVE = (
    "Archiving the notes\n"
    "Step 1: Create a folder called <archive>.\n"
    "Step 2: Move <folder>/notes.txt to <archive>/notes.txt."
)
MENUS = (
    "Creating a project\n"
    "Step 1: Open the File menu.\n"
    "Step 2: Choose New Project."
)
STEPS = (
    "Create a folder called <folder>.",
    "Create a file called <folder>/notes.txt.",
    "Copy <folder>/notes.txt to <folder>/notes-copy.txt.",
)


class _LiveLike(SimulatedPlatform):
    """A simulated computer flagged live, so the permission rules and recording apply."""

    live = True


@pytest.fixture(scope="module")
def manual_project(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("manual")
    pdf = base / "manual.pdf"
    pdf.write_bytes(make_pdf([WORKFLOW, ARCHIVE, MENUS]))
    root = base / "project"
    assert main(["extract", str(pdf), "--project-root", str(root)]) == 0
    return root


@pytest.fixture
def memory(manual_project, tmp_path):
    root = tmp_path / "project"
    shutil.copytree(manual_project, root)
    connection = connect(root / "data" / "database" / "knowledge.db")
    yield ProcedureMemory(Repository(connection))
    connection.close()


def _by_page(memory: ProcedureMemory, page: int):
    (found,) = [p for p in memory.all() if p.name == f"Procedure stated on page {page}"]
    return found


def _runner(memory, platform, tmp_path) -> ProcedureRunner:
    return ProcedureRunner(platform, record=memory.record, screenshots=tmp_path, clock=lambda: FIXED)


def _never(*args, **kwargs):
    raise AssertionError("nothing may be recorded")


# ------------------------------------------------------------ steps (P16-3)


def test_the_steps_are_the_documents_own():
    quote = ("Step 1: Create a folder called <folder>. Step 2: Create a file called <folder>/notes.txt. "
             "Step 3: Copy <folder>/notes.txt to <folder>/notes-copy.txt.")
    statement = " / ".join(STEPS)
    assert stored_steps(statement) == quoted_steps(quote) == STEPS
    assert recovered_steps(statement, (quote,)) == (STEPS, "")


@pytest.mark.parametrize(
    ("statement", "quote", "reason"),
    [
        # A step that itself contains " / " splits into more steps than the document has.
        ("Open a / b. / Close it.", "Step 1: Open a / b. Step 2: Close it.", "disagree"),
        ("Open a. / Close it.", "Open a. Step 2: Close it.", "does not split"),
        ("Open a. / Close it.", "Step 1: Open a.", "does not split"),
        ("Open a. / Close it.", "Step 1: Open b. Step 2: Close it.", "disagree"),
    ],
)
def test_steps_that_are_not_provably_the_documents_are_not_executable(statement, quote, reason):
    assert reason in recovered_steps(statement, (quote,))[1]


def test_without_a_quote_the_steps_cannot_be_checked():
    assert "no evidence quote" in recovered_steps("Open a. / Close it.", ())[1]


# -------------------------------------------------------- parameters (P16-4)


def test_parameters_in_order_of_first_appearance_compared_without_case():
    steps = ("Copy <Source File> to <target>.", "Open <source  file>.", "Open <TARGET>.")
    assert parameters(steps) == ("Source File", "target")
    assert substitute(steps[1], {"source file": "C:/a.txt"}) == "Open C:/a.txt."


@pytest.mark.parametrize(("value", "problem"), [("", "is empty"), ("  ", "is empty"), ("a\nb", "spans more than one line"),
                                                ("C:/<x>", "contains < or >"), ("C:/work", "")])
def test_parameter_values_are_checked(value, problem):
    assert value_problem(value) == problem


# ------------------------------------------------------------ the store (P16-2)


def test_a_stored_procedure_holds_section_56s_fields(memory):
    procedure = _by_page(memory, 1)
    assert procedure.id.startswith("PRC-") and procedure.labels == ("DOCUMENTED_PROCEDURE",)
    assert procedure.steps == STEPS and procedure.parameters == ("folder",)
    assert procedure.executable and procedure.reason == ""
    assert procedure.source.status is ProvenanceStatus.AVAILABLE
    (citation,) = procedure.source.citations
    assert citation.evidence.page_number == 1 and citation.quote.status.value == "VERIFIED"
    assert (procedure.execution_count, procedure.successful_executions, procedure.failed_executions) == (0, 0, 0)
    assert procedure.history == () and procedure.last_verified is None and procedure.note == "never executed live"


def test_every_stored_procedure_is_listed_and_found(memory):
    listed = memory.all()
    assert [p.name for p in listed] == [f"Procedure stated on page {n}" for n in (1, 2, 3)]
    assert memory.find(listed[1].id).procedure == listed[1]
    assert memory.find("PRC-00000099").status is LookupStatus.NOT_FOUND


@pytest.mark.parametrize("identifier", ["K-00000001", "procedure one", "PRC-", 7])
def test_only_a_procedure_identifier_is_accepted(memory, identifier):
    with pytest.raises(InvalidInputError):
        memory.find(identifier)


@pytest.mark.parametrize("status", [LifecycleStatus.DELETED, LifecycleStatus.ARCHIVED])
def test_deleted_and_archived_procedures_are_not_listed_or_shown(memory, status):
    procedure = _by_page(memory, 1)
    row = memory.repository.get(Procedure, procedure.id)
    memory.repository.update(replace(row, lifecycle_status=status))
    assert procedure.id not in [p.id for p in memory.all()]
    assert memory.find(procedure.id).status is LookupStatus.EXCLUDED


def test_a_procedure_without_a_source_in_scope_is_not_executable(memory, tmp_path):
    procedure = _by_page(memory, 1)
    (citation,) = procedure.source.citations
    source = memory.repository.get(Source, citation.source.id)
    memory.repository.update(replace(source, authorization=Authorization.NOT_AUTHORIZED))
    again = memory.find(procedure.id).procedure
    assert not again.executable and "source is not available" in again.reason
    run = _runner(memory, _LiveLike(), tmp_path).run(again, (("folder", str(tmp_path / "w")),), confirmed=True)
    assert run.outcome is ProcedureOutcome.NOT_EXECUTABLE and run.execution is None


# ----------------------------------------------------------- recording (P16-8)


def test_recording_counts_only_what_was_verified(memory):
    procedure = _by_page(memory, 1)
    memory.record(procedure.id, VerificationStatus.VERIFIED, expected="e1", observed="o1")
    first = memory.find(procedure.id).procedure
    assert (first.execution_count, first.successful_executions, first.failed_executions) == (1, 1, 0)
    assert first.last_verified == first.history[0].recorded
    assert first.labels == ("DOCUMENTED_PROCEDURE", "VERIFIED_PROCEDURE")
    memory.record(procedure.id, VerificationStatus.FAILED, expected="e2", observed="o2")
    memory.record(procedure.id, VerificationStatus.INCONCLUSIVE, expected="e3", observed="o3")
    after = memory.find(procedure.id).procedure
    assert (after.execution_count, after.successful_executions, after.failed_executions) == (3, 1, 1)
    assert after.last_verified == first.last_verified  # only a verified run sets it
    assert [h.status for h in after.history] == ["VERIFIED", "FAILED", "INCONCLUSIVE"]
    assert [h.expected for h in after.history] == ["e1", "e2", "e3"]
    assert after.documentation_status == "DOCUMENTED_PROCEDURE"  # the documented origin is kept
    assert "verified on 1 of 3" in after.note and "recorded environments only" in after.note


def test_pending_is_not_an_outcome(memory):
    with pytest.raises(InvalidInputError):
        memory.record(_by_page(memory, 1).id, VerificationStatus.PENDING, expected="e", observed="o")


class _BrokenAdd(Repository):
    """A repository whose insert fails after the counters' update has been made."""

    __slots__ = ()

    def add(self, entity):
        raise RuntimeError("the verification row could not be written")


def test_a_record_is_one_transaction(memory):
    procedure = _by_page(memory, 1)
    broken = ProcedureMemory(_BrokenAdd(memory.repository.connection))
    with pytest.raises(RuntimeError):
        broken.record(procedure.id, VerificationStatus.VERIFIED, expected="e", observed="o")
    again = memory.find(procedure.id).procedure
    assert again.execution_count == 0 and again.history == ()


# ------------------------------------------------------------- running (P16-4 ... P16-7)


def test_a_dry_run_runs_every_step_and_records_nothing(memory, tmp_path):
    procedure = _by_page(memory, 1)
    platform = SimulatedPlatform(disk=True)
    runner = ProcedureRunner(platform, record=_never, screenshots=tmp_path, clock=lambda: FIXED)
    run = runner.run(procedure, (("folder", str(tmp_path / "work")),), confirmed=False)
    assert run.outcome is ProcedureOutcome.DONE and run.recorded is None and not run.live
    assert [s.action for s in run.steps] == ["CREATE_FOLDER", "CREATE_FILE", "COPY_FILE"]
    assert all(d.decision is Decision.NOT_REQUIRED for d in run.permissions)
    assert not (tmp_path / "work").exists()


def test_a_missing_parameter_is_missing_information_and_nothing_runs(memory, tmp_path):
    platform = _LiveLike()
    run = ProcedureRunner(platform, record=_never, screenshots=tmp_path).run(_by_page(memory, 1), (), confirmed=True)
    assert run.outcome is ProcedureOutcome.MISSING_INFORMATION
    assert run.message == "Missing information:\nfolder" and run.missing == ("folder",)
    assert platform.log == [] and run.plan is None


@pytest.mark.parametrize("supplied", [(("colour", "red"),), (("folder", ""),), (("folder", "a\nb"),),
                                      (("folder", "C:/a"), ("Folder", "C:/b"))])
def test_an_unknown_parameter_or_a_bad_value_is_invalid(memory, tmp_path, supplied):
    with pytest.raises(InvalidInputError):
        ProcedureRunner(_LiveLike(), record=_never, screenshots=tmp_path).run(
            _by_page(memory, 1), supplied, confirmed=True)


def test_live_without_confirmation_is_refused_and_nothing_runs(memory, tmp_path):
    platform = _LiveLike()
    run = ProcedureRunner(platform, record=_never, screenshots=tmp_path).run(
        _by_page(memory, 1), (("folder", str(tmp_path / "work")),), confirmed=False)
    assert run.outcome is ProcedureOutcome.REFUSED and run.execution is None
    assert "--confirm" in run.message and platform.log == []


def test_a_medium_documented_step_is_refused_even_when_confirmed(memory, tmp_path):
    platform = _LiveLike()
    run = ProcedureRunner(platform, record=_never, screenshots=tmp_path).run(
        _by_page(memory, 2), (("archive", str(tmp_path / "a")), ("folder", str(tmp_path / "w"))), confirmed=True)
    assert run.outcome is ProcedureOutcome.REFUSED and platform.log == []
    assert [d.decision for d in run.permissions] == [Decision.PERMITTED, Decision.REFUSED]
    assert "MEDIUM risk: MOVE_FILE is a documented step" in run.permissions[1].reason


def test_menu_steps_are_not_executable_and_say_why(memory, tmp_path):
    platform = _LiveLike()
    run = ProcedureRunner(platform, record=_never, screenshots=tmp_path).run(_by_page(memory, 3), (), confirmed=True)
    assert run.outcome is ProcedureOutcome.NOT_EXECUTABLE and platform.log == []
    first, second = run.steps
    assert "cannot be planned as OPEN_FILE" in first.problem  # "the File menu" is not a file
    assert second.action is None and "UNRECOGNIZED" in second.problem


def test_two_live_runs_are_verified_and_recorded(memory, tmp_path):
    procedure = _by_page(memory, 1)
    runner = _runner(memory, _LiveLike(folders=(str(tmp_path),)), tmp_path)
    for name in ("first", "second"):
        run = runner.run(procedure, (("folder", str(tmp_path / name)),), confirmed=True)
        assert run.outcome is ProcedureOutcome.DONE and run.recorded.startswith("VER-")
    after = memory.find(procedure.id).procedure
    assert (after.execution_count, after.successful_executions, after.failed_executions) == (2, 2, 0)
    assert [h.status for h in after.history] == ["VERIFIED", "VERIFIED"]
    assert "environment simulated on" in after.history[0].observed
    assert f"folder={tmp_path / 'first'}" in after.history[0].observed
    assert after.steps == procedure.steps and after.source.status is ProvenanceStatus.AVAILABLE


def test_a_blocked_live_run_is_recorded_as_a_failure_with_what_was_unmet(memory, tmp_path):
    procedure = _by_page(memory, 1)
    # The folder the first step would create already exists.
    platform = _LiveLike(folders=(str(tmp_path), str(tmp_path / "taken")))
    run = _runner(memory, platform, tmp_path).run(procedure, (("folder", str(tmp_path / "taken")),), confirmed=True)
    assert run.outcome is ProcedureOutcome.FAILED and "unmet:" in run.message
    after = memory.find(procedure.id).procedure
    assert (after.execution_count, after.successful_executions, after.failed_executions) == (1, 0, 1)
    assert after.history[0].status == "FAILED" and "step 1 BLOCKED" in after.history[0].observed
    assert after.labels == ("DOCUMENTED_PROCEDURE",) and "never fully verified" in after.note


# ------------------------------------------------------------ permission (P16-6)


def test_the_documented_procedure_policy():
    from app.actions import ActionEngine

    requests = [("CREATE_FOLDER", {"path": "C:/rudra-test/a"}), ("MOVE_FILE", {"source": "C:/rudra-test/a/x.txt",
                                                                             "destination": "C:/rudra-test/b.txt"})]
    live = ActionEngine(_LiveLike()).plan(requests)
    assert [d.decision for d in decide_documented(live, confirmed=False)] == [Decision.REFUSED, Decision.REFUSED]
    assert [d.decision for d in decide_documented(live, confirmed=True)] == [Decision.PERMITTED, Decision.REFUSED]
    dry = ActionEngine(SimulatedPlatform()).plan(requests)
    assert {d.decision for d in decide_documented(dry, confirmed=False)} == {Decision.NOT_REQUIRED}


def test_a_live_run_that_cannot_be_recorded_still_reports_what_ran(memory, tmp_path):
    from app.core.errors import StorageError

    def locked(*args, **kwargs):
        raise StorageError.of("The knowledge database is locked.", "Another writer holds it.",
                              stage="test", data_changed=False, retry_safe=True)

    procedure = _by_page(memory, 1)
    platform = _LiveLike(folders=(str(tmp_path),))
    run = ProcedureRunner(platform, record=locked, screenshots=tmp_path).run(
        procedure, (("folder", str(tmp_path / "work")),), confirmed=True)
    assert run.outcome is ProcedureOutcome.DONE and run.recorded is None
    assert run.record_error.startswith("The knowledge database is locked.")
    assert "NOT RECORDED" in run.message and [s.status.value for s in run.execution.steps] == ["VERIFIED"] * 3
