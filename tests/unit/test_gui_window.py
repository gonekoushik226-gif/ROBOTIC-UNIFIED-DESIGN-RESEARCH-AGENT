"""The desktop window itself (ADR 0057), built hidden: its pages, layouts and command runs.

The window is withdrawn before it is built, so nothing appears on screen. Commands run on
the window's worker thread through the command line's own entry point, in a temporary
project; the event loop is pumped until each result arrives.
"""

from __future__ import annotations

import gc
import threading
import time
import tkinter as tk

import pytest

from app.ui.gui import commands
from app.ui.gui.commands import Approval
from app.ui.gui.window import PAGES, RudraWindow
from app.version import EDITION


@pytest.fixture
def window(tmp_path, capsys):
    # pytest's default capture swaps the process's standard file handles, and Tcl binds
    # its standard channels to them while it starts: measured here, about one start in
    # two then fails to find its own library ("Can't find a usable init.tcl"). Capture
    # is suspended only while Tk starts; the application never runs under pytest.
    try:
        with capsys.disabled():
            root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - a machine without a display
        pytest.skip(f"Tk cannot open a window here: {exc}")
    root.withdraw()
    built = RudraWindow(root, tmp_path, full=True, autostart=False)
    yield built
    root.destroy()
    # Tk may only be called from the thread that created it. pytest still holds this
    # window after teardown, so the cycle collector could later finalize its Tk objects
    # on another test's worker thread and break the next test's Tk. Collect what can go
    # now, then freeze the rest so the collector never finalizes it anywhere. (The
    # application has one window for its whole life, so it never meets this.)
    del built, root
    gc.collect()
    gc.freeze()


def _wait(window: RudraWindow, timeout: float = 60.0):
    deadline = time.monotonic() + timeout
    while window.busy and time.monotonic() < deadline:
        window.root.update()
        time.sleep(0.01)
    assert not window.busy, "the command did not finish"
    window.root.update()
    return window.last


def test_the_full_interface_has_every_page_in_order(window):
    assert list(window.full.pages) == ["ask", "import", "knowledge", "settings", "status", "lookup", "provenance",
                                       "command", "help", "backup", "ai"]
    assert len(PAGES) == 11
    # Backup and AI are fully working pages (Settings calls straight into them) but keep no
    # sidebar entry of their own, since Settings now offers the same capability more simply.
    # Everyday use comes first; the pages that show the detail underneath are grouped as Advanced.
    assert list(window.full.nav) == ["ask", "import", "knowledge", "settings", "status", "lookup", "provenance",
                                     "command", "help"]
    assert [key for key, page in window.full.pages.items() if page.advanced] == [
        "status", "lookup", "provenance", "command", "help"]
    assert window.full.selected == "ask"  # RUDRA opens on the page where the work is done
    for key in window.full.pages:
        window.full.select(key)
        assert window.full.selected == key
        assert window.full.page_title.cget("text") == window.full.pages[key].heading
        if key in window.full.nav:
            assert window.full.nav[key].active


def test_the_window_switches_between_the_assistant_and_the_full_interface(window):
    assert window.full_shown is True and window.full.frame.winfo_manager() == "pack"
    window.show(full=False)
    assert window.compact.frame.winfo_manager() == "pack" and not window.full.frame.winfo_manager()
    window.show(full=True)
    assert window.full.frame.winfo_manager() == "pack" and not window.compact.frame.winfo_manager()


def test_a_page_runs_its_command_line_and_shows_its_output_in_both_views(window):
    assert window.full.pages["status"].run("version")
    result = _wait(window)
    assert result.argv == ("version",) and result.ok and EDITION in result.stdout
    shown = window.full.output.text_content()
    assert shown.startswith("› python -m app version") and EDITION in shown and "ok · ok ·" in shown
    assert EDITION in window.compact.output.text_content()
    assert window.full.state.cget("text") == "READY · version done"


