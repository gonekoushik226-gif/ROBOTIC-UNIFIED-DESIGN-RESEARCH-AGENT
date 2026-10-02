"""Phase 22 acceptance: full integration through the `ask` command (ADR 0054 P22-12).

Section 223's architecture - text or voice, interpreted, the knowledge path and the action
path, verification, the result with provenance - and sections 227-230's responses, with
section 237's compound request. Each step is one `python -m app` process in a temporary
project root; the live database is never opened; actions run on the simulated computer
(`--dry-run`), as the suite never acts on the live desktop (ADR 0047 P15-11). Generated
pages go through the real `extract`. The expectations are written by hand.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.ui.cli import main as cli_main
from tests.conftest import PROJECT_ROOT
from tests.unit.pdf_fixtures import make_pdf

CIRCUITS = (
    "Resistance\n"
    "Resistance is defined as the opposition offered by a material to the flow of current.\n"
    "Current is defined as the rate of flow of charge.\n"
    "Resistance depends on current.\n"
)
BOOK_A = "Nominal voltage\nNominal voltage is defined as the mains voltage of 230 V.\n"
BOOK_B = "Nominal voltage\nNominal voltage is defined as the mains voltage of 240 V.\n"


def cli(root, *args: str) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUDRA_")}
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, "-m", "app", *args, "--project-root", str(root)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(PROJECT_ROOT), timeout=240,
    )
    return done.returncode, done.stdout, done.stderr


def _ask(root, *args) -> tuple[int, dict]:
    code, out, err = cli(root, "ask", *args, "--json")
    assert out, err
    return code, json.loads(out)


@pytest.fixture(scope="module")
def root(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("integration")
    project = base / "project"
    for name, page in (("circuits", CIRCUITS), ("book-a", BOOK_A), ("book-b", BOOK_B)):
        pdf = base / f"{name}.pdf"
        pdf.write_bytes(make_pdf([page]))
        code, out, err = cli(project, "extract", str(pdf))
        assert code == 0, err or out
    return project


def test_the_command_set_gains_exactly_ask():
    before = {"start", "env", "config", "paths", "db", "extract", "classify", "lookup", "review", "edition",
              "merge", "query", "index", "reason", "calculate", "provenance", "interpret", "act", "do",
              "procedure", "manual", "research", "diagram", "voice", "version"}
    # Phase 23 adds `source` (ADR 0055); Phase 22's own addition is unchanged.
    assert set(cli_main._COMMANDS) - {"source", "solve", "inventory"} == before | {"ask"}


def test_section_227_a_knowledge_answer_with_basis_sources_and_status(root):
    code, answer = _ask(root, "What is resistance?")
    (part,) = answer["parts"]
    assert code == 0 and part["path"] == "KNOWLEDGE" and part["command"] == ["query", "--name", "resistance"]
    assert part["status"] == "ANSWERED"
    assert part["answer"] == ("Definition: Resistance is defined as the opposition offered by a material to the flow "
                              "of current.")
    assert part["basis"][0].startswith("CPT-") and part["basis"][1].startswith("K-")
    assert part["sources"] and part["sources"][0].startswith("DOC-00000001 p.1:")
    # The part carries the query's own answer: the same as `query` alone.
    code, alone, _ = cli(root, "query", "--name", "resistance", "--json")
    assert part["detail"] == json.loads(alone)


def test_section_237_application_control_and_calculation_are_distinguished(root):
    code, answer = _ask(root, "Open Calculator and calculate 123 × 456", "--dry-run")
    action, calculation = answer["parts"]
    assert code == 0
    assert action["path"] == "ACTION" and action["status"] == "DONE"
    assert action["actions"][0].startswith("OPEN_APPLICATION: VERIFIED - new window 'Calculator'")
    assert calculation["path"] == "KNOWLEDGE" and calculation["intent"] == "CALCULATE"
    assert calculation["answer"] == "value = 56088"


def test_section_228_cannot_determine_with_what_is_missing_and_why(root):
    code, answer = _ask(root, "Calculate I, given I = V / R and V = 10 V")
    (part,) = answer["parts"]
    assert code == 3 and part["status"] == "CANNOT_DETERMINE"
    assert part["answer"].startswith("I cannot determine this from the currently authorized information.")
    assert (part["missing"], part["why"], part["available"]) == (["R"], ["R is required by I = V / R"], ["V = 10 V"])
    assert len(part["next_steps"]) == 3


def test_section_229_disagreeing_sources_are_not_resolved(root):
    code, out, _ = cli(root, "ask", "What is nominal voltage?")
    assert "Conflict    : Conflict detected: CON-" in out
    assert "230 V" in out and "240 V" in out and "Resolution: Not automatically selected." in out


def test_section_230_unknown_is_an_answer(root):
    code, answer = _ask(root, "What is a memristor?")
    (part,) = answer["parts"]
    assert code == 3 and part["status"] == "UNKNOWN" and part["answer"].startswith("Unknown.")


def test_reasoning_through_ask_names_the_missing_dependency(root):
    code, answer = _ask(root, "What does resistance require?")
    (part,) = answer["parts"]
    assert part["command"] == ["reason", "resistance"] and part["status"] == "CANNOT_DETERMINE"
    assert part["missing"] == ["Current (CPT-00000002)"]


def test_an_internet_search_is_never_run_from_ask(root):
    code, answer = _ask(root, "Search the web for memristors")
    (part,) = answer["parts"]
    assert part["status"] == "NOT_RUN" and "python -m app research" in part["answer"]


def test_a_request_that_needs_information_runs_nothing(root):
    code, answer = _ask(root, "Open the project.", "--dry-run")
    assert code == 3 and all(p["status"] in ("NEEDS_INFORMATION", "NOT_EXECUTED") for p in answer["parts"])


def test_voice_through_ask_equals_text(root, tmp_path):
    from app.voice import SpeechUnavailable, engine, synthesize

    audio = tmp_path / "open.wav"
    try:
        synthesize("Open Calculator.", audio)
        engine.find_components()
    except SpeechUnavailable as missing:
        pytest.skip(f"speech is not available: {missing.reason}")
    code, spoken = _ask(root, "--audio", str(audio), "--dry-run")
    code_typed, typed = _ask(root, spoken["speech"]["text"], "--dry-run")
    assert code == code_typed == 0 and spoken["speech"]["grammar"] == "dictation"
    assert [p["status"] for p in spoken["parts"]] == [p["status"] for p in typed["parts"]] == ["DONE"]
    assert spoken["parts"][0]["actions"] == typed["parts"][0]["actions"]


@pytest.mark.parametrize(
    "args",
    [("ask",), ("ask", "What is resistance?", "--audio", "x.wav"), ("ask", "What is resistance?", "--name", "x"),
     ("ask", "What is resistance?", "--site", "http://example.org/")],
)
def test_invalid_requests_exit_2(root, args):
    assert cli(root, *args)[0] == 2
