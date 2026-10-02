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
from app.version import EDITION, VERSION
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
    (["db"], "Create or upgrade the knowledge base"),
    (["extract", "a.pdf"], "Add a document"),
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
    (["--json", "extract", "a.pdf"], "Add a document"),
])
def test_writes_actions_and_the_internet_are_put_to_the_user_first(argv, title, tmp_path):
    approval = commands.approval(argv, tmp_path)
    assert approval is not None and approval.title == title


def test_the_approval_names_the_database_and_the_backup_rule_when_it_holds_knowledge(tmp_path):
    database = tmp_path / "data" / "database" / "knowledge.db"
    assert str(database) in commands.approval(["db"], tmp_path).message
    assert "Back up your knowledge" not in commands.approval(["db"], tmp_path).message
    database.parent.mkdir(parents=True)
    database.write_bytes(b"")
    assert "Back up your knowledge" in commands.approval(["extract", "a.pdf"], tmp_path).message


# ---------------------------------------------------------------- running


def test_a_run_returns_the_command_lines_own_output_and_exit_code(tmp_path):
    result = commands.run(["version"], tmp_path)
    assert result.ok and result.exit_code == 0 and result.meaning == "ok"
    assert f"RUDRA {VERSION}" in result.stdout and EDITION in result.stdout
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
    stdout = ("RUDRA 1.0.0  (for Windows)\n  Project root : X\n\nAttention:\n"
              "  [WARNING] RAM: Little headroom.\n          Close things.\n\nStartup complete. ...\n")
    summary = commands.startup_summary(CommandResult(("start",), 0, stdout, "", 0.1))
    assert summary.splitlines() == ["RUDRA is ready.", "Attention:", "  Memory: Little headroom."]
    calm = commands.startup_summary(CommandResult(("start",), 0, "RUDRA\nStartup complete.\n", "", 0.1))
    assert calm == "RUDRA is ready."
    failed = commands.startup_summary(CommandResult(("start",), 5, "", "Storage failed.", 0.1))
    assert failed.startswith("RUDRA could not start properly (exit 5).") and "Storage failed." in failed
    assert "Phase" not in summary + calm + failed


def test_the_welcome_facts_come_from_the_inventory_result():
    stdout = ('{"totals": {"documents": 2, "counts": {"CONCEPT": 3, "EQUATION": 9}, "uncertain": 4}, '
              '"calculation": {"usable": 5}}')
    facts = commands.welcome_facts(CommandResult(("inventory", "--json"), 0, stdout, "", 0.1))
    assert facts == {"documents": 2, "counts": {"CONCEPT": 3, "EQUATION": 9}, "usable": 5, "uncertain": 4}
    nothing = commands.welcome_facts(CommandResult(("inventory", "--json"), 3, "", "", 0.1))
    assert nothing["documents"] == 0


def test_an_answer_of_unknown_is_not_an_error_in_the_status_bar():
    unknown = CommandResult(("ask", "What is X?", "--json"), 3, '{"parts": []}', "", 0.1)
    assert commands.finished_state(unknown) == ("READY · answered", True)
    added = CommandResult(("extract", "a.pdf"), 0, "", "", 0.1)
    assert commands.finished_state(added) == ("READY · document added", True)
    refused = CommandResult(("extract", "a.pdf"), 2, "", "", 0.1)
    assert commands.finished_state(refused)[1] is False


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


def test_the_help_readme_leaves_out_the_part_about_building_from_source():
    readme = commands.document("README")
    assert "## Using RUDRA" in readme and "## Limitations" in readme
    assert "For developers" not in readme and "git clone" not in readme and not readme.rstrip().endswith("---")


def test_the_marks_and_the_icon_are_where_the_window_looks():
    for name in ("rudra.ico", *(f"rudra-{size}.png" for size in (32, 48, 64, 96, 128, 256))):
        assert commands.asset(name).is_file(), name
    assert commands.asset("rudra.ico").parent == Path(commands.__file__).resolve().parent / "assets"


# ---------------------------------------------------------------- plain-language summaries ("Add document")


def test_failure_headline_reads_the_commands_own_plain_english_summary(tmp_path):
    result = commands.run(["extract", str(tmp_path / "missing.pdf")], tmp_path)
    assert not result.ok
    assert commands.failure_headline(result) == "That file does not exist."


def test_failure_headline_falls_back_when_there_is_no_structured_report(tmp_path):
    result = commands.run(["no-such-command"], tmp_path)
    assert commands.failure_headline(result) == f"RUDRA could not complete this (exit 2: {commands.exit_meaning(2)})."


def test_is_extract_command():
    assert commands.is_extract_command(["extract", "a.pdf"])
    assert not commands.is_extract_command(["ask", "x", "--json"])
    assert not commands.is_extract_command([])


def test_extract_summary_for_a_fresh_document(tmp_path):
    """A summary of `extract` alone cannot say the document is searchable yet: nothing has indexed it."""
    from tests.unit.pdf_fixtures import textbook_pdf

    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(textbook_pdf())
    result = commands.run(commands.extract(str(pdf)), tmp_path)
    assert result.ok
    summary = commands.extract_summary([result])
    assert summary.ok and not summary.already_had and not summary.partially_processed
    assert summary.document_id and summary.document_id.startswith("DOC-")
    assert summary.pages == "4 pages"
    assert summary.issue_total == 0
    assert summary.ready is False
    assert summary.headline == "Added to your knowledge base."


