"""The Phase 9 `query` and `index` commands (step 11; ADRs 0035-0037, P9-24, P9-31).

In process, over a temporary project whose `knowledge.db` holds the query tests'
`mosfet_library`, stored through the validating repository. Every call names its
scratch root with --project-root; nothing here touches the live project.

Exit codes (Phase 7, P9-24): 0 an answer was found; 2 invalid input; 3 nothing found,
or insufficient authorized information; 4 not enough disk space for a build; 5 the
database or the index cannot be used as it is; 70 anything unexpected.
"""

from __future__ import annotations

import functools
import hashlib
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest import mock

import pytest

import app.query
from app.models import KnowledgeType, LifecycleStatus, RelationType
from app.query import QueryEngine
from app.storage import Repository, connect, migrate, migrator
from app.storage import keyword_index as store
from app.ui.cli import main as cli_main
from app.ui.cli.main import main
from tests.unit.query_rows import QueryRows, mosfet_library

HIDDEN = "A MOSFET is a kind of vacuum tube."


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _layout(root) -> SimpleNamespace:
    data = root / "data"
    return SimpleNamespace(root=root, db=data / "database" / "knowledge.db",
                           indexes=data / "indexes", index=data / "indexes" / "index.db")


@pytest.fixture
def project(tmp_path) -> SimpleNamespace:
    """A scratch project holding the MOSFET library (three books, one not authorised)."""
    p = _layout(tmp_path / "project")
    p.db.parent.mkdir(parents=True)
    (p.root / "data" / "backups").mkdir()
    connection = connect(p.db)
    migrate(connection, database_path=p.db)
    p.n = mosfet_library(QueryRows(Repository(connection)))
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return p


