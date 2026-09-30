"""Phase 15: the Windows adapter, the permission engine and the request pipeline (ADR 0047).

What every test holds Phase 15 to: section 211's stages in order; "if verification fails,
report failure"; a MEDIUM step refused without confirmation and permitted with it; nothing
HIGH enabled; a plan with a refused step running nothing; nothing executed for an
ambiguous, incomplete or action-less request; only what was asked. The live desktop is
never acted on here (section 168; P15-11): the pipeline runs on the simulated computer,
and the Windows adapter is tested only by read-only inspection and by file operations in
pytest's temporary folders. Input events are built, never sent.
"""

from __future__ import annotations

import ctypes
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.actions import ActionEngine, SimulatedPlatform, StepStatus
from app.applications import LaunchKind, LaunchMethod, find
from app.computer.windows import (
    INPUT,
    WindowsPlatform,
    app_path,
    key_inputs,
    protocol_registered,
    text_inputs,
)
from app.core.errors import InvalidInputError
from app.models.enums import RiskLevel
from app.orchestration import Outcome, Pipeline
from app.security import Decision, decide

FIXED = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


class _LiveLike(SimulatedPlatform):
    """A simulated computer the permission engine treats as live."""

    live = True


def _pipeline(platform, tmp_path) -> Pipeline:
    return Pipeline(platform, screenshots=tmp_path, clock=lambda: FIXED)


# ------------------------------------------------------------ section 211


def test_open_calculator_passes_through_every_stage_in_order(tmp_path):
    report = _pipeline(_LiveLike(), tmp_path).run("Open Calculator.")
    assert report.outcome is Outcome.DONE
    assert [s.name for s in report.stages] == ["INTERPRET", "PLAN", "VALIDATE", "PERMISSION", "EXECUTE",
                                              "VERIFY", "REPORT"]
    assert report.permissions[0].decision is Decision.PERMITTED
    (step,) = report.execution.steps
    assert step.action == "OPEN_APPLICATION" and step.status is StepStatus.VERIFIED
    assert "Calculator" in step.observed


def test_if_verification_fails_the_failure_is_reported():
    """Section 211: "If verification fails, report failure." - never success."""
    platform = _LiveLike(silent=("calculator:", r"%SystemRoot%\System32\calc.exe"))
    report = _pipeline(platform, Path(".")).run("Open Calculator.")
    assert report.outcome is Outcome.FAILED
    assert report.message.startswith("ERROR: Calculator could not be verified as opened")
    assert report.stages[-2].name == "VERIFY" and report.stages[-2].outcome == "FAILED"


# ------------------------------------------------------------ permission


def test_a_medium_step_needs_confirmation(tmp_path):
    platform = _LiveLike(open_windows=("Notepad",))
    refused = _pipeline(platform, tmp_path).run("Close Notepad.")
    assert refused.outcome is Outcome.REFUSED and refused.execution is None
    assert "--confirm" in refused.permissions[0].reason and len(platform.windows()) == 1
    permitted = _pipeline(platform, tmp_path).run("Close Notepad.", confirmed=True)
    assert permitted.outcome is Outcome.DONE and platform.windows() == ()


def test_the_permission_policy_by_risk():
    engine = ActionEngine(_LiveLike())
    plan = engine.plan([("OPEN_APPLICATION", {"application": "Notepad"}), ("PRESS_KEY", {"key": "enter"})])
    low, medium = decide(plan, confirmed=False)
    assert (low.decision, medium.decision) == (Decision.PERMITTED, Decision.REFUSED)
    assert all(d.decision is Decision.PERMITTED for d in decide(plan, confirmed=True))
    dry = ActionEngine(SimulatedPlatform()).plan([("PRESS_KEY", {"key": "enter"})])
    assert decide(dry, confirmed=False)[0].decision is Decision.NOT_REQUIRED


def test_a_plan_with_one_refused_step_runs_nothing(tmp_path):
    platform = _LiveLike()
    report = _pipeline(platform, tmp_path).run("Open Notepad and press enter")
    assert report.outcome is Outcome.REFUSED and platform.log == []


# --------------------------------------------------- nothing beyond the request


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Open the project.", "needs more information"),
        ("Frobnicate the widget", "needs more information"),
        ("Delete notes.txt", "deletion is HIGH risk"),
        ("Search the web for MOSFETs", "python -m app research"),  # Phase 18 (ADR 0050)
        ("Create a project called amplifier", "documented workflow"),  # Phase 17 (ADR 0049)
    ],
)
def test_requests_that_cannot_be_carried_out_execute_nothing(tmp_path, text, reason):
    platform = _LiveLike()
    report = _pipeline(platform, tmp_path).run(text)
    assert report.outcome is Outcome.NOT_EXECUTED and reason in report.message
    assert report.execution is None and platform.log == []


