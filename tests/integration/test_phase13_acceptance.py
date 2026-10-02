"""Phase 13 acceptance: Part 5 section 207 through the `interpret` command (ADR 0045 P13-15).

    "Open Chrome."                 -> OPEN_APPLICATION, application = Chrome
    "Calculate the drain current." -> CALCULATION / KNOWLEDGE_REASONING, target = drain current
    The language layer should not perform the actual reasoning itself.

Each text is one `python -m app interpret` process in a temporary project root, and the
expectations are written by hand. The command must open no database and create none,
compute nothing, and run nothing it generates. A complete calculation's generated command
is then run separately, as a user would, and gives section 203-style exact answers - so
interpretation and calculation stay two steps.
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


def _interpret(root, text: str) -> dict:
    code, out, err = cli(root, "interpret", text, "--json")
    assert code == 0, err
    return json.loads(out)


@pytest.fixture
def root(tmp_path):
    return tmp_path / "project"


def test_the_command_set_gains_exactly_interpret():
    before = {"start", "env", "config", "paths", "db", "extract", "classify", "lookup", "review",
              "edition", "merge", "query", "index", "reason", "calculate", "provenance", "version"}
    # Phase 14 adds `act` (ADR 0046 P14-13); Phase 13's own addition is unchanged.
    assert set(cli_main._COMMANDS) - {"act", "do", "procedure", "manual", "research", "diagram", "voice", "ask", "source", "solve", "inventory"} == before | {"interpret"}


def test_open_chrome(root):
    data = _interpret(root, "Open Chrome.")
    (intent,) = data["intents"]
    assert data["status"] == "INTERPRETED"
    assert intent["intent_type"] == "OPEN_APPLICATION"
    assert intent["parameters"] == [["application", "Chrome"]] and intent["target"] == "Chrome"
    assert intent["task_classes"] == ["APPLICATION_CONTROL"]
    assert (intent["risk_level"], intent["requires_confirmation"]) == ("LOW", False)
    assert intent["action"]["action"] == "OPEN_APPLICATION" and intent["command"] is None


def test_calculate_the_drain_current_and_no_reasoning_is_done(root):
    data = _interpret(root, "Calculate the drain current.")
    (intent,) = data["intents"]
    assert data["status"] == "INCOMPLETE"
    assert intent["intent_type"] == "CALCULATE" and intent["target"] == "drain current"
    assert intent["task_classes"] == ["CALCULATION", "KNOWLEDGE_REASONING"]
    assert intent["command"] is None and intent["routes"] == [["reason", "drain current"]]
    # The language layer performed nothing: no database was opened or created.
    assert not (root / "data" / "database" / "knowledge.db").exists()
    assert list((root / "data" / "database").iterdir()) == []


def test_the_text_output_states_the_interpretation(root):
    code, out, _ = cli(root, "interpret", "Open Chrome.")
    assert code == 0
    assert 'Interpretation: "Open Chrome." - INTERPRETED' in out
    assert "OPEN_APPLICATION - INTERPRETED; classes APPLICATION_CONTROL; risk LOW" in out
    assert "action      : OPEN_APPLICATION(application=Chrome)" in out


def test_application_control_and_calculation_stay_distinct(root):
    """Section 237's compound request."""
    data = _interpret(root, "Open Calculator and calculate 123 × 456")
    first, second = data["intents"]
    assert (first["intent_type"], first["target"]) == ("OPEN_APPLICATION", "Calculator")
    assert second["intent_type"] == "CALCULATE"
    assert second["command"] == ["calculate", "value", "--formula", "value = 123 × 456"]


def test_ambiguity_and_unrecognised_text_are_answers_not_guesses(root):
    ambiguous = _interpret(root, "Open the project.")
    assert ambiguous["status"] == "AMBIGUOUS" and ambiguous["intents"][0]["action"] is None
    unknown = _interpret(root, "Frobnicate the widget.")
    assert unknown["status"] == "UNRECOGNIZED" and unknown["intents"] == [] and unknown["examples"]
    source = _interpret(root, "Where did you get this?")
    assert source["status"] == "INCOMPLETE" and source["intents"][0]["intent_type"] == "SOURCE_QUERY"


def test_a_complete_calculation_command_gives_the_hand_worked_answer_when_run(root):
    data = _interpret(root, "Calculate I given I = V / R, V = 10 V and R = 5 Ω")
    (intent,) = data["intents"]
    assert data["status"] == "INTERPRETED"
    command = intent["command"]
    assert command == ["calculate", "I", "--formula", "I = V / R", "--input", "V=10 V", "--input", "R=5 Ω"]
    # Run separately, as a user would: 10 V ÷ 5 Ω = 2 A exactly.
    code, out, err = cli(root, *command, "--json")
    assert code == 0, err
    result = json.loads(out)["result"]
    assert (result["exact"], result["relation"], result["unit"]) == ("2", "=", "A")


def test_output_is_byte_identical_across_processes(root):
    first = cli(root, "interpret", "Open Chrome and calculate 2 * 3", "--json")[1]
    assert cli(root, "interpret", "Open Chrome and calculate 2 * 3", "--json")[1] == first


@pytest.mark.parametrize("args", [("interpret",), ("interpret", "Open Chrome", "--input", "x"),
                                  ("interpret", "Open Chrome", "--scope", "authorized"),
                                  ("interpret", "Open Chrome", "--formula", "x = 1"),
                                  ("interpret", "x" * 2001)])
def test_an_invalid_request_is_exit_code_2(root, args):
    code, _, err = cli(root, *args)
    assert code == 2, err