def test_calculation_is_asked_for_through_ask_not_a_separate_page(window):
    """No standalone Calculate page: calculation happens through Ask, naturally. When the
    question states the formula, those are the formulas used."""
    assert "calculate" not in window.full.pages and "calculate" not in window.full.nav
    window.full.pages["ask"].question.set(
        "Calculate I given I = V / Rtotal, Rtotal = R1 + R2, R1 = 10 Ω, R2 = 20 Ω, V = 10 V")
    assert window.full.pages["ask"].run()
    result = _wait(window)
    assert result.argv[0] == "ask" and result.ok
    assert '"status": "CALCULATED"' in result.stdout and '"displayed": "0.333333"' in result.stdout


@pytest.mark.parametrize(("template", "expected"), [
    ("What is it?", "What is capacitance?"),
    ("How does it work?", "How does capacitance work?"),
    ("How is it used?", "How is capacitance used?"),
    ("What are its applications?", "What are applications of capacitance?"),
])
def test_question_templates_fill_the_topic_and_send_a_normal_ask(window, template, expected):
    page = window.full.pages["ask"]
    page.template.set(template)
    page.template_topic.set(" capacitance ")
    seen = []
    page.run = lambda: seen.append(page.question.get()) or True
    assert page.ask_template()
    assert seen == [expected]


def test_a_failing_command_shows_its_exit_and_report(window):
    window.full.pages["lookup"].value.set("Resistance")
    assert window.full.pages["lookup"].run()
    result = _wait(window)
    assert result.exit_code == 5 and window.full.state.cget("text") == "EXIT 5 · storage"
    assert "exit 5 · storage" in window.full.output.text_content()


def test_an_incomplete_form_runs_nothing(window):
    window.full.pages["provenance"].identifier.set("  ")
    assert not window.full.pages["provenance"].run()
    assert window.last is None and not window.busy
    assert "Type an identifier" in window.full.output.text_content()
    assert window.full.state.cget("text") == "NOT RUN"


def test_a_declined_approval_runs_nothing(window, tmp_path):
    asked: list[Approval] = []
    window.confirm = lambda approval: asked.append(approval) or False
    window.full.pages["command"].line.set("db")
    assert not window.full.pages["command"].run()
    assert [approval.title for approval in asked] == ["Create or upgrade the knowledge base"]
    assert window.last is None and not (tmp_path / "data").exists()
    assert "Cancelled. Nothing was run." in window.full.output.text_content()


def test_the_assistant_asks_questions_and_runs_slash_commands(window):
    window.show(full=False)
    window.compact.entry.insert(0, "/version")
    assert window.compact.send()
    assert _wait(window).argv == ("version",)
    assert window.compact.entry.get() == ""
    window.compact.entry.insert(0, "What is resistance?")
    assert window.compact.send()
    assert _wait(window).argv == ("ask", "What is resistance?", "--json", "--dry-run")


def test_a_second_command_waits_for_the_first(window):
    assert window.full.pages["status"].run("version")
    assert not window.full.pages["status"].run("env")  # busy: refused, not queued
    assert all("disabled" in control.state() for control in window.run_controls)
    _wait(window)
    assert all("disabled" not in control.state() for control in window.run_controls)


def test_the_help_page_shows_the_projects_documents(window):
    window.full.pages["help"].document("Limitations")
    assert commands.DOCUMENTS["Limitations"] in window.full.output.text_content()
    assert "## 2. Every subsystem, labelled" in window.full.output.text_content()


def test_the_window_is_titled_and_marked(window):
    assert window.root.title() == "RUDRA"
    assert window.mark(84) is not None and window.mark(84).width() >= 84


# ------------------------------------------------------------------ optional AI stays local until chosen