def test_a_knowledge_request_is_reported_with_its_command_not_run(tmp_path):
    report = _pipeline(_LiveLike(), tmp_path).run("What is voltage?")
    assert report.outcome is Outcome.NOT_EXECUTED
    assert report.reported_commands == ("python -m app query --name voltage",)


def test_a_screenshot_request_writes_to_the_screenshots_folder(tmp_path):
    platform = _LiveLike(folders=(str(tmp_path),))
    report = _pipeline(platform, tmp_path).run("Take a screenshot")
    assert report.outcome is Outcome.DONE
    assert report.plan.steps[0].parameters == (("path", str(tmp_path / "screenshot-20260926T120000Z.png")),)


def test_a_relative_path_is_refused_never_guessed(tmp_path):
    with pytest.raises(InvalidInputError):
        _pipeline(_LiveLike(), tmp_path).run("Create a file called notes.txt")


def test_the_compound_request_executes_its_action_and_reports_its_calculation(tmp_path):
    report = _pipeline(_LiveLike(), tmp_path).run("Open Calculator and calculate 123 × 456")
    assert report.outcome is Outcome.DONE and len(report.plan.steps) == 1
    assert report.reported_commands == ('python -m app calculate value --formula "value = 123 × 456"',)


# ------------------------------------------------ the Windows adapter, read-only


def test_the_adapter_inspects_windows_read_only():
    platform = WindowsPlatform()
    windows = platform.windows()
    assert isinstance(windows, tuple)
    for window in windows:
        assert isinstance(window.handle, int) and window.title and isinstance(window.pid, int)
    width, height = platform.screen_size()
    assert width > 0 and height > 0
    assert platform.live is True


def test_launch_methods_resolve_from_the_real_machine_without_launching():
    platform = WindowsPlatform()
    notepad = LaunchMethod(LaunchKind.SYSTEM_PATH, r"%SystemRoot%\System32\notepad.exe")
    assert platform.can_launch(notepad)[0] is True
    assert platform.can_launch(LaunchMethod(LaunchKind.SYSTEM_PATH, r"C:\no\such\program.exe"))[0] is False
    assert app_path("definitely-not-installed-rudra.exe") is None
    assert protocol_registered("definitely-not-a-protocol-rudra") is False
    assert find("Calculator").launch[0].value == "calculator:"


def test_the_adapters_file_operations_never_overwrite(tmp_path):
    platform = WindowsPlatform()
    a, b = str(tmp_path / "a.txt"), str(tmp_path / "b.txt")
    platform.write_new(a, b"one")
    with pytest.raises(FileExistsError):
        platform.write_new(a, b"two")
    platform.copy_new(a, b)
    assert Path(b).read_bytes() == b"one"
    with pytest.raises(FileExistsError):
        platform.move_new(a, b)
    platform.make_dir(str(tmp_path / "d"))
    platform.move_new(a, str(tmp_path / "d" / "a.txt"))
    assert not Path(a).exists() and (tmp_path / "d" / "a.txt").read_bytes() == b"one"


def test_live_file_actions_through_the_engine_in_a_temporary_folder(tmp_path):
    """Section 210's "create temporary test files" and "read files", live, in pytest's folder."""
    engine = ActionEngine(WindowsPlatform())
    target = tmp_path / "rudra-test.txt"
    report = engine.run(engine.plan([("CREATE_FILE", {"path": str(target), "content": "phase 15"}),
                                     ("COPY_FILE", {"source": str(target), "destination": str(tmp_path / "copy.txt")})]))
    assert report.status is StepStatus.VERIFIED and not report.dry_run
    assert target.read_text(encoding="utf-8") == "phase 15"


def test_input_events_are_built_exactly_and_never_sent_here():
    typed = text_inputs("hé")
    assert len(typed) == 4 and all(e.type == 1 for e in typed)
    assert [e.u.ki.wScan for e in typed[::2]] == [ord("h"), ord("é")]
    keys = key_inputs(("ctrl", "s"))
    assert [(e.u.ki.wVk, e.u.ki.dwFlags) for e in keys] == [(0x11, 0), (0x53, 0), (0x53, 2), (0x11, 2)]
    assert ctypes.sizeof(INPUT) in (28, 40)  # the documented size on 32- or 64-bit Windows


def test_reasoning_cannot_reach_the_computer():
    """Section 117, checked on the new packages as well as by the boundary rules."""
    import ast

    from tests.conftest import PROJECT_ROOT

    for path in (PROJECT_ROOT / "app" / "reasoning").glob("*.py"):
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
        assert not any(m.startswith(("app.computer", "app.orchestration", "app.security")) for m in modules)
    assert RiskLevel.HIGH.value == "HIGH"