def test_extract_summary_says_what_was_stored_when_the_inventory_is_included(tmp_path):
    from tests.unit.pdf_fixtures import textbook_pdf

    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(textbook_pdf())
    extracted = commands.run(commands.extract(str(pdf)), tmp_path)
    indexed = commands.run(commands.index(), tmp_path)
    inventory = commands.run(commands.inventory(), tmp_path)
    assert extracted.ok and indexed.ok and inventory.ok
    summary = commands.extract_summary([extracted, indexed, inventory])
    assert summary.ready is True and summary.stored and all(word[0].isdigit() for word in summary.stored)
    assert summary.headline == "Added to your knowledge base."


def test_extract_summary_for_an_already_ingested_document(tmp_path):
    from tests.unit.pdf_fixtures import textbook_pdf

    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(textbook_pdf())
    commands.run(commands.extract(str(pdf)), tmp_path)
    second = commands.run(commands.extract(str(pdf)), tmp_path)
    summary = commands.extract_summary([second])
    assert second.ok and summary.already_had
    assert summary.headline == "This document is already in your knowledge base."


def test_extract_summary_for_a_failure(tmp_path):
    result = commands.run(commands.extract(str(tmp_path / "missing.pdf")), tmp_path)
    assert not result.ok
    summary = commands.extract_summary([result])
    assert not summary.ok and summary.document_id is None and summary.ready is False
    assert summary.headline == "That file does not exist."


def test_a_document_with_nothing_to_store_is_added_but_says_so(tmp_path):
    """The window never claims knowledge it does not have: a plain note leaves the inventory empty."""
    note = tmp_path / "notes.txt"
    note.write_text("Just some notes.\nNothing technical here at all, only plain sentences about lunch.\n",
                    encoding="utf-8")
    results = [commands.run(commands.extract(str(note)), tmp_path)]
    results.append(commands.run(commands.index(), tmp_path))
    results.append(commands.run(commands.inventory(), tmp_path))
    assert all(result.ok for result in results)
    summary = commands.extract_summary(results)
    assert summary.ok and summary.empty and not summary.stored
    assert summary.pages == "1 section"  # a count of one is not "1 sections"
    assert summary.headline == "Added, but RUDRA found nothing in it to store as knowledge."


def test_a_document_that_gave_knowledge_is_not_called_empty(tmp_path):
    from tests.unit.pdf_fixtures import textbook_pdf

    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(textbook_pdf())
    results = [commands.run(commands.extract(str(pdf)), tmp_path)]
    results.append(commands.run(commands.index(), tmp_path))
    results.append(commands.run(commands.inventory(), tmp_path))
    summary = commands.extract_summary(results)
    assert summary.stored and not summary.empty


def test_a_file_that_cannot_be_added_comes_with_what_to_do_about_it(tmp_path):
    """The reason RUDRA writes for a person (save it as .docx) reaches the window, not only the headline."""
    old = tmp_path / "legacy.doc"
    old.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1not a real doc")
    result = commands.run(commands.extract(str(old)), tmp_path)
    assert not result.ok
    summary = commands.extract_summary([result])
    assert summary.headline == "RUDRA cannot import that kind of file."
    assert any(".docx" in line for line in summary.advice)
    assert all("Traceback" not in line and "\\" not in line for line in summary.advice)


def test_failure_advice_is_empty_without_a_structured_report(tmp_path):
    result = commands.run(["no-such-command"], tmp_path)
    assert commands.failure_advice(result) == ()


def test_index_command():
    assert commands.index() == ["index"]


def test_inventory_command():
    assert commands.inventory() == ["inventory", "--json"]
    assert commands.inventory("EQUATION", "DOC-00000001") == [
        "inventory", "--json", "--knowledge-type", "EQUATION", "--in-document", "DOC-00000001"]


# ---------------------------------------------------------------- uninstalling


def test_find_uninstaller_reads_the_inno_setup_registry_entry(monkeypatch):
    import winreg

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def fake_open_key(hive, path):
        assert hive == winreg.HKEY_CURRENT_USER
        assert path == rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{commands.RUDRA_APP_ID}_is1"
        return FakeKey()

    monkeypatch.setattr(winreg, "OpenKey", fake_open_key)
    monkeypatch.setattr(winreg, "QueryValueEx",
                        lambda key, name: (r'"C:\Users\x\AppData\Local\Programs\RUDRA\unins000.exe"', 1))
    assert commands.find_uninstaller() == r'"C:\Users\x\AppData\Local\Programs\RUDRA\unins000.exe"'


def test_find_uninstaller_is_none_when_rudra_was_not_installed_by_it(monkeypatch):
    import winreg

    def fake_open_key(hive, path):
        raise FileNotFoundError

    monkeypatch.setattr(winreg, "OpenKey", fake_open_key)
    assert commands.find_uninstaller() is None
