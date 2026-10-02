"""The product journey, end to end: add a document, ask in plain English, calculate, restart.

Everything here goes through the command line's own commands on a scratch project - the
same commands the window runs - and a second operating-system process for "restart". The
live knowledge database is never opened.

    ADD DOCUMENT -> knowledge stored and searchable -> ask -> answer with sources
    -> ask a calculation, RUDRA chooses the equations -> restart -> still there
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.ui.cli.main import main
from tests.conftest import PROJECT_ROOT

DOCUMENT = """Circuits

Resistance is defined as the opposition offered by a material to the flow of current.
The symbols are used in the usual way, where V is the voltage across the element.
The same convention is followed everywhere, where I is the current in the circuit.
The opposition to current is limited by the resistor, where R is the resistance in ohms.
Gain describes how strongly an amplifier increases the amplitude of a signal.
Rtotal = R1 + R2
I = V / Rtotal
V = I * R
P = V * I
"""


def _run(root: Path, capsys, *arguments: str) -> tuple[int, str]:
    capsys.readouterr()
    code = main([*arguments, "--project-root", str(root)])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def _ask(root: Path, capsys, question: str) -> dict:
    code, out = _run(root, capsys, "ask", question, "--json")
    return json.loads(out[out.index("{"):out.rindex("}") + 1])["parts"][0]


@pytest.fixture
def project(tmp_path, capsys):
    root = tmp_path / "project"
    document = tmp_path / "circuits.txt"
    document.write_text(DOCUMENT, encoding="utf-8")
    code, out = _run(root, capsys, "extract", str(document))
    assert code == 0, out
    return root, out


def test_a_document_is_searchable_the_moment_it_is_asked_about(project, capsys):
    """Nobody has to build a search index: asking (or searching) brings it up to date by itself."""
    root, extract_output = project
    assert "Phase" not in extract_output and "run 'index'" not in extract_output
    assert not (root / "data" / "indexes" / "index.db").exists()  # extraction never writes the derived index
    part = _ask(root, capsys, "What is the gain?")
    assert part["status"] == "ANSWERED" and (root / "data" / "indexes" / "index.db").is_file()


def test_a_factual_question_is_answered_from_the_document_with_its_source(project, capsys):
    root, _ = project
    part = _ask(root, capsys, "What is resistance?")
    assert part["status"] == "ANSWERED"
    assert "opposition offered by a material" in part["answer"]
    assert part["sources"] and "DOC-00000001" in part["sources"][0] and part["basis"]


def test_a_concept_that_is_only_mentioned_is_answered_honestly(project, capsys):
    root, _ = project
    part = _ask(root, capsys, "What is the gain?")
    assert part["status"] == "ANSWERED"
    first, *rest = part["answer"].split("\n")
    assert first == "Nothing about 'gain' was stored as knowledge, but your documents mention it:"
    assert any(line.startswith("Page text (circuits.txt, page 1):") and "amplifier" in line for line in rest)
    assert "did not extract it as knowledge" in part["reasoning"][0]
    assert part["sources"] and "DOC-00000001 p.1" in part["sources"][0]


def test_a_question_nothing_in_the_documents_touches_stays_unknown(project, capsys):
    root, _ = project
    part = _ask(root, capsys, "What is a zorblaxian?")
    assert part["status"] == "UNKNOWN" and part["answer"].startswith("Unknown.")
    assert not any("python -m app" in step and "Cancel" in step for step in part["next_steps"])


def test_a_calculation_is_asked_for_in_plain_english_and_rudra_chooses_the_equations(project, capsys):
    root, _ = project
    part = _ask(root, capsys, "Calculate the current when V = 10 V and R = 5 ohms")
    assert part["status"] == "ANSWERED" and part["answer"] == "I = 2 A"
    assert part["command"][:2] == ["solve", "current"] or part["command"][:2] == ["solve", "I"]
    assert any("I = V / R" in step for step in part["calculation"])
    assert part["calculation"][-1].startswith("Checked")
    assert part["sources"] and any("circuits" in s or "DOC-00000001" in s for s in part["sources"])


def test_a_two_step_calculation_follows_the_values_through_the_equations(project, capsys):
    root, _ = project
    part = _ask(root, capsys, "Find the current if V = 12 V, R1 = 10 ohms and R2 = 20 ohms")
    assert part["status"] == "ANSWERED" and part["answer"] == "I = 0.4 A"
    steps = [s for s in part["calculation"] if s.startswith("Step")]
    assert len(steps) == 2 and "Rtotal = R1 + R2" in steps[0] and "I = V / Rtotal" in steps[1]


def test_a_quantity_the_documents_explain_can_be_named_in_words(project, capsys):
    root, _ = project
    part = _ask(root, capsys, "What is the voltage if I = 2 A and R = 5 ohms?")
    assert part["status"] == "ANSWERED" and part["answer"] == "V = 10 V"
    assert "(voltage)" in part["reasoning"][0]


def test_a_calculation_that_the_documents_cannot_support_says_what_is_missing(project, capsys):
    root, _ = project
    part = _ask(root, capsys, "Calculate P when V = 10 V")
    assert part["status"] == "CANNOT_DETERMINE"
    assert part["answer"].startswith("I cannot determine this from the currently authorized information.")
    assert part["missing"] and "needs I" in part["missing"][0]
    assert part["available"] == ["V (voltage) = 10 V"]


def test_a_calculation_without_any_values_asks_for_them(project, capsys):
    root, _ = project
    part = _ask(root, capsys, "Calculate the output voltage")
    assert part["status"] == "NEEDS_INFORMATION"
    assert any("values you know" in item for item in part["missing"])


def test_asking_before_any_document_is_added_says_so_in_plain_words(tmp_path, capsys):
    part = _ask(tmp_path / "empty", capsys, "What is resistance?")
    assert part["status"] == "NEEDS_INFORMATION"
    assert part["answer"] == "RUDRA has no knowledge yet. Add a document first (the Add document page), then ask."


def test_the_knowledge_survives_a_restart(project):
    """A second operating-system process - a restart - answers from what the first one stored."""
    root, _ = project
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}

    def ask(question: str) -> dict:
        done = subprocess.run([sys.executable, "-m", "app", "ask", question, "--json", "--project-root", str(root)],
                              capture_output=True, text=True, encoding="utf-8", cwd=str(PROJECT_ROOT), env=environment,
                              timeout=300)
        return json.loads(done.stdout[done.stdout.index("{"):done.stdout.rindex("}") + 1])["parts"][0]

    assert "opposition offered by a material" in ask("What is resistance?")["answer"]
    assert ask("Calculate the current when V = 10 V and R = 5 ohms")["answer"] == "I = 2 A"


def test_the_knowledge_inventory_matches_what_was_added(project, capsys):
    root, _ = project
    code, out = _run(root, capsys, "inventory", "--json")
    data = json.loads(out[out.index("{"):out.rindex("}") + 1])
    assert data["totals"]["documents"] == 1 and data["totals"]["counts"]["EQUATION"] == 4
    assert data["calculation"]["usable"] == 4


def test_a_search_in_ask_rebuilds_a_missing_index_by_itself(project, capsys):
    root, _ = project
    assert not (root / "data" / "indexes" / "index.db").exists()
    part = _ask(root, capsys, "Search for amplifier")
    assert part["status"] == "ANSWERED" and "amplifier" in part["answer"].lower()
    assert (root / "data" / "indexes" / "index.db").is_file()


def test_the_help_prints_on_a_console_that_cannot_show_every_character():
    """`--help` once crashed on a Windows console using the ANSI code page (a Greek letter in its text)."""
    environment = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONDONTWRITEBYTECODE": "1"}
    environment.pop("PYTHONUTF8", None)
    done = subprocess.run([sys.executable, "-m", "app", "--help"], capture_output=True, cwd=str(PROJECT_ROOT),
                          env=environment, timeout=120)
    assert done.returncode == 0, done.stderr.decode("cp1252", "replace")
    assert b"Traceback" not in done.stderr and b"usage:" in done.stdout


def test_the_installed_program_names_itself_in_every_hint(project, capsys, monkeypatch):
    """There is no `python` on an installed machine: hints say how to run the installed program."""
    root, _ = project
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    code, out = _run(root, capsys, "ask", "Search for zorblax")
    assert code in (0, 3) and "python -m app" not in out and "RUDRA-CLI.exe" in out
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(["--help"])
    shown = capsys.readouterr().out
    assert "usage: RUDRA-CLI.exe" in shown and "python -m app" not in shown
    assert not isinstance(sys.stdout, type(None)) and not hasattr(sys.stdout, "_stream")  # restored afterwards
