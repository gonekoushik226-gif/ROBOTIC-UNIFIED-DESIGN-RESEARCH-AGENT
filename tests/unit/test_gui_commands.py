"""The desktop window's non-display logic (ADR 0057): the command lines it builds and runs.

The window runs the command line's own commands; these tests pin the command lines each
form produces, the approval asked before writes and actions, and that a run returns the
command line's own output and exit code - without opening a window.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ui.cli.main import ExitCode
from app.ui.gui import commands
from app.ui.gui.commands import CommandResult, FormError
from app.version import PHASE, VERSION
from tests.conftest import PROJECT_ROOT

# ---------------------------------------------------------------- forms to command lines


def test_status_commands_are_the_command_lines_own():
    assert [commands.status(name) for name in commands.STATUS_COMMANDS] == [
        ["start"], ["env"], ["paths"], ["config"], ["version"]]
    with pytest.raises(FormError):
        commands.status("db")


def test_ask_simulates_actions_unless_told_to_act():
    # The window reads the answer as JSON, so it can keep the sources behind View Sources.
    assert commands.ask("  What is resistance?  ") == ["ask", "What is resistance?", "--json", "--dry-run"]
    assert commands.ask("Open Notepad.", act=True) == ["ask", "Open Notepad.", "--json"]
    assert commands.ask("Close Notepad.", act=True, confirm=True) == ["ask", "Close Notepad.", "--json", "--confirm"]
    assert commands.ask("Close Notepad.", confirm=True) == ["ask", "Close Notepad.", "--json", "--dry-run"]
    assert commands.is_answer_command(commands.ask("What is resistance?"))
    assert not commands.is_answer_command(["ask", "What is resistance?"])
    assert not commands.is_answer_command(["query", "--json"])


def test_lookup_modes():
    assert commands.lookup("name", "Resistance") == ["lookup", "--name", "Resistance"]
    assert commands.lookup("identifier", "K-00000001") == ["query", "K-00000001"]
    assert commands.lookup("keyword", "current") == ["query", "--keyword", "current"]
    with pytest.raises(FormError):
        commands.lookup("fuzzy", "x")


def test_calculate_takes_one_formula_input_or_assumption_per_line():
    argv = commands.calculate(" I ", "I = V / Rtotal\n\n Rtotal = R1 + R2 \n", "R1=10 Ω\nR2=20 Ω\nV=10 V",
                              "T=300 K", " K-00000007 ")
    assert argv == ["calculate", "I", "--formula", "I = V / Rtotal", "--formula", "Rtotal = R1 + R2",
                    "--input", "R1=10 Ω", "--input", "R2=20 Ω", "--input", "V=10 V",
                    "--assume", "T=300 K", "--admit", "K-00000007"]
    assert commands.calculate("I", "", "") == ["calculate", "I"]


def test_provenance_and_extract():
    assert commands.provenance(" K-00000001") == ["provenance", "K-00000001"]
    assert commands.extract(r"C:\books\a b.pdf") == ["extract", r"C:\books\a b.pdf"]
    assert commands.extract("m.pdf", "Notepad") == ["extract", "m.pdf", "--manual", "Notepad"]


@pytest.mark.parametrize("build", [
    lambda: commands.ask(" "), lambda: commands.lookup("name", ""), lambda: commands.calculate("", "x = 1", ""),
    lambda: commands.provenance("\t"), lambda: commands.extract(""), lambda: commands.typed("  "),
    lambda: commands.assistant(""),
])
def test_an_empty_required_field_is_refused_before_anything_runs(build):
    with pytest.raises(FormError):
        build()


def test_typed_command_lines_split_as_windows_splits_them():
    assert commands.typed(r'extract "C:\books\a b.pdf" --manual Notepad') == [
        "extract", r"C:\books\a b.pdf", "--manual", "Notepad"]
    assert commands.typed('ask "What is resistance?" --dry-run') == ["ask", "What is resistance?", "--dry-run"]
    assert commands.typed("help") == ["--help"]


def test_the_assistant_asks_unless_the_line_starts_with_a_slash():
    assert commands.assistant("What is resistance?") == ["ask", "What is resistance?", "--json", "--dry-run"]
    assert commands.assistant("/version") == ["version"]
    assert commands.assistant("/help") == ["--help"]
    assert commands.assistant("/") == ["--help"]


def test_the_command_names_come_from_the_command_lines_parser():
    names = commands.command_names()
    assert names[0] == "start" and {"ask", "calculate", "provenance", "extract", "voice", "source"} <= set(names)


# ---------------------------------------------------------------- approval


@pytest.mark.parametrize("argv", [
    ["version"], ["start"], ["lookup", "--name", "X"], ["query", "K-1"], ["calculate", "I"], ["provenance", "K-1"],
    ["interpret", "open notepad"], ["index"], ["reason", "X"], ["source", "DOC-00000001"],
    ["ask", "What is it?", "--dry-run"], ["do", "Open Notepad.", "--dry-run"], ["act", "OPEN_APPLICATION"],
    ["research", "What is it?"], ["voice", "--say", "hello", "--audio", "out.wav"], ["--help"],
])
def test_commands_that_write_nothing_and_act_on_nothing_run_without_a_question(argv, tmp_path):
    assert commands.approval(argv, tmp_path) is None


@pytest.mark.parametrize("argv, title", [
    (["db"], "Create or upgrade the database"),
    (["extract", "a.pdf"], "Import a document"),
    (["classify", "DOC-00000001"], "Run 'classify'"),
    (["edition", "DOC-2", "--work", "DOC-1"], "Run 'edition'"),
    (["merge"], "Run 'merge'"),
    (["research", "What?", "--site", "https://example.org/"], "Retrieve a web page"),
    (["source", "DOC-00000001", "--delete-file", "--confirm"], "Delete RUDRA's copy of a document"),
    (["procedure", "PRC-00000001", "--dry-run"], "Run a procedure"),
    (["procedure", "PRC-00000001", "--execute", "--confirm"], "Run a procedure"),
    (["act", "OPEN_APPLICATION", "--execute"], "Act on this computer"),
    (["do", "Open Notepad."], "Act on this computer"),
    (["ask", "Open Notepad."], "Act on this computer"),
    (["voice", "--listen"], "Act on this computer"),
    (["--json", "extract", "a.pdf"], "Import a document"),
])
def test_writes_actions_and_the_internet_are_put_to_the_user_first(argv, title, tmp_path):
    approval = commands.approval(argv, tmp_path)
    assert approval is not None and approval.title == title


def test_the_approval_names_the_database_and_the_backup_rule_when_it_holds_knowledge(tmp_path):
    database = tmp_path / "data" / "database" / "knowledge.db"
    assert str(database) in commands.approval(["db"], tmp_path).message
    assert "export a backup" not in commands.approval(["db"], tmp_path).message
    database.parent.mkdir(parents=True)
    database.write_bytes(b"")
    assert "export a backup" in commands.approval(["extract", "a.pdf"], tmp_path).message


# ---------------------------------------------------------------- running


def test_a_run_returns_the_command_lines_own_output_and_exit_code(tmp_path):
    result = commands.run(["version"], tmp_path)
    assert result.ok and result.exit_code == 0 and result.meaning == "ok"
    assert f"RUDRA {VERSION}" in result.stdout and PHASE in result.stdout
    assert "RUDRA starting" in result.stderr  # the console log, captured too
    assert result.argv == ("version",) and (tmp_path / "logs").is_dir()  # --project-root was passed


def test_a_run_reports_what_the_command_line_reports(tmp_path):
    no_database = commands.run(["lookup", "--name", "Nothing"], tmp_path)  # the project has no database yet
    assert no_database.exit_code == ExitCode.STORAGE and no_database.meaning == "storage"
    assert not no_database.ok and "RUDRA exiting with code 5" in no_database.stderr
    refused = commands.run(["no-such-command"], tmp_path)  # argparse refuses; its exit is captured
    assert refused.exit_code == 2 and "invalid choice" in refused.stderr
    reference = commands.run(["--help"], tmp_path)
    assert reference.ok and reference.stdout.startswith("usage: python -m app")


def test_a_calculation_keeps_its_units_and_symbols_exactly(tmp_path):
    result = commands.run(commands.calculate("I", "I = V / R", "V=10 V\nR=5 Ω"), tmp_path)
    assert result.ok and "I = 2 A" in result.stdout and "R = 5 Ω" in result.stdout


def test_exit_meanings_come_from_the_command_line():
    assert commands.exit_meaning(0) == "ok"
    assert commands.exit_meaning(3) == "missing information"
    assert commands.exit_meaning(70) == "unknown error"
    assert commands.exit_meaning(99) == "exit code 99"


def test_display_command_is_what_a_terminal_would_run():
    assert commands.display_command(["ask", "What is it?", "--dry-run"]) == 'python -m app ask "What is it?" --dry-run'


def test_the_startup_summary_is_short_and_keeps_what_needs_attention():
    stdout = ("RUDRA AI 0.1.0  (Phase 23 - Final Acceptance)\n  Project root : X\n\nAttention:\n"
              "  [WARNING] RAM: Little headroom.\n          Close things.\n\nStartup complete. ...\n")
    summary = commands.startup_summary(CommandResult(("start",), 0, stdout, "", 0.1))
    assert summary.splitlines() == ["RUDRA AI 0.1.0  (Phase 23 - Final Acceptance)", "Ready: startup complete.",
                                    "Attention:", "  [WARNING] RAM: Little headroom."]
    calm = commands.startup_summary(CommandResult(("start",), 0, "RUDRA AI\nStartup complete.\n", "", 0.1))
    assert calm.endswith("Nothing needs attention.")
    failed = commands.startup_summary(CommandResult(("start",), 5, "", "Storage failed.", 0.1))
    assert failed.startswith("Startup did not complete: storage (exit 5).")


def test_answer_lines_are_recognized():
    assert commands.is_answer_line("ANSWER      : Definition: Resistance is ...\n")
    assert commands.is_answer_line("Answer      : CALCULATED - I = 2 A")
    assert not commands.is_answer_line("Answers are returned, never stored.")
    assert not commands.is_answer_line("Request     : What is it?")


# ---------------------------------------------------------------- resources


def test_the_bundle_root_of_the_source_tree_is_the_project():
    assert commands.bundle_root() == PROJECT_ROOT.resolve()


def test_the_help_documents_are_the_projects_own():
    assert commands.document("README").startswith("# RUDRA")
    assert commands.document("Limitations").startswith("# RUDRA — Limitations")


def test_the_marks_and_the_icon_are_where_the_window_looks():
    for name in ("rudra.ico", *(f"rudra-{size}.png" for size in (32, 48, 64, 96, 128, 256))):
        assert commands.asset(name).is_file(), name
    assert commands.asset("rudra.ico").parent == Path(commands.__file__).resolve().parent / "assets"
