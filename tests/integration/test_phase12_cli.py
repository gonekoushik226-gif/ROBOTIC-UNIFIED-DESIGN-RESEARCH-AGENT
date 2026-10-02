"""The Phase 12 `provenance` command (ADR 0044 P12-15, P12-16).

In process, over temporary project roots whose `knowledge.db` is written through the
validating repository (`tests/unit/query_rows.py`). Every call names its scratch root with
--project-root; nothing here touches the live project. The rules are unit-tested in
`tests/unit/test_provenance.py` and `test_verification.py`; these tests check the command
carries them.

Exit codes (P12-15): 0 answered, including "Provenance unavailable." and every
verification outcome; 2 invalid request; 3 not found; 5 storage; 70 unexpected.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from types import SimpleNamespace

import pytest

from app.models import Authorization, Equation, KnowledgeType, LifecycleStatus
from app.storage import Repository, connect, migrate
from app.ui.cli import main as cli_main
from app.ui.cli.main import main
from tests.unit.query_rows import QueryRows

SECTION_203 = ("--formula", "Rtotal = R1 + R2", "--formula", "I = V / Rtotal",
               "--input", "R1=10 Ω", "--input", "R2=20 Ω", "--input", "V=10 V")


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
def project(tmp_path) -> SimpleNamespace:
    root = tmp_path / "project"
    db = root / "data" / "database" / "knowledge.db"
    db.parent.mkdir(parents=True)
    (root / "data" / "backups").mkdir()
    n = SimpleNamespace(root=root, db=db, tmp=tmp_path)
    with closing(connect(db)) as connection:
        migrate(connection, database_path=db)
        rows = QueryRows(Repository(connection))
        book = n.book = rows.document("circuits")
        source = rows.source(book)
        run = rows.run(book)
        n.ohm = rows.knowledge(KnowledgeType.EQUATION, "I = V / Rtotal")
        rows._add(Equation, expression="I = V / Rtotal", lifecycle_status=LifecycleStatus.ACTIVE,
                  knowledge_id=n.ohm.id)
        rows.knowledge_occurrence(n.ohm, source, page=3, run=run)
        closed = rows.source(rows.document("closed"), authorization=Authorization.NOT_AUTHORIZED)
        n.hidden = rows.knowledge(KnowledgeType.DEFINITION, "A withheld statement.")
        rows.knowledge_occurrence(n.hidden, closed, page=1)
        n.bare = rows.knowledge(KnowledgeType.CLAIM, "A statement with no evidence.")
        rows.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return n


def _answer(root, capsys, tmp, *args, name="answer.json"):
    code, out, err = _run(root, capsys, *args, "--json")
    assert code == 0, err
    path = tmp / name
    path.write_text(out, encoding="utf-8")
    return path


# ------------------------------------------------------------ the command set


def test_phase_12_adds_exactly_the_provenance_command():
    before = {"start", "env", "config", "paths", "db", "extract", "classify", "lookup", "review",
              "edition", "merge", "query", "index", "reason", "calculate", "version"}
    # Phase 13 adds `interpret` (ADR 0045 P13-14); Phase 12's own addition is unchanged.
    assert set(cli_main._COMMANDS) - {"interpret", "act", "do", "procedure", "manual", "research", "diagram", "voice", "ask", "source", "solve", "inventory"} == before | {"provenance"}


# ------------------------------------------------------------ stored items


def test_an_items_provenance_in_text_and_json(project, capsys):
    before = _sha256(project.db)
    code, out, _ = _run(project.root, capsys, "provenance", project.ohm.id)
    assert code == 0
    assert "Answer      : AVAILABLE - Provenance available" in out
    assert "p.3" in out and "quote check INCONCLUSIVE" in out  # no span recorded here

    code, data = _json(project.root, capsys, "provenance", project.ohm.id)
    assert code == 0 and data["status"] == "AVAILABLE"
    assert data["citations"][0]["evidence"]["page_number"] == 3
    assert _sha256(project.db) == before
    assert not (project.root / "data" / "indexes" / "index.db").exists()


def test_unavailable_items_say_so_verbatim_and_cite_nothing(project, capsys):
    for item, reason in ((project.bare, "No evidence is stored"), (project.hidden, "not authorized")):
        code, out, _ = _run(project.root, capsys, "provenance", item.id)
        assert code == 0
        assert "Answer      : UNAVAILABLE - Provenance unavailable." in out and reason in out
        assert item.statement not in out and "Evidence    :" not in out


def test_a_nonexistent_identifier_is_exit_code_3(project, capsys):
    code, out, _ = _run(project.root, capsys, "provenance", "K-00009999")
    assert code == 3 and "NOT_FOUND" in out


@pytest.mark.parametrize(
    "args",
    [
        ("provenance",),
        ("provenance", "the resistor"),
        ("provenance", "K-00000001", "--answer", "x.json"),
        ("provenance", "K-00000001", "--scope", "everywhere"),
        ("provenance", "K-00000001", "--input", "E"),
        ("provenance", "K-00000001", "--formula", "X = 1"),
        ("provenance", "K-00000001", "--name", "Resistor"),
        ("provenance", "--answer", "missing.json", "--scope", "authorized"),
        ("provenance", "--answer", "missing.json"),
    ],
)
def test_an_invalid_request_is_exit_code_2(project, capsys, args):
    code, _, err = _run(project.root, capsys, *args)
    assert code == 2, err


def test_reason_and_calculate_refuse_the_answer_flag(project, capsys):
    assert _run(project.root, capsys, "reason", "X", "--answer", "a.json")[0] == 2
    assert _run(project.root, capsys, "calculate", "I", "--answer", "a.json")[0] == 2


def test_an_items_provenance_without_a_database_is_exit_code_5(tmp_path, capsys):
    code, _, err = _run(tmp_path / "empty", capsys, "provenance", "K-00000001")
    assert code == 5 and not (tmp_path / "empty" / "data" / "database" / "knowledge.db").exists()


# ------------------------------------------------------------------ answers


def test_a_request_only_answer_verifies_without_opening_a_database(tmp_path, capsys):
    bare = tmp_path / "bare"
    path = _answer(bare, capsys, tmp_path, "calculate", "I", *SECTION_203)

    code, out, _ = _run(bare, capsys, "provenance", "--answer", str(path))

    assert code == 0
    assert "Answer      : VERIFIED" in out and "Database    : not opened" in out
    assert "Source      : UNAVAILABLE - Provenance unavailable." in out
    assert not (bare / "data" / "database" / "knowledge.db").exists()


def test_an_admitted_answer_verifies_and_cites_its_page(project, capsys):
    path = _answer(project.root, capsys, project.tmp, "calculate", "I", "--formula", "Rtotal = R1 + R2",
                   "--admit", project.ohm.id, "--input", "R1=10 Ω", "--input", "R2=20 Ω", "--input", "V=10 V")
    before = _sha256(project.db)

    code, data = _json(project.root, capsys, "provenance", "--answer", str(path))

    assert code == 0 and data["status"] == "VERIFIED" and data["database_opened"] is True
    assert data["exposure"]["source_status"] == "AVAILABLE"
    assert data["exposure"]["relevant_knowledge"] == [project.ohm.id]
    assert data["exposure"]["pages"] == [f"{project.book.id} p.3"]
    assert _sha256(project.db) == before


def test_a_tampered_answer_is_failed_with_exit_code_0(project, capsys):
    path = _answer(project.root, capsys, project.tmp, "calculate", "I", *SECTION_203)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["result"]["exact"] = "2/3"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    code, report = _json(project.root, capsys, "provenance", "--answer", str(path))

    assert code == 0 and report["status"] == "FAILED"


def test_an_answer_needing_a_missing_database_is_inconclusive(project, capsys, tmp_path):
    path = _answer(project.root, capsys, project.tmp, "calculate", "I", "--formula", "Rtotal = R1 + R2",
                   "--admit", project.ohm.id, "--input", "R1=10 Ω", "--input", "R2=20 Ω", "--input", "V=10 V")
    code, report = _json(tmp_path / "elsewhere", capsys, "provenance", "--answer", str(path))
    assert code == 0 and report["status"] == "INCONCLUSIVE" and report["database_opened"] is False


def test_a_file_that_is_not_an_answer_is_exit_code_2(project, capsys):
    path = project.tmp / "not-an-answer.json"
    path.write_text('{"hello": "world"}', encoding="utf-8")
    assert _run(project.root, capsys, "provenance", "--answer", str(path))[0] == 2
