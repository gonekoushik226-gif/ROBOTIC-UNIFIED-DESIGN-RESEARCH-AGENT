"""The desktop window itself (ADR 0057), built hidden: its pages, layouts and command runs.

The window is withdrawn before it is built, so nothing appears on screen. Commands run on
the window's worker thread through the command line's own entry point, in a temporary
project; the event loop is pumped until each result arrives.
"""

from __future__ import annotations

import gc
import time
import tkinter as tk

import pytest

from app.ui.gui import commands
from app.ui.gui.commands import Approval
from app.ui.gui.window import PAGES, RudraWindow
from app.version import PHASE


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
    assert list(window.full.pages) == ["status", "ask", "lookup", "provenance", "import", "settings", "backup",
                                       "ai", "command", "help"]
    assert len(PAGES) == 10
    # Backup and AI are fully working pages (Settings calls straight into them) but keep no
    # sidebar entry of their own, since Settings now offers the same capability more simply.
    assert list(window.full.nav) == ["status", "ask", "lookup", "provenance", "import", "settings", "command", "help"]
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
    assert result.argv == ("version",) and result.ok and PHASE in result.stdout
    shown = window.full.output.text_content()
    assert shown.startswith("› python -m app version") and PHASE in shown and "ok · ok ·" in shown
    assert PHASE in window.compact.output.text_content()
    assert window.full.state.cget("text") == "READY · version done"


def test_calculation_is_asked_for_through_ask_not_a_separate_page(window):
    """The master specification: no standalone Calculate page: calculation happens through
    Ask, naturally, when the question states the formula and the values (N2 is unchanged -
    RUDRA still never chooses a formula on its own)."""
    assert "calculate" not in window.full.pages and "calculate" not in window.full.nav
    window.full.pages["ask"].question.set(
        "Calculate I given I = V / Rtotal, Rtotal = R1 + R2, R1 = 10 Ω, R2 = 20 Ω, V = 10 V")
    assert window.full.pages["ask"].run()
    result = _wait(window)
    assert result.argv[0] == "ask" and result.ok
    assert '"status": "CALCULATED"' in result.stdout and '"displayed": "0.333333"' in result.stdout


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
    assert not window.full.pages["import"].database()
    assert [approval.title for approval in asked] == ["Create or upgrade the database"]
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
                        lambda seconds, cancel=None: app.voice.Transcript("hello", "dictation", 0.8, "x", "mic"))
    results = []
    window.listen(results.append)
    _wait(window)
    assert len(results) == 1 and results[0].text == "hello" and results[0].error is None


def test_window_listen_reports_no_speech_in_plain_language(window, monkeypatch):
    import app.voice

    monkeypatch.setattr(app.voice, "listen",
                        lambda seconds, cancel=None: app.voice.Transcript("", "", None, "x", "mic"))
    results = []
    window.listen(results.append)
    _wait(window)
    assert results[0].text == "" and "did not hear anything" in results[0].error


def test_window_listen_reports_an_unavailable_engine_in_plain_language(window, monkeypatch):
    import app.voice

    def fail(seconds, cancel=None):
        raise app.voice.SpeechUnavailable("no microphone found")

    monkeypatch.setattr(app.voice, "listen", fail)
    results = []
    window.listen(results.append)
    _wait(window)
    assert "no microphone found" in results[0].error


def test_window_listen_cancellation_is_silent(window, monkeypatch):
    import app.voice

    def cancelled(seconds, cancel=None):
        raise app.voice.SpeechCancelled("stopped")

    monkeypatch.setattr(app.voice, "listen", cancelled)
    results = []
    window.listen(results.append)
    _wait(window)
    assert results[0].text == "" and results[0].error == ""  # cancelled, not an error to show


def test_the_asks_mic_button_fills_the_question_field_for_the_user_to_edit(window, monkeypatch):
    import app.voice

    monkeypatch.setattr(app.voice, "listen", lambda seconds, cancel=None: app.voice.Transcript(
        "What is resistance?", "dictation", 0.8, "x", "mic"))
    page = window.full.pages["ask"]
    page.mic.button.invoke()
    _wait(window)
    assert page.question.get() == "What is resistance?"
    assert page.mic.button.cget("text") == "Speak"  # back to idle, ready to edit and press Ask


# ------------------------------------------------------------------ Settings: the home for maintenance


def test_settings_has_no_calculate_and_hides_backup_and_ai_from_the_sidebar(window):
    assert list(window.full.nav) == ["status", "ask", "lookup", "provenance", "import", "settings", "command",
                                     "help"]
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
                        lambda seconds, cancel=None: app.voice.Transcript("testing", "dictation", 0.8, "x", "mic"))
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


def test_an_enabled_provider_without_a_stored_key_sends_nothing(window, monkeypatch):
    from app.providers import credentials, settings
    from app.providers.registry import ProviderError

    settings.enable(window.project_root / "config", "mistral", "test-model", consent=True)
    monkeypatch.setattr(credentials, "read_key", lambda *a, **k: None)
    assert window.ai_active() is True
    with pytest.raises(ProviderError):
        window.ai_sender()