def test_ai_assistance_is_local_only_until_the_user_enables_it(window, monkeypatch):
    from app.ui.gui import answerview

    called = []
    monkeypatch.setattr(window, "ai_transport_send", lambda *a: called.append(a) or "never")
    part = answerview.AnswerPart(1, "ANSWERED", ("Definition: A capacitor stores charge.",), (), False,
                                 answerview.SourceDetails())
    assert window.ai_active() is False
    assert window.ai_explain(None, part, "What is a capacitor?") is False
    assert window.ai_interpret("tell me about capacitors") is False
    assert called == []  # nothing was sent anywhere
    assert "ai" in window.full.pages and not (window.project_root / "config" / "ai.json").exists()


# ------------------------------------------------------------------ voice dictation (click, speak, edit, send)


def test_window_listen_hands_back_recognized_text(window, monkeypatch):
    import app.voice

    monkeypatch.setattr(app.voice, "listen",
                        lambda seconds, **options: app.voice.Transcript("hello", "dictation", 0.8, "x", "mic"))
    results = []
    window.listen(results.append)
    _wait(window)
    assert len(results) == 1 and results[0].text == "hello" and results[0].error is None


def test_window_listen_reports_no_speech_in_plain_language(window, monkeypatch):
    import app.voice

    monkeypatch.setattr(app.voice, "listen",
                        lambda seconds, **options: app.voice.Transcript("", "", None, "x", "mic"))
    results = []
    window.listen(results.append)
    _wait(window)
    assert results[0].text == "" and "did not hear anything" in results[0].error


def test_window_listen_reports_an_unavailable_engine_in_plain_language(window, monkeypatch):
    import app.voice

    def fail(seconds, **options):
        raise app.voice.SpeechUnavailable("no microphone found")

    monkeypatch.setattr(app.voice, "listen", fail)
    results = []
    window.listen(results.append)
    _wait(window)
    assert "no microphone found" in results[0].error


def test_window_listen_cancellation_is_silent(window, monkeypatch):
    import app.voice

    def cancelled(seconds, **options):
        raise app.voice.SpeechCancelled("stopped")

    monkeypatch.setattr(app.voice, "listen", cancelled)
    results = []
    window.listen(results.append)
    _wait(window)
    assert results[0].text == "" and results[0].error == ""  # cancelled, not an error to show


def test_the_asks_mic_button_fills_the_question_field_for_the_user_to_edit(window, monkeypatch):
    import app.voice

    monkeypatch.setattr(app.voice, "listen", lambda seconds, **options: app.voice.Transcript(
        "What is resistance?", "dictation", 0.8, "x", "mic"))
    page = window.full.pages["ask"]
    page.mic.button.invoke()
    _wait(window)
    assert page.question.get() == "What is resistance?"
    assert page.mic.button.cget("text") == "Speak"  # back to idle, ready to edit and press Ask


def test_an_uncertain_reading_is_still_filled_in_but_flagged_for_checking(window, monkeypatch):
    import app.voice

    monkeypatch.setattr(app.voice, "listen", lambda seconds, **options: app.voice.Transcript(
        "Like name is go seek", "dictation", 0.31, "x", "mic", uncertain=True))
    results = []
    window.listen(results.append)
    _wait(window)
    assert results[0].text == "Like name is go seek" and results[0].uncertain is True
    page = window.full.pages["ask"]
    page.mic.button.invoke()
    _wait(window)
    assert page.question.get() == "Like name is go seek"
    assert window.full.state.cget("text") == "CHECK THE WORDS"


