"""The Phase 10 `reason` command (Batch C; ADR 0040 P10-26 ... P10-28).

In process, over a temporary project whose `knowledge.db` holds section 201's graph and
one small case for each behaviour the command must carry through: a conflict with a
withheld claim, a cycle, an unauthorised target, a deleted requirement, a D2 twin, a D1
equation and a stored sentence claiming a derivation. Stored through the validating
repository (`tests/unit/reasoning_rows.py`). Every call names its scratch root with
--project-root; nothing here touches the live project. The engine's rules are unit-tested
in `tests/unit/test_reasoning_engine.py`; these tests check the command carries them.

Exit codes (ADR 0040 P10-28; Phase 7, P9-24): 0 answered - determined, or "cannot
determine" (section 230); 2 invalid request; 3 no concept answers to the target, or
insufficient authorized information; 5 the database cannot be used as it is; 70
anything unexpected.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest import mock

import pytest

import app.reasoning.engine
from app.models import Authorization, ConceptEquivalenceStatus, KnowledgeType, LifecycleStatus, RelationType
from app.storage import Repository, connect, migrate, migrator
from app.ui.cli import main as cli_main
from app.ui.cli.main import main
from tests.unit.query_rows import STATED
from tests.unit.reasoning_rows import ReasoningRows, section_201

HIDDEN = "Rivet is a part made in a closed workshop."
CLAIMED = "Yoke is derived from E, so Yoke is known whenever E is known."


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _layout(root) -> SimpleNamespace:
    data = root / "data"
    return SimpleNamespace(root=root, db=data / "database" / "knowledge.db",
                           indexes=data / "indexes", index=data / "indexes" / "index.db")


@pytest.fixture
def project(tmp_path) -> SimpleNamespace:
    p = _layout(tmp_path / "project")
    p.db.parent.mkdir(parents=True)
    (p.root / "data" / "backups").mkdir()
    connection = connect(p.db)
    migrate(connection, database_path=p.db)
    w = ReasoningRows(Repository(connection))
    n = section_201(w)
    n.def_d = w.knowledge("D is a stated quantity.")
    w.link(n.d, n.def_d)
    # A conflict whose other claim is from a source that is not authorised.
    n.quill, n.rivet = w.concept("Quill"), w.concept("Rivet")
    w.requires(n.quill, n.rivet)
    n.def_rivet = w.knowledge("Rivet is a fastening part.")
    w.link(n.rivet, n.def_rivet)
    n.hidden = w.knowledge(HIDDEN, w.other_source("closed", authorization=Authorization.NOT_AUTHORIZED))
    w.rows.conflict(n.def_rivet, n.hidden)
    # A cycle.
    n.mast, n.nock = w.concept("Mast"), w.concept("Nock")
    w.requires(n.mast, n.nock)
    w.requires(n.nock, n.mast)
    # A target whose only evidence is from a source that is not authorised.
    w.concept("Umbra", w.other_source("sealed", authorization=Authorization.NOT_AUTHORIZED))
    # A requirement on a deleted concept.
    n.lathe, n.gone = w.concept("Lathe"), w.concept("Gone")
    w.requires(n.lathe, n.gone)
    # D2: a concept stated equivalent to Solo has a requirement Solo never gets.
    n.solo, n.twin = w.concept("Solo"), w.concept("Twin")
    stated = w.edge(RelationType.EQUIVALENT_TO, from_concept_id=n.twin.id, to_concept_id=n.solo.id)
    w.rows.equivalence(n.twin, n.solo, status=ConceptEquivalenceStatus.POSSIBLE_EQUIVALENT,
                       basis=STATED, relationship=stated)
    w.requires(n.twin, n.e)
    # D1: an equation linked to a concept is never a requirement.
    n.pole = w.concept("Pole")
    equation = w.knowledge("Pole = E + 1", kind=KnowledgeType.EQUATION)
    w.edge(RelationType.REQUIRES, from_concept_id=n.pole.id, to_knowledge_id=equation.id)
    # A stored sentence claiming a derivation, linked to its concept.
    n.yoke = w.concept("Yoke")
    w.link(n.yoke, w.knowledge(CLAIMED, kind=KnowledgeType.CLAIM))
    w.commit()
    from dataclasses import replace

    Repository(connection).update(replace(n.gone, lifecycle_status=LifecycleStatus.DELETED))
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    p.n = n
    return p


def _run(p, capsys, *args) -> tuple[int, str, str]:
    code = main([*args, "--project-root", str(p.root)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _json(p, capsys, *args) -> tuple[int, dict]:
    code, out, err = _run(p, capsys, *args, "--json")
    assert out, err
    return code, json.loads(out)


def _states(method: dict) -> dict[str, str]:
    return {node["concept"]["canonical_name"]: node["state"] for node in method["nodes"]}


def _node(method: dict, name: str) -> dict:
    return next(node for node in method["nodes"] if node["concept"]["canonical_name"] == name)


def _paths(method: dict) -> list[list[tuple[str, str]]]:
    return [[(n["name"], n["state"]) for n in path["nodes"]] for path in method["paths"]]


# ------------------------------------------------------------ the command set


def test_phase_10_adds_exactly_the_reason_command():
    before = {"start", "env", "config", "paths", "db", "extract", "classify", "lookup", "review",
              "edition", "merge", "query", "index", "version"}
    # Phase 11 adds `calculate` (ADR 0043 P11-29, P11-30 edit 3; made under the user's
    # delegation of 2026-09-26, PHASE_11.md section 16), and Phase 12 adds `provenance` (ADR
    # 0044 P12-15, P12-16); Phase 10's own addition is unchanged.
    # Phase 13 adds `interpret` (ADR 0045 P13-14).
    assert set(cli_main._COMMANDS) - {"calculate", "provenance", "interpret", "act", "do", "procedure", "manual", "research", "diagram", "voice", "ask", "source", "solve", "inventory"} == before | {"reason"}


# ------------------------------------------------------- section 201 through the command


def test_the_literal_variant_through_the_command(project, capsys):
    code, data = _json(project, capsys, "reason", "X", "--input", "E")

    assert code == 0 and data["status"] == "CANNOT_DETERMINE"
    (method,) = data["methods"]
    assert _states(method) == {"X": "BLOCKED", "A": "BLOCKED", "B": "MISSING",
                               "C": "DERIVED", "D": "MISSING", "E": "AVAILABLE"}
    assert _paths(method) == [[("E", "AVAILABLE"), ("C", "DERIVED"), ("A", "BLOCKED"), ("X", "BLOCKED")]]
    n = project.n
    assert [(m["concept"]["id"], [r["concept_id"] for r in m["required_by"]]) for m in method["missing"]] == [
        (n.b.id, [n.x.id]), (n.d.id, [n.a.id])
    ]
    assert [(s["number"], s["concept_id"]) for s in method["steps"]] == [(1, n.c.id)]


def test_the_completed_variant_through_the_command(project, capsys):
    code, data = _json(project, capsys, "reason", "X", "--input", "E", "--input", "D", "--input", "B")

    n = project.n
    assert code == 0 and data["status"] == "DETERMINED"
    (method,) = data["methods"]
    assert method["state"] == "DERIVED" and method["missing"] == []
    assert [(s["number"], s["concept_id"], s["requirements"]) for s in method["steps"]] == [
        (1, n.c.id, [n.e.id]), (2, n.a.id, [n.c.id, n.d.id]), (3, n.x.id, [n.a.id, n.b.id])
    ]
    assert ["E", "C", "A", "X"] in [[name for name, _ in path] for path in _paths(method)]


def test_the_text_output_states_the_answer_the_path_and_what_is_missing(project, capsys):
    code, out, _ = _run(project, capsys, "reason", "X", "--input", "E")

    assert code == 0
    assert "Answer     : CANNOT_DETERMINE" in out
    assert "Path     : E [AVAILABLE] -> C [DERIVED] -> A [BLOCKED] -> X [BLOCKED]" in out
    assert f'Missing  : {project.n.b.id} "B" required by {project.n.x.id}' in out
    assert "Step 1   :" in out and "REQUIREMENT_SET v1" in out
    assert "reasoning never writes or migrates" in out


def test_admission_and_assumption_through_the_command(project, capsys):
    """OI-1 = Option B: D made available by admitting its stored definition; B assumed."""
    n = project.n
    code, data = _json(project, capsys, "reason", "X", "--input", "E=given", "--admit", f"D={n.def_d.id}",
                       "--assume", "B=B holds for this problem.")

    assert code == 0 and data["status"] == "DETERMINED"
    method = data["methods"][0]
    assert _node(method, "D")["origins"] == ["ADMITTED_STORED_ITEM"]
    assert _node(method, "D")["admitted"] == [n.def_d.id]
    assert _node(method, "B")["origins"] == ["ASSUMPTION"]
    assert _node(method, "X")["conditional_on"] == [n.b.id]
    initial = data["initial"]
    assert initial["inputs"][0]["value"] == "given"
    assert initial["admitted"][0]["provenance"]["sources"]  # stored provenance travels with it
    assert "provenance" not in initial["inputs"][0]  # a USER_INPUT never carries stored provenance


def test_forward_reasoning_through_the_command(project, capsys):
    n = project.n
    code, data = _json(project, capsys, "reason", "--forward", "--input", "E", "--input", "D", "--input", "B")

    assert code == 0 and data["status"] == "DETERMINED" and data["methods"] == []
    # Twin requires only E, so it is derived too; Solo, its stated equivalent, is not (D2).
    assert data["forward"]["derived"] == [n.c.id, n.a.id, n.x.id, n.twin.id]
    assert n.solo.id not in {node["concept"]["id"] for node in data["forward"]["nodes"]}


def test_a_direct_answer_through_the_command(project, capsys):
    code, data = _json(project, capsys, "reason", "X", "--input", "X")

    assert code == 0 and data["status"] == "DETERMINED"
    assert data["methods"][0]["state"] == "AVAILABLE" and data["methods"][0]["steps"] == []


# ------------------------------------------------------------- the carried rules


def test_a_conflict_with_a_withheld_claim_blocks_and_never_shows_that_claim(project, capsys):
    code, out, _ = _run(project, capsys, "reason", "Quill", "--input", "Rivet", "--json")

    assert code == 0
    method = json.loads(out)["methods"][0]
    rivet = _node(method, "Rivet")
    assert rivet["state"] == "CONFLICTING" and method["state"] == "BLOCKED"
    (conflict,) = rivet["conflicts"]
    assert conflict["claim_b"] == {"knowledge_id": project.n.hidden.id, "knowledge": None, "evidence": [],
                                   "provenance": {"sources": [], "documents": [], "runs": []},
                                   "superseded_by": None}
    assert conflict["resolution"] == "not automatically selected"
    assert HIDDEN not in out
    code, text, _ = _run(project, capsys, "reason", "Quill", "--input", "Rivet")
    assert f"{project.n.hidden.id} withheld" in text and HIDDEN not in text


def test_a_cycle_is_reported_through_the_command(project, capsys):
    code, data = _json(project, capsys, "reason", "Mast")

    assert code == 0 and data["status"] == "CANNOT_DETERMINE"
    method = data["methods"][0]
    assert method["cycles"] == [[project.n.mast.id, project.n.nock.id]]
    assert [b["reason"] for b in _node(method, "Mast")["blocks"]] == ["ON_CYCLE"]


def test_authorisation_and_not_found_answers_exit_3(project, capsys):
    code, data = _json(project, capsys, "reason", "Umbra")
    assert code == 3 and data["status"] == "INSUFFICIENT_AUTHORIZED_INFORMATION"
    code, data = _json(project, capsys, "reason", "Nowhere")
    assert code == 3 and data["status"] == "NOT_FOUND"


def test_a_deleted_requirement_blocks_through_the_command(project, capsys):
    code, data = _json(project, capsys, "reason", "Lathe")

    lathe = _node(data["methods"][0], "Lathe")
    assert code == 0 and lathe["state"] == "BLOCKED"
    assert [b["reason"] for b in lathe["blocks"]] == ["EXCLUDED_REQUIREMENT"]
    assert "Gone" not in _states(data["methods"][0])


@pytest.mark.parametrize("target", ["Solo", "Pole", "Yoke"])
def test_no_d2_widening_no_d1_attachment_and_no_retrieval_from_text(project, capsys, target):
    code, data = _json(project, capsys, "reason", target, "--input", "E")

    assert code == 0 and data["status"] == "CANNOT_DETERMINE"
    method = data["methods"][0]
    assert method["state"] == "MISSING" and method["steps"] == []
    assert [n["concept"]["canonical_name"] for n in method["nodes"]] == [target]


# -------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    "args",
    [
        ("reason",),  # neither a target nor --forward
        ("reason", "X", "--forward"),
        ("reason", "X", "--admit", "D"),
        ("reason", "X", "--admit", "D=K-12"),
        ("reason", "X", "--admit", "D=CPT-00000001"),
        ("reason", "X", "--admit", "D=K-99999999"),  # no such item
        ("reason", "X", "--input", "   "),
        ("reason", "X", "--input", "E="),
        ("reason", "X", "--input", "Nowhere"),
        ("reason", "X", "--assume", "Nowhere"),
        ("reason", "K-00000001"),  # a knowledge object is not a node
        ("reason", "X", "--scope", "everything"),
        ("reason", "X", "--name", "X"),
        ("reason", "X", "--keyword", "x"),
        ("reason", "X", "--in-run", "RUN-00000001"),
        ("reason", "X", "--run", "RUN-00000001"),
        ("reason", "--forward"),  # nothing to reason from
    ],
)
def test_an_invalid_request_is_refused_with_exit_code_2(project, capsys, args):
    before = _sha256(project.db)
    code, out, err = _run(project, capsys, *args)
    assert code == 2 and "INVALID_INPUT" in err and out == "", args
    assert _sha256(project.db) == before and not project.index.exists()


def test_an_invalid_request_is_refused_before_any_database_is_opened(tmp_path, capsys):
    empty = _layout(tmp_path / "empty-project")
    code, _, err = _run(empty, capsys, "reason", "X", "--admit", "D=K-12")
    assert code == 2 and "INVALID_INPUT" in err and not empty.db.exists()


def test_a_project_without_a_database_is_refused_and_nothing_is_created(tmp_path, capsys):
    empty = _layout(tmp_path / "empty-project")
    code, out, err = _run(empty, capsys, "reason", "X", "--input", "E")
    assert code == 5 and "DATABASE_FAILURE" in err and out == ""
    assert not empty.db.exists() and not empty.index.exists()


def test_an_older_schema_is_refused_and_never_migrated(tmp_path, capsys):
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
    code, _, err = _run(p, capsys, "reason", "X", "--input", "E")
    assert code == 5 and "schema version is 4" in err
    assert _sha256(p.db) == before
    with closing(sqlite3.connect(p.db)) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 4


def test_an_unexpected_error_exits_70(project, capsys, monkeypatch):
    def broken(self, request):
        raise RuntimeError("simulated")

    monkeypatch.setattr(app.reasoning.engine.ReasoningEngine, "reason", broken)
    code, out, err = _run(project, capsys, "reason", "X", "--input", "E")
    assert code == 70 and out == ""


# ---------------------------------------------------- read-only and determinism


def test_the_command_never_migrates_writes_or_opens_the_index(project, capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("reasoning must never migrate")

    monkeypatch.setattr(migrator, "migrate", forbidden)
    monkeypatch.setattr("app.storage.migrate", forbidden)
    before = _sha256(project.db)
    for args in (("X", "--input", "E"), ("X", "--input", "E", "--input", "D", "--input", "B"),
                 ("--forward", "--input", "E"), ("Quill", "--input", "Rivet"), ("Mast",)):
        assert _run(project, capsys, "reason", *args)[0] == 0
    assert _sha256(project.db) == before
    assert not project.index.exists()
    assert not project.indexes.exists() or list(project.indexes.iterdir()) == []
    with closing(sqlite3.connect(f"file:{project.db}?mode=ro", uri=True)) as check:
        counts = {t: check.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("derivation", "calculation", "query")}
    assert counts == {"derivation": 0, "calculation": 0, "query": 0}


def test_the_output_is_byte_identical_every_time(project, capsys):
    for args in (("X", "--input", "E", "--json"), ("X", "--input", "E"),
                 ("--forward", "--input", "E", "--input", "D", "--input", "B", "--json")):
        first = _run(project, capsys, "reason", *args)[:2]
        assert _run(project, capsys, "reason", *args)[:2] == first
