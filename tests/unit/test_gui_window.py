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
    assert list(window.full.pages) == ["status", "ask", "lookup", "calculate", "provenance", "import", "backup",
                                       "ai", "command", "help"]
    assert len(PAGES) == 10 and list(window.full.nav) == list(window.full.pages)
    for key in window.full.pages:
        window.full.select(key)
        assert window.full.selected == key and window.full.nav[key].active
        assert window.full.page_title.cget("text") == window.full.pages[key].heading


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


def test_the_calculate_page_sends_its_example_to_the_command_line(window):
    assert window.full.pages["calculate"].run()
    result = _wait(window)
    assert result.argv[:4] == ("calculate", "I", "--formula", "I = V / Rtotal")
    assert result.ok and "exact value 1/3" in result.stdout


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


def test_an_enabled_provider_without_a_stored_key_sends_nothing(window, monkeypatch):
    from app.providers import credentials, settings
    from app.providers.registry import ProviderError

    settings.enable(window.project_root / "config", "mistral", "test-model", consent=True)
    monkeypatch.setattr(credentials, "read_key", lambda *a, **k: None)
    assert window.ai_active() is True
    with pytest.raises(ProviderError):
        window.ai_sender()