def test_the_mic_button_says_when_it_is_listening_and_when_it_is_working_out_the_words(window, monkeypatch):
    import app.voice

    release = threading.Event()
    seen = {}

    def listen(seconds, cancel=None, finish=None, on_phase=None, hints=()):
        on_phase("listening")
        assert finish.wait(5)           # the first click: the person has finished speaking
        on_phase("recognizing")
        assert release.wait(5)
        return app.voice.Transcript("open calculator", "dictation", 0.9, "x", "mic")

    monkeypatch.setattr(app.voice, "listen", listen)
    page = window.full.pages["ask"]
    button = page.mic.button
    button.invoke()
    deadline = time.monotonic() + 5
    while "Listening" not in button.cget("text") and time.monotonic() < deadline:
        window.root.update()
        time.sleep(0.01)
    assert "Listening" in button.cget("text") and len(button.cget("text")) < 16  # the one row is shared with Send
    assert "CLICK WHEN DONE" in window.full.state.cget("text")
    button.invoke()                      # click when done: stop listening, recognize what was said
    while "Working" not in button.cget("text") and time.monotonic() < deadline:
        window.root.update()
        time.sleep(0.01)
    assert button.cget("text") == "Working…" and "CLICK TO CANCEL" in window.full.state.cget("text")
    seen["label"] = button.cget("text")
    release.set()
    _wait(window)
    assert page.question.get() == "open calculator" and button.cget("text") == "Speak"


def test_a_second_click_while_the_words_are_being_worked_out_cancels_without_inserting(window, monkeypatch):
    import app.voice

    def listen(seconds, cancel=None, finish=None, on_phase=None, hints=()):
        on_phase("recognizing")
        assert cancel.wait(5)
        raise app.voice.SpeechCancelled("stopped")

    monkeypatch.setattr(app.voice, "listen", listen)
    page = window.full.pages["ask"]
    page.question.set("keep this")
    results = []
    stop = window.listen(results.append, on_phase=lambda phase: None)
    deadline = time.monotonic() + 5
    while not window.busy and time.monotonic() < deadline:
        window.root.update()
    time.sleep(0.2)
    stop()                               # while "listening" was never reported -> first stop = finish ...
    stop()                               # ... the next one cancels
    _wait(window)
    assert results[0].text == "" and results[0].error == "" and page.question.get() == "keep this"


def test_the_saved_words_reach_the_recogniser(window, monkeypatch):
    import app.voice
    from app.voice import words

    words.save(window.project_root / "config", "Kaushik, Anantham")
    seen = {}

    def listen(seconds, **options):
        seen.update(options)
        return app.voice.Transcript("hello", "dictation", 0.9, "x", "mic")

    monkeypatch.setattr(app.voice, "listen", listen)
    window.listen(lambda result: None)
    _wait(window)
    assert seen["hints"] == ("Kaushik", "Anantham")


def test_settings_keeps_the_words_to_spell_as_written(window):
    from app.voice import words

    page = window.full.pages["settings"]
    page.words.set("Kaushik,  Kaushik , Anantham ; <b>")
    assert page.save_words() is True
    assert words.load(window.project_root / "config") == ("Kaushik", "Anantham", "b")
    assert page.words.get() == "Kaushik, Anantham, b" and "Saved 3 words" in page.words_status.cget("text")
    page.words.set("")
    page.load_words()                       # reopening Settings shows what was kept
    assert page.words.get() == "Kaushik, Anantham, b"
    page.words.set("   ")
    page.save_words()
    assert words.load(window.project_root / "config") == () and "No words kept" in page.words_status.cget("text")


# ------------------------------------------------------------------ Settings: the home for maintenance


def test_settings_has_no_calculate_and_hides_backup_and_ai_from_the_sidebar(window):
    assert "calculate" not in window.full.nav
    assert list(window.full.nav)[:4] == ["ask", "import", "knowledge", "settings"]
    assert {"backup", "ai"} <= set(window.full.pages) - set(window.full.nav)


def test_settings_backup_and_restore_delegate_to_the_backup_pages_own_methods(window, monkeypatch):
    settings = window.full.pages["settings"]
    backup_page = window.full.pages["backup"]
    calls = []
    monkeypatch.setattr(backup_page, "export", lambda: calls.append("export") or True)
    monkeypatch.setattr(backup_page, "restore", lambda: calls.append("restore") or True)
    assert settings.backup() is True and settings.restore() is True
    assert calls == ["export", "restore"]


