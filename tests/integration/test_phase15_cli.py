"""The Phase 15 commands: `do` and `act --execute` (ADR 0047 P15-10, P15-13).

In process, over temporary project roots. **Nothing here acts on the live desktop**
(section 168; P15-11): `do` runs with `--dry-run`, and `act --execute` is exercised only
where the permission engine refuses before anything runs. The live acceptance is the
explicit demonstration in `tests/live/`.

Exit codes (P15-10): 0 done; 6 the objective was not achieved; 3 the request needs more
information or holds no action; 2 invalid request; 70 unexpected.
"""

from __future__ import annotations

import json

import pytest

from app.ui.cli import main as cli_main
from app.ui.cli.main import main


def _run(root, capsys, *args) -> tuple[int, str, str]:
    code = main([*args, "--project-root", str(root)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_command_set_gains_exactly_do():
    before = {"start", "env", "config", "paths", "db", "extract", "classify", "lookup", "review", "edition",
              "merge", "query", "index", "reason", "calculate", "provenance", "interpret", "act", "version"}
    # Later phases add their own commands (ADR 0047 onward); Phase 15's own addition is unchanged.
    assert set(cli_main._COMMANDS) - {"procedure", "manual", "research", "diagram", "voice", "ask", "source"} == before | {"do"}


def test_open_calculator_dry_run_shows_every_stage(tmp_path, capsys):
    code, out, _ = _run(tmp_path, capsys, "do", "Open Calculator.", "--dry-run")
    assert code == 0
    assert out.startswith('Request     : "Open Calculator." - DRY RUN (simulated)')
    for stage in ("INTERPRET", "PLAN", "VALIDATE", "PERMISSION", "EXECUTE", "VERIFY", "REPORT"):
        assert f"\n{stage:<12}: " in out
    assert "NOT_REQUIRED - a dry run on the simulated computer changes nothing" in out


def test_json_report(tmp_path, capsys):
    code, out, _ = _run(tmp_path, capsys, "do", "Open Notepad.", "--dry-run", "--json")
    data = json.loads(out)
    assert code == 0 and data["outcome"] == "DONE" and data["live"] is False
    assert data["execution"]["steps"][0]["status"] == "VERIFIED"


@pytest.mark.parametrize(
    ("text", "code"),
    [("Open the project.", 3), ("Frobnicate the widget", 3), ("What is voltage?", 3), ("Delete notes.txt", 3)],
)
def test_requests_that_cannot_be_carried_out_exit_3(tmp_path, capsys, text, code):
    assert _run(tmp_path, capsys, "do", text, "--dry-run")[0] == code


def test_a_medium_act_is_refused_live_without_confirmation_and_nothing_runs(tmp_path, capsys):
    code, out, _ = _run(tmp_path, capsys, "act", "PRESS_KEY", "--param", "key=enter", "--execute", "--json")
    data = json.loads(out)
    assert code == 6 and data["status"] == "REFUSED"
    assert data["permissions"][0]["decision"] == "REFUSED" and "--confirm" in data["permissions"][0]["reason"]


@pytest.mark.parametrize(
    "args",
    [("do",), ("do", "Open Calculator.", "--execute"), ("do", "Open Calculator.", "--param", "a=b"),
     ("do", "Open Calculator.", "--scope", "authorized"), ("act", "OPEN_APPLICATION", "--param", "application=Notepad",
                                                          "--confirm"),
     ("act", "OPEN_APPLICATION", "--param", "application=Notepad", "--dry-run"),
     ("reason", "X", "--confirm"), ("interpret", "Open Chrome", "--dry-run")],
)
def test_invalid_requests_exit_2(tmp_path, capsys, args):
    assert _run(tmp_path, capsys, *args)[0] == 2


def test_a_relative_path_in_a_request_is_invalid(tmp_path, capsys):
    assert _run(tmp_path, capsys, "do", "Create a file called notes.txt", "--dry-run")[0] == 2
