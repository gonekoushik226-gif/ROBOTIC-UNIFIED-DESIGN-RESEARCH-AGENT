"""Typed and pasted text is verified by reading the focused edit field back.

VERIFIED only when the field holds the text once more than before; FAILED when the field
could be read and the text is not in it; INCONCLUSIVE when the field cannot be read, the
focus moved, or the text was already there. Keys, clicks and scrolling have no such
evidence and stay INCONCLUSIVE. The field's contents never appear in a report. The
Windows reader is tested against a hidden edit control created by this process; no input
is sent to the desktop.
"""

from __future__ import annotations

import ctypes
import sys

import pytest

from app.actions import ActionEngine, FocusedText, SimulatedPlatform, StepStatus


def _run(platform, *requests):
    engine = ActionEngine(platform)
    return engine.run(engine.plan([("OPEN_APPLICATION", {"application": "Notepad"}), *requests]))


def _step(platform, request):
    report = _run(platform, request)
    assert report.steps[0].status is StepStatus.VERIFIED
    return report.steps[1]


class _Dropping(SimulatedPlatform):
    """A readable field that loses every typed character."""

    def send_text(self, text: str) -> None:
        self.log.append("typed, but nothing arrived")


class _Crlf(SimulatedPlatform):
    """A field that stores the Enter key as CR LF, as a Windows edit field does."""

    def send_text(self, text: str) -> None:
        super().send_text(text.replace("\n", "\r\n"))


class _Replacing(SimulatedPlatform):
    """A field whose selected text is replaced by identical text: nothing observable changes."""

    def send_text(self, text: str) -> None:
        self.log.append("replaced the selection with the same text")


class _FocusMoves(SimulatedPlatform):
    """Typing moves the focus to another control (a Tab, a dialog's Enter)."""

    moved = False

    def send_text(self, text: str) -> None:
        super().send_text(text)
        self.moved = True

    def focused_text(self):
        found = super().focused_text()
        if found is None or not self.moved:
            return found
        return FocusedText(found.handle + 99, "Edit", "")


def test_typed_text_that_arrives_in_the_focused_field_is_verified():
    platform = SimulatedPlatform(field="Dear team,\n")
    step = _step(platform, ("TYPE_TEXT", {"text": "secret words"}))
    assert step.status is StepStatus.VERIFIED and step.failure_stage is None
    assert "read back" in step.observed and "12 characters" in step.observed
    assert platform.field == "Dear team,\nsecret words"
    # The field's contents are used for the comparison only - never reported.
    for text in (step.observed, step.detail, *(str(a) for a in step.attempts)):
        assert "secret" not in text and "Dear team" not in text


def test_typed_text_that_never_arrives_fails_verification_and_stops_the_plan():
    platform = _Dropping(field="")
    report = _run(platform, ("TYPE_TEXT", {"text": "hello"}), ("PRESS_KEY", {"key": "Enter"}))
    typed, after = report.steps[1], report.steps[2]
    assert typed.status is StepStatus.FAILED and typed.failure_stage == "VERIFICATION"
    assert "does not hold" in typed.observed and "hello" not in typed.observed
    assert after.status is StepStatus.NOT_ATTEMPTED and report.status is StepStatus.FAILED


def test_an_unreadable_field_is_inconclusive_never_verified():
    step = _step(SimulatedPlatform(), ("TYPE_TEXT", {"text": "hello"}))
    assert step.status is StepStatus.INCONCLUSIVE
    assert "not a standard edit field" in step.detail


def test_text_that_was_already_in_the_field_cannot_be_told_apart():
    step = _step(_Replacing(field="hello"), ("TYPE_TEXT", {"text": "hello"}))
    assert step.status is StepStatus.INCONCLUSIVE and "already in the field" in step.detail


def test_a_second_copy_of_text_already_present_is_verified():
    platform = SimulatedPlatform(field="hello ")
    step = _step(platform, ("TYPE_TEXT", {"text": "hello"}))
    assert step.status is StepStatus.VERIFIED and platform.field == "hello hello"


def test_focus_moving_away_makes_the_check_inconclusive():
    step = _step(_FocusMoves(field=""), ("TYPE_TEXT", {"text": "hello"}))
    assert step.status is StepStatus.INCONCLUSIVE and "focus moved" in step.detail


def test_line_breaks_are_compared_as_the_field_stores_them():
    platform = _Crlf(field="")
    step = _step(platform, ("TYPE_TEXT", {"text": "line one\nline two"}))
    assert platform.field == "line one\r\nline two" and step.status is StepStatus.VERIFIED


def test_paste_is_verified_against_the_clipboard_text():
    platform = SimulatedPlatform(field="")
    platform.clipboard = "copied text"
    step = _step(platform, ("PASTE", {}))
    assert step.status is StepStatus.VERIFIED and platform.field == "copied text"
    assert "copied" not in step.observed


def test_paste_with_an_empty_clipboard_is_inconclusive():
    step = _step(SimulatedPlatform(field="abc"), ("PASTE", {}))
    assert step.status is StepStatus.INCONCLUSIVE and "no text to look for" in step.detail


def test_paste_into_an_unreadable_field_is_inconclusive():
    platform = SimulatedPlatform()
    platform.clipboard = "copied text"
    assert _step(platform, ("PASTE", {})).status is StepStatus.INCONCLUSIVE


@pytest.mark.parametrize("request_", [("PRESS_KEY", {"key": "Enter"}), ("HOTKEY", {"keys": "ctrl+a"}),
                                      ("CLICK", {"x": "5", "y": "5"}), ("SCROLL", {"direction": "down"})])
def test_keys_clicks_and_scrolling_stay_inconclusive_even_with_a_readable_field(request_):
    assert _step(SimulatedPlatform(field="abc"), request_).status is StepStatus.INCONCLUSIVE


# ----------------------------------------------------------- the Windows reader

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="reads a real Windows edit control")


def _control(class_name: str, style: int = 0):
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    # Never shown (no WS_VISIBLE): nothing appears on the desktop and no input is sent.
    hwnd = user32.CreateWindowExW(0, class_name, None, style, 0, 0, 200, 50, None, None, None, None)
    assert hwnd, ctypes.get_last_error()
    return user32, hwnd


@windows_only
@pytest.mark.parametrize("class_name", ["Edit", "RichEdit20W"])
def test_the_windows_reader_reads_a_standard_edit_field(class_name):
    from app.computer.windows import read_edit

    library = ctypes.WinDLL("riched20") if class_name.startswith("RichEdit") else None  # registers RichEdit20W
    assert library is not None or class_name == "Edit"
    user32, hwnd = _control(class_name, 0x0004)  # ES_MULTILINE
    try:
        user32.SetWindowTextW(hwnd, "first line\r\nsecond line")
        found = read_edit(hwnd)
        assert found is not None and found.handle == hwnd
        assert found.text.replace("\r\n", "\n") == "first line\nsecond line"
    finally:
        user32.DestroyWindow(hwnd)


@windows_only
def test_the_windows_reader_never_reads_a_password_field():
    from app.computer.windows import read_edit

    user32, hwnd = _control("Edit", 0x0020)  # ES_PASSWORD
    try:
        user32.SetWindowTextW(hwnd, "hunter2")
        assert read_edit(hwnd) is None
    finally:
        user32.DestroyWindow(hwnd)


@windows_only
def test_the_windows_reader_ignores_controls_that_are_not_edit_fields():
    from app.computer.windows import read_edit

    user32, hwnd = _control("Static")
    try:
        user32.SetWindowTextW(hwnd, "a label")
        assert read_edit(hwnd) is None
    finally:
        user32.DestroyWindow(hwnd)