def test_settings_shows_whether_ai_is_on_and_can_open_its_page(window):
    from app.providers import settings as ai_settings

    page = window.full.pages["settings"]
    page.refresh()
    assert "Off" in page.ai_status.cget("text")
    ai_settings.enable(window.project_root / "config", "mistral", "test-model", consent=True)
    page.refresh()
    assert "On" in page.ai_status.cget("text")
    page.open_ai()
    assert window.full.selected == "ai"


def test_settings_mic_test_shows_what_was_heard(window, monkeypatch):
    import app.voice

    monkeypatch.setattr(app.voice, "listen",
                        lambda seconds, **options: app.voice.Transcript("testing", "dictation", 0.8, "x", "mic"))
    page = window.full.pages["settings"]
    page.mic.button.invoke()
    _wait(window)
    assert "testing" in page.mic_status.cget("text")


def test_uninstall_asks_first_and_runs_the_real_installed_uninstaller(window, monkeypatch):
    from app.ui.gui import commands

    page = window.full.pages["settings"]
    monkeypatch.setattr(commands, "find_uninstaller", lambda: r'"C:\RUDRA\unins000.exe"')
    questions = []
    page.ask_yes = lambda title, message: questions.append(message) or True
    started = []
    page.start_uninstaller = lambda command: started.append(command)
    quit_calls = []
    page.quit = lambda: quit_calls.append(True)
    page.uninstall()
    assert questions and "uninstaller" in questions[0] and "knowledge base" in questions[0]
    assert started == [r'"C:\RUDRA\unins000.exe"']
    assert quit_calls == [True]


def test_uninstall_declines_safely_when_rudra_was_not_installed_by_the_installer(window, monkeypatch):
    from app.ui.gui import commands

    page = window.full.pages["settings"]
    monkeypatch.setattr(commands, "find_uninstaller", lambda: None)
    told = []
    page.show_info = lambda title, message: told.append(message)
    started = []
    page.start_uninstaller = lambda command: started.append(command)
    page.uninstall()
    assert told and "does not see an installation" in told[0]
    assert started == []


def test_uninstall_does_nothing_when_the_user_declines(window, monkeypatch):
    from app.ui.gui import commands

    page = window.full.pages["settings"]
    monkeypatch.setattr(commands, "find_uninstaller", lambda: r'"C:\RUDRA\unins000.exe"')
    page.ask_yes = lambda title, message: False
    started, quit_calls = [], []
    page.start_uninstaller = lambda command: started.append(command)
    page.quit = lambda: quit_calls.append(True)
    page.uninstall()
    assert started == [] and quit_calls == []


# ------------------------------------------------------------------ Add document: a plain summary, details on request


def test_adding_a_document_shows_a_plain_summary_with_details_on_request(window):
    from tests.unit.pdf_fixtures import textbook_pdf

    pdf = window.project_root / "book.pdf"
    pdf.write_bytes(textbook_pdf())
    page = window.full.pages["import"]
    page.pdf.set(str(pdf))
    assert page.run()
    _wait(window)
    shown = window.full.output.text_content()
    assert "book.pdf" in shown and "Added to your knowledge base." in shown
    assert "Stored:" in shown and "You can ask about it now." in shown  # what was found, in words
    assert "DOC-" not in shown and "Pages      :" not in shown  # technical detail stays behind Details
    assert window.full.output.source_buttons == {}  # this is not an answer view
    window.full.output.toggle_extract_details()
    shown = window.full.output.text_content()
    assert "DOC-00000001" in shown and "Pages      :" in shown  # the command's own report, unchanged


def test_adding_the_same_document_twice_says_so_in_plain_language(window):
    from tests.unit.pdf_fixtures import textbook_pdf

    pdf = window.project_root / "book.pdf"
    pdf.write_bytes(textbook_pdf())
    page = window.full.pages["import"]
    page.pdf.set(str(pdf))
    page.run()
    _wait(window)
    page.run()
    _wait(window)
    assert "This document is already in your knowledge base." in window.full.output.text_content()


