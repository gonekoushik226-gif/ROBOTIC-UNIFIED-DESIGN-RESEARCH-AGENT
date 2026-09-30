"""Phase 17 acceptance: Part 5 section 215 through `extract --manual`, `manual` and `do`
(ADR 0049 P17-13).

    Given a manual containing: File -> New Project -> Select Template -> Enter Name ->
    Create, and the user requests: "Create a project called amplifier." RUDRA must
    construct the documented workflow rather than inventing an unrelated one.

Each step is one `python -m app` process in a temporary project root; the live database is
never opened. The generated manual states section 215's workflow in its own layout - the
menu, then one line per step - with the arrows written `->`, because the test PDF's
standard font cannot carry `→` (its extraction is mojibake; `→` is tested on text in
`tests/unit/test_manuals.py`). Nothing is executed on the computer: `do` constructs the
workflow and runs nothing. The expectations are written by hand.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.ui.cli import main as cli_main
from tests.conftest import PROJECT_ROOT
from tests.unit.pdf_fixtures import make_pdf
from tests.unit.test_manuals import MANUAL, TEXTBOOK, WORKFLOW


def cli(root, *args: str) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUDRA_")}
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, "-m", "app", *args, "--project-root", str(root)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(PROJECT_ROOT), timeout=240,
    )
    return done.returncode, done.stdout, done.stderr


def _json(root, *args) -> tuple[int, dict]:
    code, out, err = cli(root, *args, "--json")
    assert out, err
    return code, json.loads(out)


@pytest.fixture
def root(tmp_path) -> Path:
    project = tmp_path / "project"
    textbook = tmp_path / "textbook.pdf"
    textbook.write_bytes(make_pdf([TEXTBOOK]))
    code, out, err = cli(project, "extract", str(textbook))  # DOC-00000001: never declared a manual
    assert code == 0, err or out
    manual = tmp_path / "Circuit Studio User Guide.pdf"
    manual.write_bytes(make_pdf([MANUAL]))
    code, out, err = cli(project, "extract", str(manual), "--manual", "Circuit Studio")
    assert code == 0, err or out
    assert "Manual     : DOC-00000002 declared the manual of 'Circuit Studio' (recorded now)" in out
    return project


def _digest(root: Path) -> str:
    return hashlib.sha256((root / "data" / "database" / "knowledge.db").read_bytes()).hexdigest()


def test_the_command_set_gains_exactly_manual():
    before = {"start", "env", "config", "paths", "db", "extract", "classify", "lookup", "review", "edition",
              "merge", "query", "index", "reason", "calculate", "provenance", "interpret", "act", "do",
              "procedure", "version"}
    # Later phases add their own commands (ADR 0047 onward); Phase 17's own addition is unchanged.
    assert set(cli_main._COMMANDS) - {"research", "diagram", "voice", "ask", "source"} == before | {"manual"}


def test_section_215_the_documented_workflow_is_constructed(root):
    # The manual's seven kinds.
    code, manual = _json(root, "manual", "DOC-00000002")
    assert code == 0 and manual["declaration"]["application"] == "Circuit Studio"
    assert manual["menus"] == ["File"] and manual["commands"] == ["New Project", "Create"]
    (workflow,) = manual["workflows"]
    assert workflow["steps"] == list(WORKFLOW) and workflow["labels"] == ["DOCUMENTED_PROCEDURE"]
    assert [s["statement"] for s in manual["shortcuts"]] == ["Press Ctrl+N"]
    assert manual["parameters"] == ["template", "name"]
    assert [c["quote"] for c in manual["constraints"]] == ["Project names must not contain spaces."]
    assert [f["name"] for f in manual["file_formats"]] == ["File format .cstudio"]

    # The request: the documented workflow, exactly, with what is missing - nothing run.
    before = _digest(root)
    code, report = _json(root, "do", "Create a project called amplifier.")
    assert code == 3 and report["outcome"] == "NOT_EXECUTED" and report["execution"] is None
    (built,) = report["workflows"]
    assert [s["text"] for s in built["steps"]] == list(WORKFLOW)  # none added, removed or reordered
    assert [s["role"] for s in built["steps"]] == ["MENU", "COMMAND", "INPUT", "INPUT", "COMMAND"]
    assert (built["steps"][3]["input"], built["steps"][3]["value"]) == ("name", "amplifier")
    assert (built["steps"][2]["input"], built["steps"][2]["value"]) == ("template", None)
    assert built["missing"] == ["template"] and built["procedure_id"] == workflow["id"]
    assert (built["document_id"], built["page"], built["application"]) == ("DOC-00000002", 1, "Circuit Studio")
    assert "Missing information:\ntemplate" in report["message"] and "Nothing was executed" in report["message"]
    assert _digest(root) == before  # read only

    # The workflow stays source-linked: its quote is where the manual prints it.
    code, provenance = _json(root, "provenance", workflow["id"])
    (citation,) = provenance["citations"]
    assert provenance["status"] == "AVAILABLE" and citation["quote"]["status"] == "VERIFIED"
    assert citation["evidence"]["evidence_text"] == "File -> New Project -> Select Template -> Enter Name -> Create"


def test_the_text_form_shows_the_workflow_and_what_is_missing(root):
    code, out, _ = cli(root, "do", "Create a project called amplifier.")
    assert code == 3
    for line in ("Answer      : NOT_EXECUTED - the documented workflow was constructed from",
                 "              Missing information:", "              template",
                 "  step 1    : MENU    File", "  step 2    : COMMAND New Project",
                 "  step 3    : INPUT   Select Template - template: MISSING",
                 "  step 4    : INPUT   Enter Name - name = 'amplifier'", "  step 5    : COMMAND Create"):
        assert line in out


def test_naming_the_application(root):
    code, report = _json(root, "do", "Create a project called amplifier in Circuit Studio.")
    assert code == 3 and [s["text"] for s in report["workflows"][0]["steps"]] == list(WORKFLOW)
    code, report = _json(root, "do", "Create a project called amplifier in Word.")
    assert code == 3 and report["workflows"] == []
    assert "no documented workflow for creating a project for Word" in report["message"]


def test_without_a_declared_manual_nothing_is_invented(tmp_path):
    project = tmp_path / "project"
    textbook = tmp_path / "textbook.pdf"
    textbook.write_bytes(make_pdf([TEXTBOOK]))  # its Step 1 is "Choose New Project."
    assert cli(project, "extract", str(textbook))[0] == 0
    code, report = _json(project, "do", "Create a project called amplifier.")
    assert code == 3 and report["workflows"] == [] and "RUDRA does not invent one" in report["message"]
    code, report = _json(tmp_path / "empty", "do", "Create a project called amplifier.")
    assert code == 3 and report["workflows"] == []
    assert not (tmp_path / "empty" / "data" / "database" / "knowledge.db").exists()


def test_the_manual_list_and_an_undeclared_document(root):
    code, listing = _json(root, "manual")
    (entry,) = listing["manuals"]
    assert code == 0 and entry["document_id"] == "DOC-00000002" and entry["counts"]["workflows"] == 1
    assert cli(root, "manual", "DOC-00000001")[0] == 3


def test_the_declaration_is_never_rewritten_and_the_stage_runs_once(root, tmp_path):
    manual = tmp_path / "Circuit Studio User Guide.pdf"
    code, out, _ = cli(root, "extract", str(manual), "--manual", "circuit studio")
    assert code == 0 and "(already recorded)" in out and "already run" in out
    before = _digest(root)
    code, out, err = cli(root, "extract", str(manual), "--manual", "Word")
    assert code == 2 and _digest(root) == before
    code, _, _ = cli(root, "extract", "--re-extract", "DOC-00000002", "--manual", "Word")
    assert code == 2 and _digest(root) == before  # refused before any extraction


@pytest.mark.parametrize(
    "args",
    [("manual", "PRC-00000001"), ("manual", "--name", "x"), ("manual", "DOC-00000002", "--manual", "X"),
     ("reason", "X", "--manual", "Y"), ("do", "Open Calculator.", "--manual", "Y"),
     ("extract", "missing.pdf", "--manual", "  ")],
)
def test_invalid_requests_exit_2(root, args):
    assert cli(root, *args)[0] == 2
