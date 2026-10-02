"""Phase 16 acceptance: Part 5 section 213 through the `procedure` command (ADR 0048 P16-13).

    Execute a documented workflow twice. Verify: Procedure persisted. Procedure can be
    retrieved. Procedure remains source-linked. Procedure records verification history.

Each step is one `python -m app` process in a temporary project root; the live database
is never opened. A generated one-page manual states a three-step workflow - create a
folder `<folder>`, create a file in it, copy the file - and goes through the real
`extract`. The workflow is then executed **live** twice with `--execute --confirm`, each
time into a different folder inside pytest's temporary folder: file operations there are
the only live effects, as ADR 0047 P15-11 permits. The expectations are written by hand.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from app.ui.cli import main as cli_main
from tests.conftest import PROJECT_ROOT
from tests.unit.pdf_fixtures import make_pdf
from tests.unit.test_procedures import STEPS, WORKFLOW


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
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(make_pdf([WORKFLOW]))
    project = tmp_path / "project"
    code, out, err = cli(project, "extract", str(pdf))
    assert code == 0, err or out
    return project


def _database(root: Path) -> Path:
    return root / "data" / "database" / "knowledge.db"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_command_set_gains_exactly_procedure():
    before = {"start", "env", "config", "paths", "db", "extract", "classify", "lookup", "review", "edition",
              "merge", "query", "index", "reason", "calculate", "provenance", "interpret", "act", "do", "version"}
    # Later phases add their own commands (ADR 0047 onward); Phase 16's own addition is unchanged.
    assert set(cli_main._COMMANDS) - {"manual", "research", "diagram", "voice", "ask", "source", "solve", "inventory"} == before | {"procedure"}


def test_section_213_a_documented_workflow_executed_twice(root, tmp_path):
    # Retrieved, before any run: listed, and shown with its steps, parameter and source.
    code, listed = _json(root, "procedure")
    assert code == 0 and [p["id"] for p in listed["procedures"]] == ["PRC-00000001"]
    code, shown = _json(root, "procedure", "PRC-00000001")
    assert code == 0 and shown["procedure"]["steps"] == list(STEPS)
    assert shown["procedure"]["parameters"] == ["folder"] and shown["procedure"]["history"] == []
    assert [s["action"] for s in shown["steps"]] == ["CREATE_FOLDER", "CREATE_FILE", "COPY_FILE"]

    # Executed twice, live, each run into its own folder.
    for name in ("first", "second"):
        folder = tmp_path / name
        code, run = _json(root, "procedure", "PRC-00000001", "--execute", "--confirm", "--param", f"folder={folder}")
        assert code == 0 and run["outcome"] == "DONE" and run["live"] is True
        assert [s["status"] for s in run["execution"]["steps"]] == ["VERIFIED"] * 3
        assert run["recorded"].startswith("VER-")
        assert (folder / "notes.txt").read_bytes() == (folder / "notes-copy.txt").read_bytes() == b""

    # Persisted: read back from the database file itself, outside RUDRA.
    with closing(sqlite3.connect(f"file:{_database(root)}?mode=ro", uri=True)) as db:
        row = db.execute("SELECT documentation_status, execution_count, successful_executions, failed_executions, "
                         "last_verified FROM procedure WHERE id = 'PRC-00000001'").fetchone()
        rows = db.execute("SELECT status, observed FROM verification WHERE subject_id = 'PRC-00000001' "
                          "ORDER BY created_at").fetchall()
    assert row[:4] == ("DOCUMENTED_PROCEDURE", 2, 2, 0) and row[4] is not None
    assert [r[0] for r in rows] == ["VERIFIED", "VERIFIED"]
    assert f"folder={tmp_path / 'first'}" in rows[0][1] and f"folder={tmp_path / 'second'}" in rows[1][1]

    # Retrieved again, in a fresh process, with its verification history.
    code, shown = _json(root, "procedure", "PRC-00000001")
    procedure = shown["procedure"]
    assert code == 0 and procedure["labels"] == ["DOCUMENTED_PROCEDURE", "VERIFIED_PROCEDURE"]
    assert (procedure["execution_count"], procedure["successful_executions"], procedure["failed_executions"]) == (2, 2, 0)
    assert [h["status"] for h in procedure["history"]] == ["VERIFIED", "VERIFIED"]
    assert procedure["last_verified"] == procedure["history"][-1]["recorded"]
    assert "verified on 2 of 2 live run(s)" in procedure["note"]
    assert procedure["steps"] == list(STEPS)  # executing changed nothing documented

    # Source-linked: the procedure's own source, and `provenance` for it, still verified.
    (citation,) = procedure["source"]["citations"]
    assert citation["evidence"]["page_number"] == 1 and citation["quote"]["status"] == "VERIFIED"
    assert citation["evidence"]["evidence_text"].startswith("Step 1: Create a folder called <folder>.")
    code, provenance = _json(root, "provenance", "PRC-00000001")
    assert code == 0 and provenance["status"] == "AVAILABLE" and provenance["verification"] == "VERIFIED"
    assert {check["status"] for check in provenance["checks"]} == {"VERIFIED"}


def test_a_blocked_run_is_recorded_as_a_failure(root, tmp_path):
    folder = tmp_path / "taken"
    folder.mkdir()
    code, run = _json(root, "procedure", "PRC-00000001", "--execute", "--confirm", "--param", f"folder={folder}")
    assert code == 6 and run["outcome"] == "FAILED" and "unmet:" in run["message"]
    assert not (folder / "notes.txt").exists()
    code, shown = _json(root, "procedure", "PRC-00000001")
    procedure = shown["procedure"]
    assert (procedure["execution_count"], procedure["successful_executions"], procedure["failed_executions"]) == (1, 0, 1)
    assert [h["status"] for h in procedure["history"]] == ["FAILED"]
    assert procedure["labels"] == ["DOCUMENTED_PROCEDURE"] and procedure["last_verified"] is None


def test_a_dry_run_a_refusal_and_missing_information_change_nothing(root, tmp_path):
    before = _digest(_database(root))
    folder = tmp_path / "work"
    code, out, _ = cli(root, "procedure", "PRC-00000001", "--dry-run", "--param", f"folder={folder}")
    assert code == 0 and "DRY RUN (simulated) - nothing is changed or recorded" in out
    code, run = _json(root, "procedure", "PRC-00000001", "--execute", "--param", f"folder={folder}")
    assert code == 6 and run["outcome"] == "REFUSED" and "--confirm" in run["message"]
    code, out, _ = cli(root, "procedure", "PRC-00000001", "--execute", "--confirm")
    assert code == 3 and "Missing information:\n              folder" in out
    assert not folder.exists() and _digest(_database(root)) == before


def test_the_text_form_shows_section_56s_fields(root):
    code, out, _ = cli(root, "procedure", "PRC-00000001")
    assert code == 0
    for line in ('Procedure   : PRC-00000001 "Procedure stated on page 1"',
                 "Labels      : DOCUMENTED_PROCEDURE; lifecycle ACTIVE", "Executable  : yes", "Parameters  : folder",
                 'Step 1      : "Create a folder called <folder>." -> CREATE_FOLDER(path=<folder>)',
                 "History     : never executed live", "Limitations : none recorded"):
        assert line in out


def test_not_found_is_exit_3(root):
    assert cli(root, "procedure", "PRC-00000099")[0] == 3


@pytest.mark.parametrize(
    "args",
    [("procedure", "K-00000001"), ("procedure", "--dry-run"), ("procedure", "PRC-00000001", "--confirm"),
     ("procedure", "PRC-00000001", "--param", "folder=C:/x"),
     ("procedure", "PRC-00000001", "--dry-run", "--execute"),
     ("procedure", "PRC-00000001", "--dry-run", "--param", "colour=red"),
     ("procedure", "PRC-00000001", "--dry-run", "--param", "folder"),
     ("procedure", "PRC-00000001", "--name", "x"), ("procedure", "--scope", "everything")],
)
def test_invalid_requests_exit_2(root, args):
    assert cli(root, *args)[0] == 2