def test_a_failed_import_shows_the_commands_own_plain_english_summary(window):
    page = window.full.pages["import"]
    page.pdf.set(str(window.project_root / "missing.pdf"))
    page.run()
    _wait(window)
    shown = window.full.output.text_content()
    assert "That file does not exist." in shown


def test_adding_a_document_makes_every_query_mode_reachable_immediately(window):
    """The root-cause fix: a document's text was only ever reachable by keyword search
    (unlike concept, exact and page queries) once someone explicitly rebuilt the derived
    index - a separate, technical, never-automatic step. Add Document now also rebuilds
    it, right after a successful `extract`, so a natural request like "Search for X" works
    straight away instead of surfacing "the index is MISSING"."""
    from tests.unit.pdf_fixtures import textbook_pdf

    pdf = window.project_root / "book.pdf"
    pdf.write_bytes(textbook_pdf())
    page = window.full.pages["import"]
    page.pdf.set(str(pdf))
    assert page.run()
    _wait(window)
    assert "Added to your knowledge base." in window.full.output.text_content()

    ask = window.full.pages["ask"]
    ask.question.set("Search for flip-flop")
    assert ask.run()
    result = _wait(window)
    assert result.ok
    import json

    part = json.loads(result.stdout)["parts"][0]  # what the person is shown; "detail" is the raw engine output
    said = " ".join([part["answer"], *part["reasoning"], *part["next_steps"]])
    assert "MISSING" not in said and "derived index" not in said and "index" not in said.lower()
    assert part["status"] == "ANSWERED" and "flip-flop" in said.lower()


def test_adding_a_document_needs_no_approval_and_then_lists_what_was_found(window, monkeypatch):
    from tests.unit.pdf_fixtures import textbook_pdf
    from app.ui.gui import commands as gui_commands

    calls: list[list[str]] = []
    real_run = gui_commands.run

    def spy(argv, project_root):
        calls.append(list(argv))
        return real_run(argv, project_root)

    monkeypatch.setattr(gui_commands, "run", spy)
    pdf = window.project_root / "book.pdf"
    pdf.write_bytes(textbook_pdf())
    page = window.full.pages["import"]
    page.pdf.set(str(pdf))
    assert page.run()
    _wait(window)
    assert calls == [["extract", str(pdf)], ["index"], ["inventory", "--json"]]
    assert window.last.argv == ("inventory", "--json")


def test_add_document_picker_accumulates_multiple_unique_files_and_removes_selection(window, monkeypatch):
    from app.ui.gui import window as gui_window

    first = window.project_root / "first.pdf"
    second = window.project_root / "second.pdf"
    first.touch()
    second.touch()
    selections = iter(((str(first), str(second)), (str(second),)))
    monkeypatch.setattr(gui_window.filedialog, "askopenfilenames", lambda **_kwargs: next(selections))
    page = window.full.pages["import"]
    page.browse()
    page.browse()
    assert page.selected_documents == [first, second]
    assert page.selection_status.cget("text") == "2 documents selected."
    page.file_list.selection_set(0)
    page.remove_selected()
    assert page.selected_documents == [second]
    page.clear_selection()
    assert page.selected_documents == []
    assert page.selection_status.cget("text") == "No documents selected."


def test_batch_import_continues_after_a_bad_file_and_rebuilds_index_once(window, monkeypatch):
    from tests.unit.pdf_fixtures import textbook_pdf, unit_module_pdf

    first = window.project_root / "first.pdf"
    invalid = window.project_root / "legacy.doc"
    last = window.project_root / "last.pdf"
    first.write_bytes(textbook_pdf())
    invalid.write_bytes(b"not a supported document")
    last.write_bytes(unit_module_pdf())

    calls: list[list[str]] = []
    real_run = commands.run

    def spy(argv, project_root):
        calls.append(list(argv))
        return real_run(argv, project_root)

    monkeypatch.setattr(commands, "run", spy)
    page = window.full.pages["import"]
    page.add_files((first, invalid, last))
    assert page.run()
    _wait(window, timeout=120)

    assert calls == [
        ["extract", str(first)], ["extract", str(invalid)], ["extract", str(last)],
        ["index"], ["inventory", "--json"],
    ]
    shown = window.full.output.text_content()
    assert "Added 2 of 3 documents." in shown
    assert "Added 2 of 3 documents." in window.compact.output.text_content()
    assert "first.pdf" in shown and "Added to your knowledge base." in shown
    assert "legacy.doc" in shown and "RUDRA cannot import that kind of file." in shown
    assert "last.pdf" in shown
    assert page.selected_documents == [invalid]  # successful inputs are cleared; failures remain retryable
    assert window.full.state.cget("text") == "PARTIAL · added 2 of 3"
    window.full.output.toggle_extract_details()
    details = window.full.output.text_content()
    assert details.count("› python -m app index") == 1
    assert "› python -m app extract" in details


