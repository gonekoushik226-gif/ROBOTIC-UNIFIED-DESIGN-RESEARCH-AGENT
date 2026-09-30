"""The Phase 11 `calculate` command (Batch C; ADR 0043 P11-28 ... P11-30).

In process, over temporary project roots. Without an admission the project has no
`knowledge.db` at all, and the command must calculate and create none (P11-29). With an
admission, a temporary `knowledge.db` holds stored equations written through the
validating repository (`tests/unit/query_rows.py`). Every call names its scratch root with
--project-root; nothing here touches the live project. The engine's rules are unit-tested
in `tests/unit/test_calculation_engine.py` and `test_calculation_admission.py`; these
tests check that the command carries them.

Exit codes (P11-29): 0 answered - calculated, cannot determine or conflicting; 2 invalid
request; 3 insufficient authorized information; 5 the database cannot be used as it is;
70 anything unexpected.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from types import SimpleNamespace

import pytest

from app.models import Authorization, Equation, KnowledgeType
from app.storage import Repository, connect, migrate
from app.ui.cli import main as cli_main
from app.ui.cli.main import main
from tests.unit.query_rows import QueryRows

SECTION_203 = ("--formula", "Rtotal = R1 + R2", "--formula", "I = V / Rtotal",
               "--input", "R1=10 Ω", "--input", "R2=20 Ω", "--input", "V=10 V")
GIVEN = ("--input", "R1=10 Ω", "--input", "R2=20 Ω", "--input", "V=10 V")


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(root, capsys, *args) -> tuple[int, str, str]:
    code = main([*args, "--project-root", str(root)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _json(root, capsys, *args) -> tuple[int, dict]:
    code, out, err = _run(root, capsys, *args, "--json")
    assert out, err
    return code, json.loads(out)


@pytest.fixture
def bare(tmp_path):
    """A project root with no knowledge database."""
    return tmp_path / "bare"


@pytest.fixture
def stored(tmp_path) -> SimpleNamespace:
    """A project whose knowledge.db holds stored equations in and out of scope."""
    root = tmp_path / "stored"
    db = root / "data" / "database" / "knowledge.db"
    db.parent.mkdir(parents=True)
    (root / "data" / "backups").mkdir()
    n = SimpleNamespace(root=root, db=db)
    with closing(connect(db)) as connection:
        migrate(connection, database_path=db)
        rows = QueryRows(Repository(connection))
        book = rows.document("circuits")
        source = rows.source(book)
        run = rows.run(book)
        closed = rows.source(rows.document("closed"), authorization=Authorization.NOT_AUTHORIZED)

        def equation(text, where):
            item = rows.knowledge(KnowledgeType.EQUATION, text)
            rows._add(Equation, expression=text, lifecycle_status=item.lifecycle_status, knowledge_id=item.id)
            rows.knowledge_occurrence(item, where, page=3, run=run if where is source else None)
            return item

        n.ohm = equation("I = V / Rtotal", source)
        n.hidden = equation("I = V / Rtotal", closed)
        n.implicit = equation("V = IR", source)
        n.definition = rows.knowledge(KnowledgeType.DEFINITION, "Current is a flow of charge.")
        rows.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return n


# ------------------------------------------------------------ the command set


def test_phase_11_adds_exactly_the_calculate_command():
    before = {"start", "env", "config", "paths", "db", "extract", "classify", "lookup", "review",
              "edition", "merge", "query", "index", "reason", "version"}
    # Phase 12 adds `provenance` (ADR 0044 P12-15, P12-16); Phase 11's own addition is unchanged.
    # Phase 13 adds `interpret` (ADR 0045 P13-14).
    assert set(cli_main._COMMANDS) - {"provenance", "interpret", "act", "do", "procedure", "manual", "research", "diagram", "voice", "ask", "source"} == before | {"calculate"}


# ----------------------------------------------------- without an admission


def test_section_203_through_the_command_in_text(bare, capsys):
    code, out, _ = _run(bare, capsys, "calculate", "I", *SECTION_203)

    assert code == 0
    assert "Answer      : CALCULATED - I ≈ 0.333333 A - calculated (step 2); exact value 1/3" in out
    assert "Rtotal = 10 Ω + 20 Ω" in out and "Rtotal = 30 Ω (exact 30)" in out
    assert "I = 10 V ÷ 30 Ω" in out and "I ≈ 0.333333 A (exact 1/3)" in out
    assert "Formula 1   : Rtotal = R1 + R2 - source: the request" in out
    assert "Input       : R1 = 10 Ω (USER_INPUT; given as '10 Ω')" in out
    assert "input sources V USER_INPUT, Rtotal DERIVED from step 1" in out
    assert "Verification: PENDING - not independently verified" in out
    assert "Database    : not opened" in out


def test_without_an_admission_no_database_is_opened_or_created(bare, capsys):
    code, data = _json(bare, capsys, "calculate", "I", *SECTION_203)

    assert code == 0 and data["status"] == "CALCULATED" and data["result"]["exact"] == "1/3"
    assert data["database_opened"] is False and data["read_only_connection"] is None
    database = bare / "data" / "database"
    assert not (database / "knowledge.db").exists()
    assert list(database.iterdir()) == [] and list((bare / "data" / "indexes").iterdir()) == []


def test_json_is_byte_identical_on_repeat(bare, capsys):
    first = _run(bare, capsys, "calculate", "I", *SECTION_203, "--json")[1]
    assert _run(bare, capsys, "calculate", "I", *SECTION_203, "--json")[1] == first


def test_cannot_determine_and_conflicting_are_answers_with_exit_code_0(bare, capsys):
    code, out, _ = _run(bare, capsys, "calculate", "I", "--formula", "Rtotal = R1 + R2",
                        "--formula", "I = V / Rtotal", "--input", "R1=10 Ω", "--input", "V=10 V")
    assert code == 0 and "CANNOT_DETERMINE" in out
    assert "Missing     : R2 — required by `Rtotal = R1 + R2`" in out

    code, data = _json(bare, capsys, "calculate", "X", "--formula", "X = V + R",
                       "--input", "V=10 V", "--input", "R=20 Ω")
    assert code == 0 and data["status"] == "CANNOT_DETERMINE"
    assert data["symbols"][0]["methods"][0]["state"] == "DIMENSIONALLY_INCONSISTENT"

    code, data = _json(bare, capsys, "calculate", "P", "--formula", "P = V * I",
                       "--formula", "P = I^2 * R", "--input", "V=10 V", "--input", "I=2 A",
                       "--input", "R=6 Ω")
    assert code == 0 and data["status"] == "CONFLICTING" and data["result"] is None


def test_assumptions_are_labelled_in_the_text(bare, capsys):
    code, out, _ = _run(bare, capsys, "calculate", "I", "--formula", "I = V / R",
                        "--input", "R=2.2 kΩ", "--assume", "V=5 V")
    assert code == 0
    assert "Assumption  : V = 5 V (ASSUMPTION; given as '5 V')" in out
    assert "conditional on the assumption(s) V" in out


@pytest.mark.parametrize(
    "args",
    [
        ("calculate", "I", "--formula", "I = V R", "--input", "V=1 V", "--input", "R=1 Ω"),
        ("calculate", "I", "--formula", "I = V / R", "--input", "V=1 volt"),
        ("calculate", "G", "--formula", "G = A * 2", "--input", "A=3 dB"),
        ("calculate", "V", "--formula", "V = IR", "--input", "I=1 A", "--input", "R=1 Ω"),
        ("calculate", "V", "--formula", "V / I = R"),
        ("calculate", "I", "--input", "R1"),
        ("calculate", "--formula", "I = V / R"),
        ("calculate", "I", "--admit", "K-00000001", "--admit", "K-00000002"),
        ("calculate", "I", "--admit", "CPT-00000001"),
        ("calculate", "I", "--scope", "everywhere"),
        ("calculate", "I", "--forward"),
        ("calculate", "I", "--name", "Current"),
        ("calculate", "I", "--keyword", "ohm"),
    ],
)
def test_an_invalid_request_is_exit_code_2_before_anything_is_opened(bare, capsys, args):
    code, _, err = _run(bare, capsys, *args)
    assert code == 2, err
    assert not (bare / "data" / "database" / "knowledge.db").exists()


def test_reason_still_refuses_formula_and_other_commands_ignore_it(bare, capsys):
    code, _, err = _run(bare, capsys, "reason", "X", "--formula", "X = 1")
    assert code == 2 and "--formula" in err
    assert _run(bare, capsys, "version", "--formula", "X = 1")[0] == 0


# ------------------------------------------------------------ with an admission


def test_the_admitted_equation_is_used_read_only_and_nothing_is_written(stored, capsys):
    before = _sha256(stored.db)
    code, data = _json(stored.root, capsys, "calculate", "I", "--formula", "Rtotal = R1 + R2",
                       "--admit", stored.ohm.id, *GIVEN)

    assert code == 0 and data["status"] == "CALCULATED" and data["result"]["exact"] == "1/3"
    assert data["database_opened"] is True and data["read_only_connection"] is True
    assert data["admitted"]["knowledge"]["id"] == stored.ohm.id
    assert data["admitted"]["label"] == "UNCERTAIN"
    assert data["formulas"][1]["origin"] == "ADMITTED_STORED_ITEM"
    assert _sha256(stored.db) == before
    assert not (stored.root / "data" / "indexes" / "index.db").exists()

    code, out, _ = _run(stored.root, capsys, "calculate", "I", "--formula", "Rtotal = R1 + R2",
                        "--admit", stored.ohm.id, *GIVEN)
    assert code == 0 and f"Admitted    : {stored.ohm.id} \"I = V / Rtotal\" - UNCERTAIN" in out
    assert f"formula source stored equation {stored.ohm.id}" in out


def test_a_withheld_admission_the_target_needs_is_exit_code_3(stored, capsys):
    code, data = _json(stored.root, capsys, "calculate", "I", "--formula", "Rtotal = R1 + R2",
                       "--admit", stored.hidden.id, *GIVEN)
    assert code == 3 and data["status"] == "INSUFFICIENT_AUTHORIZED_INFORMATION"
    assert data["refused"]["reason"] == "NOT_AUTHORIZED"
    assert "I = V / Rtotal" not in json.dumps(data["formulas"], ensure_ascii=False)


def test_an_admitted_equation_outside_the_grammar_is_answered_not_refused(stored, capsys):
    code, data = _json(stored.root, capsys, "calculate", "V", "--admit", stored.implicit.id,
                       "--input", "I=2 A", "--input", "R=5 Ω")
    assert code == 0 and data["status"] == "CANNOT_DETERMINE"
    assert "IMPLICIT_MULTIPLICATION" in data["admitted"]["not_usable"]


def test_an_admission_that_is_not_a_stored_equation_is_exit_code_2(stored, capsys):
    assert _run(stored.root, capsys, "calculate", "I", "--admit", stored.definition.id)[0] == 2
    assert _run(stored.root, capsys, "calculate", "I", "--admit", "K-00009999")[0] == 2


def test_an_admission_without_a_database_is_exit_code_5_and_creates_none(bare, capsys):
    code, _, err = _run(bare, capsys, "calculate", "I", "--admit", "K-00000001", *GIVEN)
    assert code == 5 and "no knowledge database" in err.lower()
    assert not (bare / "data" / "database" / "knowledge.db").exists()


def test_an_older_schema_is_refused_and_never_migrated(stored, capsys):
    with closing(connect(stored.db)) as connection:
        connection.execute("PRAGMA user_version = 4")
        connection.commit()
    before = _sha256(stored.db)
    code, _, err = _run(stored.root, capsys, "calculate", "I", "--formula", "Rtotal = R1 + R2",
                        "--admit", stored.ohm.id, *GIVEN)
    assert code == 5 and "schema version is 4" in err
    assert _sha256(stored.db) == before
