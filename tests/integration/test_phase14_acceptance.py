"""Phase 14 acceptance: Part 5 section 209 through the `act` command (ADR 0046 P14-14).

    Demonstrate OPEN_APPLICATION("Notepad") and OPEN_APPLICATION("Calculator") using the
    same action implementation with different parameters.

Each run is one `python -m app act` process in a temporary project root. In Phase 14 every
run is a **dry run** on the simulated computer (P14-1, P14-9): nothing is started on the
live desktop, and each report says so. That both requests use the same implementation is
asserted by identity in `tests/unit/test_actions.py`; here the command shows the same
action, planned and verified, with the registry resolving different launch data.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from app.ui.cli import main as cli_main
from tests.conftest import PROJECT_ROOT


def cli(root, *args: str) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUDRA_")}
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, "-m", "app", *args, "--project-root", str(root)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(PROJECT_ROOT), timeout=240,
    )
    return done.returncode, done.stdout, done.stderr


def _act(root, *args) -> dict:
    code, out, err = cli(root, "act", *args, "--json")
    assert code == 0, err
    return json.loads(out)


def test_the_command_set_gains_exactly_act():
    before = {"start", "env", "config", "paths", "db", "extract", "classify", "lookup", "review", "edition",
              "merge", "query", "index", "reason", "calculate", "provenance", "interpret", "version"}
    # Later phases add their own commands (ADR 0047 onward); Phase 14's own addition is unchanged.
    assert set(cli_main._COMMANDS) - {"do", "procedure", "manual", "research", "diagram", "voice", "ask", "source"} == before | {"act"}


def test_open_application_for_notepad_and_calculator(tmp_path):
    notepad = _act(tmp_path, "OPEN_APPLICATION", "--param", "application=Notepad")
    calculator = _act(tmp_path, "OPEN_APPLICATION", "--param", "application=Calculator")

    for report, title in ((notepad, "Untitled - Notepad"), (calculator, "Calculator")):
        (step,) = report["plan"]["steps"]
        (result,) = report["steps"]
        assert step["action"] == "OPEN_APPLICATION" and step["risk_level"] == "LOW"
        assert result["status"] == "VERIFIED" and f"'{title}'" in result["observed"]
        assert report["dry_run"] is True and "no changes have been made" in report["notes"][0]
    # The same action; only the parameter and the registry's resolution differ.
    n_step, c_step = notepad["plan"]["steps"][0], calculator["plan"]["steps"][0]
    assert {k: v for k, v in n_step.items() if k not in ("parameters", "resolved")} == \
           {k: v for k, v in c_step.items() if k not in ("parameters", "resolved")}
    assert n_step["parameters"] == [["application", "Notepad"]]
    assert c_step["parameters"] == [["application", "Calculator"]]
    n_launch = dict(map(tuple, n_step["resolved"]))["launch methods, in order"]
    c_launch = dict(map(tuple, c_step["resolved"]))["launch methods, in order"]
    assert "notepad.exe" in n_launch and c_launch.startswith("PROTOCOL calculator:")


def test_the_text_report_states_the_dry_run(tmp_path):
    code, out, _ = cli(tmp_path, "act", "OPEN_APPLICATION", "--param", "application=Notepad")
    assert code == 0
    assert out.startswith("Actions     : DRY RUN on the simulated computer - no changes have been made")
    assert "OPEN_APPLICATION(application=Notepad) - risk LOW - VERIFIED" in out


def test_a_dry_run_changes_nothing_on_disk(tmp_path):
    target = tmp_path / "made-by-dry-run.txt"
    report = _act(tmp_path, "CREATE_FILE", "--param", f"path={target}", "--param", "content=hello")
    assert report["status"] == "VERIFIED" and report["dry_run"] is True
    assert not target.exists()


@pytest.mark.parametrize(
    "args",
    [("act",), ("act", "LAUNCH_ROCKET"), ("act", "OPEN_APPLICATION", "--param", "application=Excel"),
     ("act", "OPEN_APPLICATION", "--param", "application"), ("act", "OPEN_APPLICATION", "--name", "x"),
     ("act", "CREATE_FILE", "--param", "path=relative.txt")],
)
def test_an_invalid_request_is_exit_code_2(tmp_path, args):
    code, _, err = cli(tmp_path, *args)
    assert code == 2, err


def test_earlier_commands_refuse_param(tmp_path):
    assert cli(tmp_path, "reason", "X", "--param", "a=b")[0] == 2
    assert cli(tmp_path, "interpret", "Open Chrome", "--param", "a=b")[0] == 2