def _run(p, capsys, *args) -> tuple[int, str, str]:
    code = main([*args, "--project-root", str(p.root)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _json(p, capsys, *args) -> tuple[int, dict]:
    code, out, err = _run(p, capsys, *args, "--json")
    assert out, err
    return code, json.loads(out)


def _add_concept(p, name: str) -> None:
    """A later write to the scratch knowledge.db, as a later extraction would make:
    a concept with its occurrence in book A."""
    with closing(connect(p.db)) as connection:
        rows = QueryRows(Repository(connection))
        concept = rows.concept(name)
        rows.concept_occurrence(concept, p.n.src_a, page=1, segment=p.n.seg_a, span=(500, 505), run=p.n.run_a)
        rows.commit()


def _add_endpoint_cases(p) -> tuple[str, str, str, str]:
    """Two stored HAS_PROPERTY edges from book A's MOSFET, each stated in book A: one
    reaching an object whose only evidence is in NOT_AUTHORIZED book C, one reaching a
    SUPERSEDED object. Returns (edge, object) identifiers for each."""
    n = p.n
    with closing(connect(p.db)) as connection:
        rows = QueryRows(Repository(connection))
        hidden = rows.knowledge(KnowledgeType.PROPERTY, "The characteristic of a MOSFET is a drift known only to book C.")
        rows.knowledge_occurrence(hidden, n.src_c, page=1, segment=n.seg_c, span=(100, 160), run=n.run_c)
        to_hidden = rows.edge(RelationType.HAS_PROPERTY, from_concept_id=n.mosfet_a.id, to_knowledge_id=hidden.id)
        rows.relationship_occurrence(to_hidden, n.src_a, page=1, segment=n.seg_a, span=(500, 560), run=n.run_a)
        old = rows.knowledge(KnowledgeType.PROPERTY, "The characteristic of a MOSFET is a gain stored before merge.",
                             status=LifecycleStatus.SUPERSEDED)
        rows.knowledge_occurrence(old, n.src_a, page=1, segment=n.seg_a, span=(600, 660), run=n.run_a)
        to_old = rows.edge(RelationType.HAS_PROPERTY, from_concept_id=n.mosfet_a.id, to_knowledge_id=old.id)
        rows.relationship_occurrence(to_old, n.src_a, page=1, segment=n.seg_a, span=(600, 660), run=n.run_a)
        rows.commit()
    return to_hidden.id, hidden.id, to_old.id, old.id


def _built(p) -> tuple:
    with closing(connect(p.index, read_only=True)) as index:
        return store.read_metadata(index), store.entry_rows(index), store.page_rows(index)


# ------------------------------------------------------------ the command set


def test_phase_9_adds_exactly_the_query_and_index_commands():
    before = {"start", "env", "config", "paths", "db", "extract", "classify", "lookup",
              "review", "edition", "merge", "version"}
    # Phase 10 adds `reason` (ADR 0040 P10-28; forced edit approved by the user on
    # 2026-09-25, PHASE_10.md section 13.2), and Phase 11 adds `calculate` (ADR 0043
    # P11-29, P11-30 edit 4; PHASE_11.md section 16), and Phase 12 adds `provenance` (ADR 0044
    # P12-15, P12-16); Phase 9's own additions are unchanged.
    assert set(cli_main._COMMANDS) - {"reason", "calculate", "provenance", "interpret", "act", "do", "procedure", "manual", "research", "diagram", "voice", "ask", "source", "solve", "inventory"} == before | {"query", "index"}


# ------------------------------------------------------------------ every mode


def test_every_mode_answers_in_json_with_exit_code_0(project, capsys):
    p, n = project, project.n
    assert _run(p, capsys, "index")[0] == 0

    code, concept = _json(p, capsys, "query", "--name", "MOSFET")
    assert code == 0 and concept["status"] == "FOUND" and concept["request"]["mode"] == "CONCEPT"
    assert [c["concept"]["id"] for c in concept["concept"]["concepts"]] == [n.mosfet_a.id, n.mosfet_b.id]
    assert concept["trace"]["database"]["read_only"] is True and concept["trace"]["persisted"] is False

    code, exact = _json(p, capsys, "query", n.definition.id)
    assert code == 0 and exact["exact"]["knowledge"]["knowledge"]["statement"] == n.definition.statement

    code, page = _json(p, capsys, "query", "--document", n.book_a.id, "--page", "1")
    assert code == 0 and [s["id"] for s in page["page"]["segments"]] == [n.seg_a.id]

    code, keyword = _json(p, capsys, "query", "--keyword", "impedance")
    assert code == 0 and keyword["request"]["mode"] == "KEYWORD"
    assert {h["knowledge"]["id"] for h in keyword["keyword"]["knowledge"]} == {n.property.id, n.rival.id}
    assert all(h["label"].startswith("KEYWORD HIT") for h in keyword["keyword"]["knowledge"])
    assert keyword["keyword"]["index"]["state"] == "FRESH"

    code, prefix = _json(p, capsys, "query", "--keyword", "imped", "--prefix")
    assert code == 0 and prefix["keyword"]["expression"] == '"imped"*'


def test_every_mode_answers_in_text_and_the_text_cites_every_evidence_row(project, capsys):
    p, n = project, project.n
    assert _run(p, capsys, "index")[0] == 0
    asks = {
        "concept": ("--name", "MOSFET"),
        "exact": (n.definition.id,),
        "page": ("--document", n.book_a.id, "--page", "1"),
        "keyword": ("--keyword", "impedance"),
    }
    for mode, args in asks.items():
        code, data = _json(p, capsys, "query", *args)
        code_text, text, _ = _run(p, capsys, "query", *args)
        assert code == code_text == 0, mode
        assert "Answer     : FOUND" in text and "never stored" in text, mode
        for identifier in data["trace"]["evidence_ids"]:
            assert identifier in text, (mode, identifier)
    _, text, _ = _run(p, capsys, "query", "--name", "MOSFET")
    for line in ('Query      : concept named "MOSFET"; scope MY_BOOKS', "[Definitions] 1",
                 "not automatically selected", "rule R1", "POSSIBLE equivalent", "Index      : not used"):
        assert line in text
    _, text, _ = _run(p, capsys, "query", "--keyword", "impedance")
    assert 'Searched   : "impedance"' in text and "KEYWORD HIT" in text


def test_a_name_that_is_not_stored_is_an_answer_with_exit_code_3(project, capsys):
    code, data = _json(project, capsys, "query", "--name", "Diode")
    assert code == 3 and data["status"] == "NOT_FOUND"
    code, _, err = _run(project, capsys, "query", "--name", "Diode")
    assert code == 3 and "RUDRA could not continue" not in err


def test_filters_and_scope_are_parsed_from_the_command_line(project, capsys):
    p, n = project, project.n
    code, only = _json(p, capsys, "query", "--name", "MOSFET", "--knowledge-type", "property")
    assert code == 0 and "KNOWLEDGE_TYPES" in json.dumps(only["request"]["filters"]).upper()
    groups = {g["name"]: [i["knowledge"]["id"] for i in g["items"] if "knowledge" in i]
              for g in only["concept"]["groups"]}
    assert groups["Definitions"] == [] and groups["Properties"] == [n.property.id, n.rival.id]
    code, wider = _json(p, capsys, "query", "--name", "MOSFET", "--scope", "authorized")
    assert code == 0 and wider["request"]["scope"] == "AUTHORIZED"
    code, none = _json(p, capsys, "query", "--name", "MOSFET", "--source-category", "local-source")
    assert code == 3 and none["status"] == "NOT_FOUND"
    code, narrow = _json(p, capsys, "query", "--name", "MOSFET", "--no-widen", "--exclude-superseded")
    assert code == 0 and narrow["possible_equivalents"] == []
    assert narrow["request"]["widen"] is False and narrow["request"]["include_superseded"] is False


# ------------------------------------------------------ scope and authorization


def test_unauthorised_evidence_is_never_returned_through_the_cli(project, capsys):
    p, n = project, project.n
    assert _run(p, capsys, "index")[0] == 0
    code, out, _ = _run(p, capsys, "query", "--name", "MOSFET", "--json")
    data = json.loads(out)
    assert code == 0 and HIDDEN not in out and "vacuum" not in out
    assert n.mosfet_c.id not in [c["concept"]["id"] for c in data["concept"]["concepts"]]
    assert data["withheld"]["unauthorized_evidence"] >= 1
    for args in ((n.hidden.id,), ("--document", n.book_c.id, "--page", "1"), ("--keyword", "vacuum")):
        for form in ((), ("--json",)):
            code, out, _ = _run(p, capsys, "query", *args, *form)
            assert code == 3, args
            assert HIDDEN not in out and "vacuum tube" not in out, args
            assert "INSUFFICIENT_AUTHORIZED_INFORMATION" in out, args


def test_an_exact_relationship_never_shows_an_endpoint_without_authorised_evidence(project, capsys):
    """The edge is stated in authorised book A and is shown; the object it reaches has
    evidence only in NOT_AUTHORIZED book C: withheld in JSON and in text alike (P9-5)."""
    p = project
    to_hidden, hidden_id, _, _ = _add_endpoint_cases(p)
    code, out, _ = _run(p, capsys, "query", to_hidden, "--json")
    data = json.loads(out)
    assert code == 0 and data["status"] == "FOUND"
    assert [end["id"] for end in data["exact"]["endpoints"]] == [p.n.mosfet_a.id]
    assert data["exact"]["relationship"]["relationship"]["to_knowledge_id"] == hidden_id  # as stored
    assert data["withheld"]["items_without_authorized_evidence"] == 1
    code, text, _ = _run(p, capsys, "query", to_hidden)
    assert code == 0 and f"Its endpoint {hidden_id} is withheld" in text
    for shown in (out, text):
        assert "known only to book C" not in shown


def test_an_exact_relationship_leaves_out_a_superseded_endpoint_when_asked(project, capsys):
    p = project
    _, _, to_old, old_id = _add_endpoint_cases(p)
    code, out, _ = _run(p, capsys, "query", to_old, "--json")
    assert code == 0 and "gain stored before merge" in out  # shown as stored by default (P9-16)
    for form in ((), ("--json",)):
        code, out, _ = _run(p, capsys, "query", to_old, "--exclude-superseded", *form)
        assert code == 0 and "gain stored before merge" not in out, form
        assert f"Its endpoint {old_id} is withheld: excluded by the request's filters" in out, form


# ----------------------------------------------------------------- the index


def test_index_builds_from_a_read_only_knowledge_database_and_reports_ingestion(project, capsys):
    p, n = project, project.n
    before = _sha256(p.db)
    code, built = _json(p, capsys, "index")
    assert code == 0
    assert built["knowledge_database"] == {"path": str(p.db), "read_only": True}
    assert built["index"]["path"] == str(p.index) and p.index.exists()
    assert built["index"]["status"]["state"] == "FRESH"
    assert built["index"]["size_bytes"] == p.index.stat().st_size
    documents = {d["document_id"]: d for d in built["documents"]}
    assert documents[n.book_a.id]["fully_ingested"] is True
    assert documents[n.book_b.id]["fully_ingested"] is False
    assert "no COMPLETED extraction run at extractor version 4 or later" in documents[n.book_b.id]["reason"]
    assert _sha256(p.db) == before
    assert not p.index.with_name("index.db.building").exists()

    code, text, _ = _run(p, capsys, "index")
    assert code == 0
    for line in ("Verified   : FRESH", "(opened read-only; not modified)", "Tokenizer  : unicode61 remove_diacritics 0",
                 f"Marker     : {built['index']['status']['build_marker']}",  # size, marker, duration (P9-31)
                 f"written {built['index']['size_bytes']} bytes", "Duration   :",
                 f"Document   : {n.book_a.id} book-a.pdf: fully ingested"):
        assert line in text
    assert _sha256(p.db) == before


def test_a_rebuild_is_deterministic_and_queries_give_the_same_bytes(project, capsys):
    p = project
    assert _run(p, capsys, "index")[0] == 0
    first = _built(p)
    answers = [_run(p, capsys, "query", *args)[:2] for args in
               (("--name", "MOSFET", "--json"), ("--name", "MOSFET"), ("--keyword", "mosfet", "--json"))]
    assert _run(p, capsys, "index")[0] == 0
    assert _built(p) == first
    again = [_run(p, capsys, "query", *args)[:2] for args in
             (("--name", "MOSFET", "--json"), ("--name", "MOSFET"), ("--keyword", "mosfet", "--json"))]
    assert again == answers


def test_a_failed_build_leaves_the_previous_index_in_place(project, capsys, monkeypatch):
    p = project
    assert _run(p, capsys, "index")[0] == 0
    index_before, db_before = _sha256(p.index), _sha256(p.db)

    def boom(connection, pages):
        raise RuntimeError("simulated failure while writing pages")

    monkeypatch.setattr(store, "insert_pages", boom)
    code, out, err = _run(p, capsys, "index")
    assert code == 70 and "UNKNOWN_ERROR" in err and out == ""
    monkeypatch.undo()
    assert _sha256(p.index) == index_before and _sha256(p.db) == db_before
    assert not p.index.with_name("index.db.building").exists()
    assert _run(p, capsys, "query", "--keyword", "mosfet")[0] == 0  # the old index is still fresh


def test_a_build_that_does_not_verify_is_not_put_in_place(project, capsys, monkeypatch):
    p = project
    assert _run(p, capsys, "index")[0] == 0
    index_before = _sha256(p.index)
    real = app.query.index._verify

    def incomplete(connection, path, marker):
        return real(connection, path, "not the marker that was written")

    monkeypatch.setattr(app.query.index, "_verify", incomplete)
    code, _, err = _run(p, capsys, "index")
    assert code == 5 and "did not verify" in err
    assert _sha256(p.index) == index_before
    assert not p.index.with_name("index.db.building").exists()


def test_a_build_without_the_disk_space_it_needs_is_refused_with_exit_code_4(project, capsys, monkeypatch):
    p = project
    full = functools.partial(app.query.build_index, disk_usage=lambda _: SimpleNamespace(free=0))
    monkeypatch.setattr(app.query, "build_index", full)
    code, out, err = _run(p, capsys, "index")
    assert code == 4 and "RESOURCE_LIMIT" in err and "nothing was written" in err and out == ""
    assert not p.index.exists() and not p.index.with_name("index.db.building").exists()


# ------------------------------------------ keyword refusal; no query-time index


def test_keyword_mode_refuses_a_missing_index_and_no_query_creates_one(project, capsys):
    p, n = project, project.n
    code, out, err = _run(p, capsys, "query", "--keyword", "mosfet")
    assert code == 5 and "MISSING" in err and out == ""
    for args in (("--name", "MOSFET"), (n.definition.id,), ("--document", n.book_a.id, "--page", "1"),
                 ("--keyword", "mosfet", "--prefix")):
        _run(p, capsys, "query", *args)
    assert not p.index.exists()
    assert not [f for f in p.indexes.iterdir() if f.name.startswith("index.db")]


def test_keyword_mode_refuses_a_stale_or_unreadable_index_and_never_repairs_it(project, capsys):
    p, n = project, project.n
    assert _run(p, capsys, "index")[0] == 0
    _add_concept(p, "Diode")  # knowledge.db changes after the build
    stale = _sha256(p.index)
    code, out, err = _run(p, capsys, "query", "--keyword", "mosfet")
    assert code == 5 and "STALE" in err and out == ""
    for args in (("--name", "Diode"), (n.definition.id,), ("--document", n.book_a.id, "--page", "1")):
        assert _run(p, capsys, "query", *args)[0] == 0  # the other modes do not need the index
    assert _sha256(p.index) == stale

    p.index.write_bytes(b"not an index at all " * 64)
    for name in ("index.db-wal", "index.db-shm"):
        p.indexes.joinpath(name).unlink(missing_ok=True)
    garbage = _sha256(p.index)
    code, _, err = _run(p, capsys, "query", "--keyword", "mosfet")
    assert code == 5 and "UNREADABLE" in err
    assert _sha256(p.index) == garbage

    assert _run(p, capsys, "index")[0] == 0  # only `index` rebuilds it
    code, data = _json(p, capsys, "query", "--keyword", "diode")
    assert code == 0 and [c["concept"]["canonical_name"] for c in data["keyword"]["concepts"]] == ["Diode"]
    _, text, _ = _run(p, capsys, "query", "--keyword", "diode")
    for identifier in data["trace"]["evidence_ids"]:  # the text cites a concept hit's evidence too
        assert identifier in text


# ------------------------------------------------------ nothing is written


def test_queries_never_change_the_knowledge_database(project, capsys):
    p, n = project, project.n
    assert _run(p, capsys, "index")[0] == 0
    before = _sha256(p.db)
    wal = p.db.with_name("knowledge.db-wal")
    wal_before = _sha256(wal) if wal.exists() else None
    for args in (("--name", "MOSFET"), ("--name", "Diode"), (n.definition.id,), (n.hidden.id,),
                 ("--document", n.book_a.id, "--page", "1"), ("--keyword", "mosfet"), ("--keyword", "imped", "--prefix")):
        for form in ((), ("--json",)):
            _run(p, capsys, "query", *args, *form)
    assert _sha256(p.db) == before
    assert (_sha256(wal) if wal.exists() else None) in (wal_before, hashlib.sha256(b"").hexdigest())


def test_the_query_path_never_migrates(project, capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a query or an index build must never migrate")

    monkeypatch.setattr(migrator, "migrate", forbidden)
    monkeypatch.setattr("app.storage.migrate", forbidden)
    assert _run(project, capsys, "query", "--name", "MOSFET")[0] == 0
    assert _run(project, capsys, "index")[0] == 0


# ------------------------------------------------ project root and storage


def test_a_project_without_a_database_is_refused_and_nothing_is_created(tmp_path, capsys):
    empty = _layout(tmp_path / "empty-project")
    for command in (("query", "--name", "MOSFET"), ("query", "--keyword", "mosfet"), ("index",)):
        code, out, err = _run(empty, capsys, *command)
        assert code == 5 and "DATABASE_FAILURE" in err and out == "", command
    assert not empty.db.exists() and not empty.index.exists()


def test_an_older_schema_is_refused_by_query_and_index_and_never_migrated(tmp_path, capsys):
    p = _layout(tmp_path / "old-project")
    p.db.parent.mkdir(parents=True)
    connection = connect(p.db)
    real = migrator.available_migrations()
    with mock.patch.object(migrator, "available_migrations",
                           lambda: tuple(m for m in real if m.version <= 4)), \
         mock.patch.object(migrator, "CODE_SCHEMA_VERSION", 4):
        migrate(connection, database_path=p.db)
    connection.commit()
    connection.close()
    before = _sha256(p.db)
    for command in (("query", "--name", "MOSFET"), ("index",)):
        code, _, err = _run(p, capsys, *command)
        assert code == 5 and "schema version is 4" in err, command
    assert _sha256(p.db) == before and not p.index.exists()
    with closing(sqlite3.connect(p.db)) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 4


def test_an_unexpected_error_exits_70(project, capsys, monkeypatch):
    def boom(self, request):
        raise RuntimeError("simulated failure inside the engine")

    monkeypatch.setattr(QueryEngine, "run", boom)
    code, out, err = _run(project, capsys, "query", "--name", "MOSFET")
    assert code == 70 and "UNKNOWN_ERROR" in err and out == ""


# ------------------------------------------------------------- invalid input


@pytest.mark.parametrize(
    "args",
    [
        ("query",),
        ("query", "--name", "MOSFET", "--keyword", "MOSFET"),
        ("query", "--name", "MOSFET", "K-00000001"),
        ("query", "--page", "1"),
        ("query", "--document", "DOC-00000001"),
        ("query", "--document", "DOC-00000001", "--page", "one"),
        ("query", "--document", "CPT-00000001", "--page", "1"),
        ("query", "--document", "DOC-00000001", "--page", "-1"),
        ("query", "not-an-identifier"),
        ("query", "SRC-00000001"),
        ("query", "--name", "   "),
        ("query", "--keyword", "   "),
        ("query", "--name", "MOSFET", "--prefix"),
        ("query", "--name", "MOSFET", "--scope", "everything"),
        ("query", "--name", "MOSFET", "--knowledge-type", "opinion"),
        ("query", "--name", "MOSFET", "--lifecycle", "forgotten"),
        ("query", "--name", "MOSFET", "--on-page", "first"),
        ("query", "--name", "MOSFET", "--in-run", "DOC-00000001"),
        ("query", "--name", "MOSFET", "--extractor-version", " "),
        ("index", "K-00000001"),
    ],
)
def test_invalid_input_is_refused_with_exit_code_2(project, capsys, args):
    before = _sha256(project.db)
    code, out, err = _run(project, capsys, *args)
    assert code == 2 and "INVALID_INPUT" in err and out == ""
    assert _sha256(project.db) == before and not project.index.exists()


def test_invalid_input_is_refused_before_any_database_is_opened(tmp_path, capsys):
    empty = _layout(tmp_path / "empty-project")
    for args in (("query", "--name", "  "), ("query", "--keyword", "x", "--name", "y"), ("index", "K-1")):
        code, _, err = _run(empty, capsys, *args)
        assert code == 2 and "INVALID_INPUT" in err, args
    assert not empty.db.exists()