def test_batch_import_does_not_run_index_when_every_file_fails(window, monkeypatch):
    missing_a = window.project_root / "missing-a.pdf"
    missing_b = window.project_root / "missing-b.pdf"
    calls: list[list[str]] = []
    real_run = commands.run

    def spy(argv, project_root):
        calls.append(list(argv))
        return real_run(argv, project_root)

    monkeypatch.setattr(commands, "run", spy)
    page = window.full.pages["import"]
    page.add_files((missing_a, missing_b))
    assert page.run()
    _wait(window)
    assert calls == [["extract", str(missing_a)], ["extract", str(missing_b)]]
    assert "Added 0 of 2 documents." in window.full.output.text_content()
    assert page.selected_documents == [missing_a, missing_b]


def test_adding_a_document_does_not_run_index_when_extract_itself_fails(window, monkeypatch):
    from app.ui.gui import commands as gui_commands

    calls: list[list[str]] = []
    real_run = gui_commands.run

    def spy(argv, project_root):
        calls.append(list(argv))
        return real_run(argv, project_root)

    monkeypatch.setattr(gui_commands, "run", spy)
    page = window.full.pages["import"]
    page.pdf.set(str(window.project_root / "missing.pdf"))
    assert page.run()
    _wait(window)
    assert calls == [["extract", str(window.project_root / "missing.pdf")]]  # nothing to list


def test_the_window_says_so_when_a_document_gave_nothing_to_store(window):
    note = window.project_root / "notes.txt"
    note.write_text("Just some notes.\nNothing technical here at all, only plain sentences about lunch.\n",
                    encoding="utf-8")
    page = window.full.pages["import"]
    page.pdf.set(str(note))
    assert page.run()
    _wait(window)
    shown = window.full.output.text_content()
    assert "Added, but RUDRA found nothing in it to store as knowledge." in shown
    assert "You can still ask RUDRA to search its text" in shown
    assert "You can ask about it now." not in shown


def test_the_window_tells_what_to_do_when_a_file_cannot_be_added(window):
    old = window.project_root / "legacy.doc"
    old.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1not a real doc")
    page = window.full.pages["import"]
    page.pdf.set(str(old))
    assert page.run()
    _wait(window)
    shown = window.full.output.text_content()
    assert "RUDRA cannot import that kind of file." in shown and ".docx" in shown


def _add(window, name: str, text: str) -> None:
    document = window.project_root / name
    document.write_text(text, encoding="utf-8")
    page = window.full.pages["import"]
    page.pdf.set(str(document))
    assert page.run()
    _wait(window)


CIRCUITS = """Circuits

The symbols are used in the usual way, where V is the voltage across the element.
The same convention is followed everywhere, where I is the current in the circuit.
The opposition to current is limited by the resistor, where R is the resistance in ohms.
V = I * R
P = V * I
"""


