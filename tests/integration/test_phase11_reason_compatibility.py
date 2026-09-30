"""Phase 11 Batch A: `reason` refuses `--formula` (Step 0 compatibility decision (a)).

The command line has one flat set of flags. Phase 11 adds `--formula` for the coming
`calculate` command, and `reason` refuses other commands' flags rather than ignoring
them (`_NOT_REASON_FLAGS`, ADR 0040 P10-28): a flag silently ignored could be read as
applied. The user chose decision (a) at Step 0: `--formula` joins that list, a one-line
compatibility change recorded as ADR 0043 P11-30 edit 5. `reason` does not read,
parse or use a formula, and nothing else about it changes.

Temporary project roots only; the live database is never opened.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.storage import connect, migrate
from app.ui.cli import main as cli_main
from app.ui.cli.main import main


def _run(root: Path, capsys, *args: str) -> tuple[int, str, str]:
    code = main([*args, "--project-root", str(root)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_formula_is_on_the_list_of_flags_reason_refuses():
    assert ("--formula", "formula") in cli_main._NOT_REASON_FLAGS


def test_reason_refuses_formula_before_any_database_is_opened(tmp_path, capsys):
    root = tmp_path / "empty-project"
    code, out, err = _run(root, capsys, "reason", "X", "--input", "E", "--formula", "y = x")
    assert code == 2 and out == ""
    assert "INVALID_INPUT" in err and "'reason' does not take --formula" in err
    assert not (root / "data" / "database" / "knowledge.db").exists()


def test_reason_refuses_formula_and_leaves_an_existing_database_byte_identical(tmp_path, capsys):
    root = tmp_path / "project"
    db = root / "data" / "database" / "knowledge.db"
    db.parent.mkdir(parents=True)
    (root / "data" / "backups").mkdir()
    connection = connect(db)
    migrate(connection, database_path=db)
    connection.close()
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    code, out, err = _run(root, capsys, "reason", "--forward", "--input", "E", "--formula", "I = V / R")
    assert code == 2 and out == "" and "--formula" in err
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not (root / "data" / "indexes" / "index.db").exists()


def test_the_calculate_command_takes_the_flag_that_reason_refuses():
    """Batch A added the flag alone and asserted that no `calculate` command existed yet.
    Batch C registered the command (ADR 0043 P11-29) and retired that interim assertion
    (PHASE_11.md section 16.1): `--formula` now belongs to `calculate`, and `reason` still
    refuses it."""
    assert "calculate" in cli_main._COMMANDS
    assert ("--formula", "formula") not in cli_main._NOT_CALCULATE_FLAGS
