"""Phase 14: the reusable action engine (ADR 0046 P14-1 ... P14-12).

What every test holds the engine to: section 208's nineteen actions, one implementation
each; applications resolved from the registry, never named in action code; parameters
validated and paths confined before anything is planned; preconditions that block;
postconditions verified, and effects that cannot be observed reported INCONCLUSIVE, never
success; one bounded recovery for a LOW-risk launch; a plan with section 103's fields and
no added step; a failed step stopping the plan; dry runs that change nothing; the audit
line. The simulated computer is in memory; the live desktop and disk are never touched.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.actions import CATALOGUE, ActionEngine, SimulatedPlatform, StepStatus, to_json
from app.actions import catalogue as catalogue_module
from app.actions import png
from app.actions.safety import UnsafeValue, bare_name, confined_path, key_combination, key_name
from app.applications import APPLICATIONS, find
from app.core.errors import InvalidInputError
from app.models.enums import RiskLevel
from app.nlu.lexicon import APPLICATIONS as NLU_APPLICATIONS
from tests.conftest import PROJECT_ROOT

SECTION_208 = ("OPEN_APPLICATION", "CLOSE_APPLICATION", "OPEN_FILE", "CREATE_FILE", "SAVE_FILE", "MOVE_FILE",
               "COPY_FILE", "RENAME_FILE", "CREATE_FOLDER", "TYPE_TEXT", "PRESS_KEY", "HOTKEY", "CLICK",
               "DOUBLE_CLICK", "RIGHT_CLICK", "SCROLL", "COPY", "PASTE", "TAKE_SCREENSHOT")


def _run(platform, *requests):
    engine = ActionEngine(platform)
    return engine.run(engine.plan(list(requests)))


def _sim(**options):
    return SimulatedPlatform(folders=("C:/sim",), **options)


# ------------------------------------------------------------ section 209


def test_notepad_and_calculator_open_through_the_same_implementation():
    """Section 209: the same action implementation, different parameters."""
    definition = CATALOGUE["OPEN_APPLICATION"]
    engine = ActionEngine(_sim())
    notepad = engine.plan([("OPEN_APPLICATION", {"application": "Notepad"})])
    calculator = engine.plan([("OPEN_APPLICATION", {"application": "Calculator"})])
    assert notepad.steps[0].action == calculator.steps[0].action == "OPEN_APPLICATION"
    assert CATALOGUE[notepad.steps[0].action].run is CATALOGUE[calculator.steps[0].action].run is definition.run
    launch = dict(notepad.steps[0].resolved)["launch methods, in order"]
    assert launch == r"SYSTEM_PATH %SystemRoot%\System32\notepad.exe; APP_PATH notepad.exe"
    assert dict(calculator.steps[0].resolved)["launch methods, in order"] == (
        r"PROTOCOL calculator:; SYSTEM_PATH %SystemRoot%\System32\calc.exe")
    for plan, title in ((notepad, "Untitled - Notepad"), (calculator, "Calculator")):
        report = engine.run(plan)
        assert report.status is StepStatus.VERIFIED and report.dry_run
        assert f"'{title}'" in report.steps[0].observed


def test_no_application_is_named_in_action_code():
    source = inspect.getsource(catalogue_module)
    for app in APPLICATIONS:
        assert f'"{app.name}"' not in source and f"'{app.name}'" not in source


def test_the_catalogue_is_exactly_section_208s_nineteen_with_declared_risk():
    assert set(CATALOGUE) == set(SECTION_208)
    low = {"OPEN_APPLICATION", "OPEN_FILE", "CREATE_FILE", "COPY_FILE", "CREATE_FOLDER", "TYPE_TEXT", "TAKE_SCREENSHOT"}
    assert {n for n, d in CATALOGUE.items() if d.risk is RiskLevel.LOW} == low
    assert all(d.risk is not RiskLevel.HIGH for d in CATALOGUE.values())
    assert all(d.preconditions and d.expected and d.verification for d in CATALOGUE.values())


# ------------------------------------------------------------ every action


def test_every_action_runs_through_the_engine_with_an_honest_outcome():
    report = _run(
        _sim(),
        ("CREATE_FILE", {"path": "C:/sim/a.txt", "content": "hello"}),
        ("COPY_FILE", {"source": "C:/sim/a.txt", "destination": "C:/sim/b.txt"}),
        ("RENAME_FILE", {"source": "C:/sim/b.txt", "new_name": "c.txt"}),
        ("CREATE_FOLDER", {"path": "C:/sim/d"}),
        ("MOVE_FILE", {"source": "C:/sim/c.txt", "destination": "C:/sim/d/c.txt"}),
        ("TAKE_SCREENSHOT", {"path": "C:/sim/s.png"}),
        ("OPEN_APPLICATION", {"application": "notepad"}),
        ("TYPE_TEXT", {"text": "hi"}), ("HOTKEY", {"keys": "ctrl+shift+s"}), ("COPY", {}),
        ("CLICK", {"x": "10", "y": "20"}), ("DOUBLE_CLICK", {"x": "1", "y": "1"}), ("RIGHT_CLICK", {"x": "1", "y": "1"}),
        ("SCROLL", {"direction": "down"}), ("PRESS_KEY", {"key": "Enter"}), ("PASTE", {}), ("SAVE_FILE", {}),
        ("OPEN_FILE", {"path": "C:/sim/a.txt"}), ("CLOSE_APPLICATION", {"application": "Notepad"}),
    )
    statuses = {r.action: r.status for r in report.steps}
    assert set(statuses) == set(SECTION_208)
    verified = {"CREATE_FILE", "COPY_FILE", "RENAME_FILE", "CREATE_FOLDER", "MOVE_FILE", "TAKE_SCREENSHOT",
                "OPEN_APPLICATION", "COPY", "OPEN_FILE", "CLOSE_APPLICATION"}
    assert {a for a, s in statuses.items() if s is StepStatus.VERIFIED} == verified
    assert {a for a, s in statuses.items() if s is StepStatus.INCONCLUSIVE} == set(SECTION_208) - verified
    assert report.status is StepStatus.INCONCLUSIVE  # never reported as plain success


def test_file_postconditions_are_checked_by_content():
    platform = _sim()
    _run(platform, ("CREATE_FILE", {"path": "C:/sim/a.txt", "content": "héllo"}),
         ("MOVE_FILE", {"source": "C:/sim/a.txt", "destination": "C:/sim/b.txt"}))
    assert platform.read_bytes("C:/sim/b.txt") == "héllo".encode() and not platform.exists("C:/sim/a.txt")
    shot = _run(platform, ("TAKE_SCREENSHOT", {"path": "C:/sim/x.png"}))
    assert shot.steps[0].status is StepStatus.VERIFIED
    assert png.dimensions(platform.read_bytes("C:/sim/x.png")) == (1920, 1080)


# ---------------------------------------------------------- preconditions


@pytest.mark.parametrize(
    "request_",
    [
        ("CREATE_FILE", {"path": "C:/elsewhere/a.txt"}),                       # no parent folder
        ("COPY_FILE", {"source": "C:/sim/none.txt", "destination": "C:/sim/b.txt"}),  # no source
        ("TYPE_TEXT", {"text": "hi"}),                                          # no focused window
        ("CLICK", {"x": "5000", "y": "5"}),                                     # off the screen
        ("CLOSE_APPLICATION", {"application": "Notepad"}),                      # nothing open
    ],
)
def test_a_failing_precondition_blocks_and_nothing_is_done(request_):
    platform = _sim()
    report = _run(platform, request_)
    (step,) = report.steps
    assert step.status is StepStatus.BLOCKED and not step.executed and step.failure_stage == "PRECONDITION"
    assert platform.log == []


def test_existing_files_are_never_overwritten():
    platform = _sim()
    _run(platform, ("CREATE_FILE", {"path": "C:/sim/a.txt", "content": "one"}))
    report = _run(platform, ("CREATE_FILE", {"path": "C:/sim/a.txt", "content": "two"}))
    assert report.steps[0].status is StepStatus.BLOCKED and platform.read_bytes("C:/sim/a.txt") == b"one"


def test_two_open_windows_are_not_chosen_between():
    platform = _sim(open_windows=("Notepad", "Notepad"))
    report = _run(platform, ("CLOSE_APPLICATION", {"application": "Notepad"}))
    assert report.steps[0].status is StepStatus.BLOCKED and "does not choose" in report.steps[0].detail
    handle = platform.windows()[0].handle
    named = _run(platform, ("CLOSE_APPLICATION", {"application": "Notepad", "window": str(handle)}))
    assert named.steps[0].status is StepStatus.VERIFIED and len(platform.windows()) == 1


# ------------------------------------------------------ recovery and failure


def test_an_unverified_launch_tries_the_next_method_once():
    platform = _sim(silent=("calculator:",))
    report = _run(platform, ("OPEN_APPLICATION", {"application": "Calculator"}))
    step = report.steps[0]
    assert step.status is StepStatus.VERIFIED and len(step.attempts) == 2
    assert step.attempts[0].method == "PROTOCOL calculator:"


def test_a_launch_that_is_never_verified_fails_and_stops_the_plan():
    platform = _sim(silent=("calculator:", r"%SystemRoot%\System32\calc.exe"))
    report = _run(platform, ("OPEN_APPLICATION", {"application": "Calculator"}), ("TYPE_TEXT", {"text": "1+1"}))
    first, second = report.steps
    assert first.status is StepStatus.FAILED and "could not be verified as opened" in first.detail
    assert len(first.attempts) == 2  # bounded: one alternative, no more
    assert second.status is StepStatus.NOT_ATTEMPTED and report.status is StepStatus.FAILED


def test_an_already_open_application_is_reported_as_such_without_recovery():
    platform = _sim(open_windows=("Calculator",), silent=("calculator:",))
    report = _run(platform, ("OPEN_APPLICATION", {"application": "Calculator"}))
    step = report.steps[0]
    assert step.status is StepStatus.VERIFIED and "already open" in step.detail and len(step.attempts) == 1


def test_no_launch_method_available_blocks():
    report = _run(_sim(unavailable=("chrome.exe",)), ("OPEN_APPLICATION", {"application": "Chrome"}))
    assert report.steps[0].status is StepStatus.BLOCKED


# ---------------------------------------------------------- invalid requests


@pytest.mark.parametrize(
    "request_",
    [
        ("LAUNCH_ROCKET", {}),
        ("OPEN_APPLICATION", {"application": "Excel"}),
        ("OPEN_APPLICATION", {}),
        ("OPEN_APPLICATION", {"application": "Notepad", "extra": "1"}),
        ("CREATE_FILE", {"path": "relative.txt"}),
        ("CREATE_FILE", {"path": r"\\server\share\a.txt"}),
        ("CREATE_FILE", {"path": "C:/sim/../a.txt"}),
        ("CREATE_FILE", {"path": "C:/sim/CON.txt"}),
        ("RENAME_FILE", {"source": "C:/sim/a.txt", "new_name": "x/y.txt"}),
        ("PRESS_KEY", {"key": "launchkey"}),
        ("HOTKEY", {"keys": "s"}),
        ("SCROLL", {"direction": "sideways"}),
        ("CLICK", {"x": "-1", "y": "3"}),
        ("TAKE_SCREENSHOT", {"path": "C:/sim/shot.jpg"}),
    ],
)
def test_an_invalid_request_is_refused_before_anything_is_planned(request_):
    with pytest.raises(InvalidInputError) as caught:
        ActionEngine(_sim()).plan([request_])
    assert caught.value.report.data_changed is False


def test_safety_rules():
    assert confined_path("C:/a/b.txt") == r"C:\a\b.txt"
    assert bare_name("notes.txt") == "notes.txt"
    assert key_name("ESC") == "escape" and key_combination("Ctrl+Shift+S") == ("ctrl", "shift", "s")
    for bad in ("C:relative.txt", "C:/a/<b>.txt", "C:/a/nul"):
        with pytest.raises(UnsafeValue):
            confined_path(bad)
    with pytest.raises(UnsafeValue):
        key_combination("ctrl+ctrl+s")


# -------------------------------------------------------- plans, dry run, audit


def test_a_plan_carries_section_103s_fields_and_adds_no_step():
    plan = ActionEngine(_sim()).plan([("OPEN_APPLICATION", {"application": "Chrome"})])
    (step,) = plan.steps
    assert (step.action, step.parameters, step.risk_level) == ("OPEN_APPLICATION", (("application", "Chrome"),), RiskLevel.LOW)
    assert step.preconditions and step.expected and step.verification and not plan.live


def test_a_dry_run_reads_the_real_disk_and_writes_nothing(tmp_path):
    platform = SimulatedPlatform(disk=True)
    target = tmp_path / "made.txt"
    report = _run(platform, ("CREATE_FILE", {"path": str(target), "content": "x"}))
    assert report.steps[0].status is StepStatus.VERIFIED and report.dry_run
    assert not target.exists() and list(tmp_path.iterdir()) == []
    assert "no changes have been made" in report.notes[0]


def test_reports_are_deterministic():
    first = to_json(_run(_sim(), ("OPEN_APPLICATION", {"application": "Notepad"})))
    assert to_json(_run(_sim(), ("OPEN_APPLICATION", {"application": "Notepad"}))) == first


def test_every_step_writes_one_audit_line():
    """A handler on the audit logger itself: other tests configure logging, and the
    `rudra` logger then does not propagate to pytest's capture."""
    import logging

    records: list[logging.LogRecord] = []

    class Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("rudra.app.actions")
    handler, level = Grab(level=1), logger.level
    logger.addHandler(handler)
    logger.setLevel(1)
    try:
        _run(_sim(), ("OPEN_APPLICATION", {"application": "Notepad"}), ("TYPE_TEXT", {"text": "x"}))
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
    assert len(records) == 2 and '"action": "OPEN_APPLICATION"' in records[0].getMessage()


# ------------------------------------------------------------- the registry


def test_the_registry_holds_phase_0s_applications_and_agrees_with_the_interpreter():
    assert {a.name for a in APPLICATIONS} == set(NLU_APPLICATIONS)
    for app in APPLICATIONS:
        assert set(NLU_APPLICATIONS[app.name]) <= set(app.aliases) | {app.name.casefold()}
        assert app.launch and app.detection.matches(app.detection.process_images[0], app.detection.sample_title)
    assert find("CALC").name == "Calculator" and find("excel") is None
    assert find("matlab").kind == "WEB_APP"


def test_reasoning_cannot_import_the_action_engine():
    """Section 117: the reasoning engine never reaches actions (the boundary rule, checked here too)."""
    for path in (PROJECT_ROOT / "app" / "reasoning").glob("*.py"):
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
        assert not any(m.startswith(("app.actions", "app.applications")) for m in modules)