def test_the_welcome_says_the_knowledge_base_is_empty_then_what_it_holds(window):
    window.refresh_welcome()
    _wait(window)
    shown = window.full.output.text_content()
    assert "Welcome to RUDRA." in shown and "Your knowledge base is empty." in shown
    assert not any(word in shown for word in ("Phase", "ADR", "API-"))

    window.output_touched = False
    _add(window, "circuits.txt", CIRCUITS)
    window.output_touched = False
    window.refresh_welcome()
    _wait(window)
    shown = window.full.output.text_content()
    assert "Your knowledge base is ready." in shown and "1 document" in shown and "2 equations" in shown
    assert "2 of the 2 equations can be calculated with." in shown


def test_the_knowledge_page_lists_what_was_stored_and_the_source_of_each_item(window):
    _add(window, "circuits.txt", CIRCUITS)
    page = window.full.pages["knowledge"]
    window.full.select("knowledge")
    _wait(window)
    rows = page.tree.get_children()
    assert len(rows) == 1 and page.tree.item(rows[0], "text") == "circuits.txt"
    assert "2 equations" in page.summary.cget("text") and "can be calculated with" in page.summary.cget("text")
    stored = " ".join(page.tree.item(child, "text") for child in page.tree.get_children(rows[0]))
    assert "Stored:" in stored and "equation" in stored

    page.kind.set("Equations")
    assert page.load()
    _wait(window)
    items = page.tree.get_children()
    assert len(items) == 2
    page.tree.selection_set(items[0])
    window.root.update()
    detail = page.detail.cget("text")
    assert "From circuits.txt" in detail and "RUDRA can calculate with this equation." in detail


def test_the_knowledge_page_says_so_when_nothing_is_stored_yet(window):
    window.full.select("knowledge")
    _wait(window)
    assert window.full.pages["knowledge"].summary.cget("text") == "There is no knowledge yet. Add a document first."


def test_a_page_that_fills_the_window_hides_the_result_area_and_the_others_bring_it_back(window):
    """The Knowledge list gets the whole window; every other page shows its result underneath."""
    view = window.full
    assert view._output_shown()
    view.select("knowledge")
    assert not view._output_shown() and len(view.panes.panes()) == 1
    view.select("ask")
    assert view._output_shown() and len(view.panes.panes()) == 2
    view.select("settings")
    assert view._output_shown()


def test_the_ask_page_keeps_its_extra_options_out_of_the_way(window):
    page = window.full.pages["ask"]
    assert page.options_open is False and not page.options.winfo_manager()
    page.toggle_options()
    assert page.options_open is True and page.options.winfo_manager() == "pack"
    assert "More options" in page.options_toggle.cget("text")
    page.toggle_options()
    assert not page.options.winfo_manager()


def test_the_everyday_pages_come_before_the_advanced_ones_in_the_sidebar(window):
    order = list(window.full.nav)
    advanced = [key for key in order if window.full.pages[key].advanced]
    everyday = [key for key in order if not window.full.pages[key].advanced]
    assert order == everyday + advanced and everyday[0] == "ask"
    assert "calculate" not in order


def test_a_page_too_tall_for_the_window_gets_a_scrollbar_and_otherwise_none(window, monkeypatch):
    """Settings (the tallest page) must never be clipped: it scrolls when the window is short."""
    window.full.select("settings")
    view = window.full
    window.root.update_idletasks()
    needed = view.body.winfo_reqheight()
    assert needed > 100  # the page really has content
    monkeypatch.setattr(view.canvas, "winfo_height", lambda: needed - 40)
    view._resized()
    assert view.scrollbar.winfo_manager() == "pack"
    monkeypatch.setattr(view.canvas, "winfo_height", lambda: needed + 200)
    view._resized()
    assert view.scrollbar.winfo_manager() == ""


def test_an_enabled_provider_without_a_stored_key_sends_nothing(window, monkeypatch):
    from app.providers import credentials, settings
    from app.providers.registry import ProviderError

    settings.enable(window.project_root / "config", "mistral", "test-model", consent=True)
    monkeypatch.setattr(credentials, "read_key", lambda *a, **k: None)
    assert window.ai_active() is True
    with pytest.raises(ProviderError):
        window.ai_sender()
