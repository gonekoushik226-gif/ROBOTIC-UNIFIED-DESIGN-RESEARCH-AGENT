"""Phase 6 acceptance: ADR 0026's fifteen tests, on real pipeline output.

Every fixture is a synthetic PDF taken through the **unchanged** Phase 4 ingestion
and Phase 5 extraction pipelines, so classification always reads what Phase 5
really stores. Part 5 section 193 is accepted on such a synthetic fixture (decision
P6-10); the real document is reported, not gated on (`PHASE_6.md`).

ADR 0026: "none may be satisfied by a friendly fixture alone where a whole-database
check is possible". The invariants below therefore run over every table of the
database, not over chosen rows.

These fixtures are Phase 6's own. The Phase 5 fixtures, whose invariants assert
that no relationship is INFERRED, are never classified (ADR 0030).
"""

from __future__ import annotations

import ast
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from unittest import mock

import pytest

from app.classification import (
    AREA_STATUS,
    Classifier,
    hierarchy_view,
    organisation_view,
    select_run,
)
from app.core.errors import InvalidInputError, RudraError
from app.documents import IngestionPipeline, PypdfParser
from app.extraction import EXTRACTOR_VERSION, ExtractionPipeline
from app.extraction.normalize import collapse
from app.knowledge import ConceptService, Evidence
from app.models import (
    ExtractionRunStatus,
    ExtractionTrigger,
    RelationshipInference,
    RelationshipOrigin,
    RelationType,
    normalize_alias,
)
from app.storage import Repository, connect, migrate, migrator, queries
from app.storage.repository import Repository as RepositoryClass
from app.ui.cli.main import main
from tests.conftest import PROJECT_ROOT
from tests.integration.test_phase5_extraction import TECHNICAL_PAGES
from tests.unit.pdf_fixtures import make_pdf

#: Part 5 section 193's shape. The source STATES a composition twice (V1: Parent,
#: Child, Child) and one definition NAMES a third concept (R1: Related concept).
SECTION_193_PAGES = [
    "Adder Circuits\n"
    "A full adder is a circuit that adds three input bits and produces a carry.\n"
    "A half adder is a circuit that adds two input bits together.\n"
    "An OR gate is a logic gate whose output is high when any input is high.\n"
    "A carry is a digit that is transferred to the next column of an addition.\n"
    "A full adder is composed of a half adder.\n"
    "A full adder is made up of an OR gate.",
]

#: A second document sharing concept names with the first. Its half adder's
#: definition names "carry" and "OR gate", which only the FIRST document defines.
SECOND_DOCUMENT_PAGES = [
    "Adder Circuits Revisited\n"
    "A half adder is a circuit that feeds its carry into an OR gate.\n"
    "An XOR gate is a logic gate used inside every half adder.",
]

#: Not ECE (Part 5 section 192: no hard-coded ECE hierarchy). States a type
#: relation and a part relation; its definitions name one another.
BIOLOGY_PAGES = [
    "Cell Biology\n"
    "A cell is the smallest unit of an organism that can live on its own.\n"
    "A mitochondrion is an organelle that releases energy inside a cell.\n"
    "An organelle is a specialised structure within a living cell.\n"
    "A ribosome is a type of organelle.\n"
    "The nucleus is part of a cell.\n"
    "A nucleus is a structure that holds the genetic material.\n"
    "A ribosome is a structure that builds proteins.",
]

#: A PARTIAL run (Phase 5 discards knowledge on the technical pages) that also has
#: structure rows, a stated prerequisite, and an R1 pair from the adder page.
PARTIAL_PAGES = TECHNICAL_PAGES + SECTION_193_PAGES

#: A document with no definition at all.
NO_DEFINITION_PAGES = [
    "Adder Notes\nThe sum bit is computed before the carry bit in every column.\nV = IR",
]

#: The only writes Phase 6 may make (ADR 0026, P6-8/P6-9). `id_sequence` is the
#: identifier allocator (ADR 0006), advanced for each new REL and RI row.
PERMITTED_WRITES = {
    ("INSERT", "relationship"),
    ("INSERT", "relationship_inference"),
    ("UPDATE", "id_sequence"),
}


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def world(tmp_path: pathlib.Path):
    """A migrated database plus the real ingestion, extraction and classification."""
    db_path = tmp_path / "data" / "database" / "knowledge.db"
    db_path.parent.mkdir(parents=True)
    connection = connect(db_path)
    migrate(connection, database_path=db_path)
    connection.commit()
    repository = Repository(connection)

    def ingest(pages: list[str], name: str = "book.pdf"):
        pdf = tmp_path / name
        pdf.write_bytes(make_pdf(pages))
        report = IngestionPipeline(repository, PypdfParser(), tmp_path / "documents").ingest(pdf)
        connection.commit()
        return report.document

    def extract(document_id: str, trigger=ExtractionTrigger.FIRST_EXTRACTION):
        pipeline = ExtractionPipeline(repository)
        run = pipeline.start(document_id, trigger=trigger)
        connection.commit()
        report = pipeline.execute(run)
        connection.commit()
        return report.run

    def classify(*, run_id=None, document_id=None):
        run = select_run(repository, run_id=run_id, document_id=document_id)
        return Classifier(repository).classify(run)

    yield {"connection": connection, "repository": repository, "ingest": ingest,
           "extract": extract, "classify": classify, "tmp": tmp_path, "db_path": db_path}
    connection.close()


@pytest.fixture
def adders(world):
    document = world["ingest"](SECTION_193_PAGES)
    run = world["extract"](document.id)
    return {**world, "document": document, "run": run}


def _rows(connection) -> dict[str, frozenset]:
    """Every row of every table - the row-level digest ADR 0026 test 1 asks for."""
    tables = [r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {t: frozenset(tuple(r) for r in connection.execute(f'SELECT * FROM "{t}"')) for t in tables}


def _count(connection, table: str, where: str = "1=1", params=()) -> int:
    return connection.execute(f"SELECT count(*) FROM {table} WHERE {where}", params).fetchone()[0]


def _run_concepts(connection, run_id: str) -> set[str]:
    return {c.id for c in queries.concepts_of_run(connection, run_id)}


def _name(connection, concept_id: str) -> str:
    return connection.execute(
        "SELECT canonical_name FROM concept WHERE id = ?", (concept_id,)).fetchone()[0]


def _inferred_edges(connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM relationship WHERE origin = 'INFERRED' ORDER BY id").fetchall()


def _named_pairs(connection) -> set[tuple[str, str]]:
    return {
        tuple(sorted((_name(connection, e["from_concept_id"]), _name(connection, e["to_concept_id"]))))
        for e in _inferred_edges(connection)
    }


@contextmanager
def _recorded(connection):
    """Every table read and every write SQLite authorises while the block runs."""
    reads: set[str] = set()
    writes: set[tuple[str, str]] = set()
    kinds = {sqlite3.SQLITE_INSERT: "INSERT", sqlite3.SQLITE_UPDATE: "UPDATE",
             sqlite3.SQLITE_DELETE: "DELETE"}

    def authorise(action, first, second, database, source):
        if action == sqlite3.SQLITE_READ and first:
            reads.add(first)
        elif action in kinds:
            writes.add((kinds[action], first))
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorise)
    try:
        yield reads, writes
    finally:
        connection.set_authorizer(None)


# ------------------------------------------ 1. no Phase 5 row changes


def test_01_no_phase_5_row_changes_and_only_inferred_edges_and_bases_are_added(adders):
    connection = adders["connection"]
    before = _rows(connection)
    counters_before = dict(connection.execute("SELECT entity_kind, next_value FROM id_sequence"))

    report = adders["classify"](run_id=adders["run"].id)

    after = _rows(connection)
    for table, rows in before.items():
        if table in ("relationship", "relationship_inference", "id_sequence"):
            continue
        assert after[table] == rows, table
    assert before["relationship"] <= after["relationship"]  # nothing changed or removed
    new_ids = {row[0] for row in after["relationship"] - before["relationship"]}
    assert len(new_ids) == report.edges_created == 1
    for edge_id in new_ids:
        edge = connection.execute("SELECT * FROM relationship WHERE id = ?", (edge_id,)).fetchone()
        assert (edge["relation_type"], edge["origin"], edge["lifecycle_status"]) == (
            "RELATED_TO", "INFERRED", "ACTIVE")
    assert before["relationship_inference"] == frozenset()
    assert len(after["relationship_inference"]) == report.basis_rows_added == 1
    counters_after = dict(connection.execute("SELECT entity_kind, next_value FROM id_sequence"))
    changed = {k for k in counters_after if counters_after[k] != counters_before[k]}
    assert changed == {"REL", "RI"}


def test_01b_classification_writes_only_edges_bases_and_counters(adders):
    """Asked of SQLite itself: no write to document, document_version, extraction_run,
    concept, alias, knowledge object or any lifecycle column - no UPDATE or DELETE of
    any knowledge row at all."""
    connection = adders["connection"]
    with _recorded(connection) as (reads, writes):
        adders["classify"](run_id=adders["run"].id)
    assert writes <= PERMITTED_WRITES, writes - PERMITTED_WRITES
    assert writes == PERMITTED_WRITES  # the recorder really records
    assert {"concept", "concept_alias", "source_occurrence", "relationship"} <= reads


# ---------------------------------------- 2. no Phase 5 semantics change


def test_02_phase_5_semantics_are_unchanged(world):
    """The extractor still creates no INFERRED edge on its own. Its version is 4 since
    Phase 8's stage 15 (decision P8-7, ADR 0032); Phase 6 never changed it."""
    assert EXTRACTOR_VERSION == "6"  # v6: PDF equations rebuilt from glyph layout carry their own certainty
    document = world["ingest"](SECTION_193_PAGES)
    world["extract"](document.id)
    connection = world["connection"]
    assert _count(connection, "relationship", "origin <> 'EXPLICIT'") == 0
    assert _count(connection, "relationship_inference") == 0


def test_02b_classification_imports_neither_the_parser_nor_the_extractor():
    """ADR 0030: stored output is read through app.storage; nothing re-reads text.
    (The package-level rule is also in test_import_boundaries.py.)"""
    for path in sorted((PROJECT_ROOT / "app" / "classification").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        }
        for module in imported:
            assert not module.startswith(("app.documents", "app.extraction", "pypdf")), (path, module)


# --------------------------------------------------- 3. no cross-run edges


def test_03_classifying_one_run_touches_only_that_runs_concepts(world):
    connection = world["connection"]
    document = world["ingest"](SECTION_193_PAGES)
    first = world["extract"](document.id)
    second = world["extract"](document.id, trigger=ExtractionTrigger.USER_REQUESTED)
    run_1, run_2 = _run_concepts(connection, first.id), _run_concepts(connection, second.id)
    assert run_1 and run_2 and not run_1 & run_2  # I-A: each run has its own concepts

    world["classify"](run_id=second.id)
    for edge in _inferred_edges(connection):
        assert {edge["from_concept_id"], edge["to_concept_id"]} <= run_2
    for basis in connection.execute(
        "SELECT s.extraction_run_id FROM relationship_inference i "
        "JOIN source_occurrence s ON s.id = i.basis_occurrence_id"
    ):
        assert basis[0] == second.id

    report = world["classify"](run_id=first.id)
    for edge in _inferred_edges(connection):
        ends = {edge["from_concept_id"], edge["to_concept_id"]}
        assert ends <= run_1 or ends <= run_2  # never one end in each run
    # Phase 8 (ADR 0033, "Consequence for Phase 6"): the re-extraction linked each
    # definition to run 1's object, so it has a DEFINED_BY edge from both runs'
    # concepts and R1 skips it as direction-ambiguous - counted and reported, never
    # guessed. Before stage 15 this was one Full adder--Carry edge per run.
    assert len(_inferred_edges(connection)) == 0
    assert report.definitions_ambiguous


# ---------------------------------------------- 4. no cross-document identity


def test_04_a_second_document_sharing_names_is_never_touched(world):
    connection = world["connection"]
    first = world["extract"](world["ingest"](SECTION_193_PAGES, "a.pdf").id)
    second = world["extract"](world["ingest"](SECOND_DOCUMENT_PAGES, "b.pdf").id)
    concepts_1 = _run_concepts(connection, first.id)
    concepts_2 = _run_concepts(connection, second.id)
    # Both documents define a "Half adder"; they are two different concepts.
    assert {_name(connection, c) for c in concepts_1} & {_name(connection, c) for c in concepts_2}

    world["classify"](run_id=second.id)
    # The half adder of document 2 names "carry" and "OR gate", which only document 1
    # defines: no edge may reach across. Only its own pair is found.
    assert _named_pairs(connection) == {("Half adder", "XOR gate")}
    world["classify"](run_id=first.id)
    for edge in _inferred_edges(connection):
        ends = {edge["from_concept_id"], edge["to_concept_id"]}
        assert ends <= concepts_1 or ends <= concepts_2


# -------------------------------------------------------- 5. no deduplication


def test_05_nothing_is_deduplicated_merged_or_created(world):
    """Two documents both define "Half adder"; classification merges nothing."""
    connection = world["connection"]
    first = world["extract"](world["ingest"](SECTION_193_PAGES, "a.pdf").id)
    second = world["extract"](world["ingest"](SECOND_DOCUMENT_PAGES, "b.pdf").id)
    tables = ("concept", "concept_alias", "concept_occurrence", "knowledge_object",
              "source_occurrence", "equation", "variable", "rule", "procedure")
    counts = {t: _count(connection, t) for t in tables}
    names = [r[0] for r in connection.execute("SELECT canonical_name FROM concept")]
    assert names.count("Half adder") == 2  # two concepts, one name, never merged

    reports = [world["classify"](run_id=run.id) for run in (first, second)]
    assert sum(r.edges_created for r in reports) == 2
    assert {t: _count(connection, t) for t in tables} == counts
    assert [r[0] for r in connection.execute("SELECT canonical_name FROM concept")] == names
    assert _count(connection, "knowledge_object", "normalized_hash IS NOT NULL") == 0
    assert _count(connection, "relationship", "relation_type = 'EQUIVALENT_TO'") == 0
    assert {e["relation_type"] for e in _inferred_edges(connection)} == {"RELATED_TO"}


# ---------------------------------------------------------- 6. no supersession


def test_06_no_lifecycle_status_changes_anywhere(adders):
    connection = adders["connection"]
    tables = [r[0] for r in connection.execute(
        "SELECT m.name FROM sqlite_master m WHERE m.type = 'table' AND EXISTS "
        "(SELECT 1 FROM pragma_table_info(m.name) p WHERE p.name = 'lifecycle_status')")]

    def statuses():
        return {t: sorted(connection.execute(
            f'SELECT lifecycle_status, count(*) FROM "{t}" GROUP BY 1').fetchall()) for t in tables}

    before = statuses()
    adders["classify"](run_id=adders["run"].id)
    after = statuses()
    for table in tables:
        if table == "relationship":
            continue
        assert after[table] == before[table], table
    for table in tables:
        assert _count(connection, table, "lifecycle_status IN ('SUPERSEDED', 'STALE')") == 0


# ------------------------------------------------------ 7. no conflict resolution


def test_07_no_conflict_and_no_contradiction(adders):
    connection = adders["connection"]
    adders["classify"](run_id=adders["run"].id)
    assert _count(connection, "conflict") == 0
    assert _count(connection, "relationship", "relation_type = 'CONTRADICTS'") == 0


# ------------------------------------------------------- 8. every edge has a basis


def test_08_every_inferred_edge_has_a_basis_that_resolves_to_the_page_and_span(world):
    connection = world["connection"]
    for pages, name in ((SECTION_193_PAGES, "a.pdf"), (BIOLOGY_PAGES, "b.pdf")):
        world["classify"](run_id=world["extract"](world["ingest"](pages, name).id).id)

    integrity = queries.graph_integrity(connection)
    assert integrity.inferred_without_basis == ()
    assert integrity.is_clean
    edges = _inferred_edges(connection)
    assert edges
    for edge in edges:
        assert _count(connection, "relationship_inference", "relationship_id = ?", (edge["id"],)) >= 1

    chain = connection.execute(
        "SELECT i.matched_text, s.original_text, s.char_start, s.char_end, g.text, "
        "       s.page_number, g.page_number AS segment_page, d.file_hash, x.status, "
        "       s.knowledge_id, r.from_concept_id, r.to_concept_id "
        "FROM relationship_inference i "
        "JOIN relationship r ON r.id = i.relationship_id "
        "JOIN source_occurrence s ON s.id = i.basis_occurrence_id "
        "JOIN extraction_run x ON x.id = s.extraction_run_id "
        "JOIN document d ON d.id = x.document_id AND d.id = s.document_id "
        "JOIN document_segment g ON g.id = s.segment_id"
    ).fetchall()
    assert len(chain) == _count(connection, "relationship_inference")
    for row in chain:
        assert row["file_hash"] and row["page_number"] == row["segment_page"]
        assert collapse(row["text"][row["char_start"]:row["char_end"]]) == collapse(row["original_text"])
        assert row["status"] in ("COMPLETED", "PARTIAL")
        # The mention's direction: the basis is the definition of one endpoint, and
        # it names the other.
        (mentioning,) = queries.concepts_defined_by(connection, row["knowledge_id"])
        ends = {row["from_concept_id"], row["to_concept_id"]}
        assert mentioning in ends
        (mentioned,) = ends - {mentioning}
        # I6-B: the stored text is exactly what the source printed, and it is the
        # mentioned concept's name.
        assert row["matched_text"] in row["original_text"]
        assert normalize_alias(row["matched_text"]) == normalize_alias(_name(connection, mentioned))


def test_08b_an_inferred_edge_written_behind_classification_is_reported_not_counted(adders):
    """ADR 0027: `inferred_without_basis` is separate, and `is_clean` keeps its
    Phase 3 meaning - the Phase 3 service may create INFERRED edges without a basis."""
    connection, repository = adders["connection"], adders["repository"]
    concepts = sorted(_run_concepts(connection, adders["run"].id))
    edge = ConceptService(repository).attach_relationship(
        relation_type=RelationType.SIMILAR_TO, origin=RelationshipOrigin.INFERRED,
        from_concept_id=concepts[0], to_concept_id=concepts[1],
    )
    connection.commit()
    report = queries.graph_integrity(connection)
    assert report.inferred_without_basis == (edge.id,)
    assert report.is_clean


# ----------------------------------------------------- 9. deterministic repeats


def test_09_classifying_the_same_run_twice_adds_nothing(adders):
    connection = adders["connection"]
    first = adders["classify"](run_id=adders["run"].id)
    digest = _rows(connection)
    second = adders["classify"](run_id=adders["run"].id)
    assert (second.edges_created, second.basis_rows_added) == (0, 0)
    assert second.bases_already_recorded == first.basis_rows_added == 1
    assert [p.outcome for p in second.pairs] == ["ALREADY_RECORDED"]
    assert _rows(connection) == digest


def test_09b_a_fresh_database_with_the_same_input_yields_identical_edges_and_bases(tmp_path):
    def build(root: pathlib.Path):
        root.mkdir()
        db = root / "knowledge.db"
        connection = connect(db)
        migrate(connection, database_path=db)
        connection.commit()
        repository = Repository(connection)
        for pages, name in ((SECTION_193_PAGES, "a.pdf"), (BIOLOGY_PAGES, "b.pdf")):
            pdf = tmp_path / name
            pdf.write_bytes(make_pdf(pages))
            document = IngestionPipeline(repository, PypdfParser(), root / "documents").ingest(pdf).document
            connection.commit()
            pipeline = ExtractionPipeline(repository)
            run = pipeline.start(document.id)
            connection.commit()
            pipeline.execute(run)
            connection.commit()
            Classifier(repository).classify(select_run(repository, run_id=run.id, document_id=None))
        edges = connection.execute(
            "SELECT id, relation_type, origin, lifecycle_status, from_concept_id, to_concept_id "
            "FROM relationship WHERE origin = 'INFERRED' ORDER BY id").fetchall()
        bases = connection.execute(
            "SELECT id, relationship_id, rule, rule_version, basis_occurrence_id, matched_text "
            "FROM relationship_inference ORDER BY id").fetchall()
        result = ([tuple(e) for e in edges], [tuple(b) for b in bases], _named_pairs(connection))
        connection.close()
        return result

    one, two = build(tmp_path / "one"), build(tmp_path / "two")
    assert one == two
    assert one[2] == {
        ("Carry", "Full adder"),
        ("Cell", "Mitochondrion"), ("Mitochondrion", "Organelle"),
        ("Cell", "Organelle"), ("Organelle", "Ribosome"),
    }


# ------------------------------------------- 10. no unsupported domain hierarchy


def test_10_a_non_ece_document_behaves_the_same_and_nothing_hierarchical_is_inferred(world):
    connection = world["connection"]
    document = world["ingest"](BIOLOGY_PAGES)
    run = world["extract"](document.id)
    concepts_before = _count(connection, "concept")
    report = world["classify"](run_id=run.id)
    assert report.edges_created == 4
    assert _count(connection, "concept") == concepts_before  # no category seeded
    assert {e["relation_type"] for e in _inferred_edges(connection)} == {"RELATED_TO"}
    assert _count(connection, "relationship", "origin = 'INFERRED' AND relation_type <> 'RELATED_TO'") == 0
    # The stated type and part relations stay exactly as the source stated them.
    stated = {
        (_name(connection, r["from_concept_id"]), r["relation_type"], _name(connection, r["to_concept_id"]))
        for r in connection.execute(
            "SELECT * FROM relationship WHERE origin = 'EXPLICIT' AND to_concept_id IS NOT NULL")
    }
    assert stated == {("Ribosome", "INSTANCE_OF", "Organelle"), ("Nucleus", "PART_OF", "Cell")}


def test_10b_a_document_with_no_definitions_yields_no_edges(world):
    connection = world["connection"]
    run = world["extract"](world["ingest"](NO_DEFINITION_PAGES).id)
    report = world["classify"](run_id=run.id)
    assert (report.concepts, report.definitions_read, report.edges_created) == (0, 0, 0)
    assert _count(connection, "relationship") == 0
    assert _count(connection, "relationship_inference") == 0


def test_10c_no_parent_of_is_inferred_from_an_is_a_definition(world):
    """'A mitochondrion is an organelle ...' names a genus. The source states it, so
    turning it into PARENT_OF would be an EXPLICIT extraction, not Phase 6 inference
    (ADR 0028, Rejected rules). R1 records only that the definition names it."""
    connection = world["connection"]
    world["classify"](run_id=world["extract"](world["ingest"](BIOLOGY_PAGES).id).id)
    assert _count(connection, "relationship", "relation_type IN ('PARENT_OF', 'CHILD_OF')") == 0


# --------------------------------------- 11. no LLM, no embeddings, no network


def test_11_classification_makes_no_network_call(adders, monkeypatch):
    attempts: list[str] = []

    def refuse(name):
        def _refuse(*args, **kwargs):
            attempts.append(name)
            raise OSError(f"network access attempted: {name}")
        return _refuse

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, refuse(f"socket.socket.{name}"))
    monkeypatch.setattr(socket, "create_connection", refuse("socket.create_connection"))
    report = adders["classify"](run_id=adders["run"].id)
    hierarchy_view(adders["repository"], adders["run"].id)
    organisation_view(adders["repository"], adders["run"].id)
    assert attempts == []
    assert report.edges_created == 1


def test_11b_classification_imports_only_the_standard_library_and_app():
    stdlib = set(sys.stdlib_module_names)
    for path in sorted((PROJECT_ROOT / "app" / "classification").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for name in names:
                root = name.split(".")[0]
                assert root in stdlib or root == "app", (path.name, name)


# ------------------------------------------------ 12. migration safety (ADR 0027)
# In tests/integration/test_phase6_migration.py: fresh 0->5, 1..4->5, a populated
# version-4 copy, a deliberate failure at the last statement, counters, backup.


# --------------------------------------------- 13. section 193, demonstrated


def test_13_section_193_parent_child_child_and_an_inferred_related_concept(adders):
    connection, repository, run = adders["connection"], adders["repository"], adders["run"]
    report = adders["classify"](run_id=run.id)
    assert run.status is ExtractionRunStatus.COMPLETED and not report.partial

    # V1: the source states the composition; nothing in it is inferred.
    hierarchy = hierarchy_view(repository, run.id)
    (root,) = hierarchy.roots
    assert root.concept.canonical_name == "Full adder" and root.via is None
    children = [(n.concept.canonical_name, n.via.relationship.relation_type, n.via.origin)
                for n in root.children]
    assert children == [("Half adder", "COMPOSED_OF", "EXPLICIT"), ("OR gate", "COMPOSED_OF", "EXPLICIT")]
    for node in root.children:
        (occurrence,) = node.via.occurrences  # backed by the sentence that states it
        assert "composed of" in occurrence.original_text or "made up of" in occurrence.original_text
        assert node.via.bases == ()

    # V2: the related concept is inferred, and its basis says from what.
    organisation = organisation_view(repository, run.id)
    (related,) = organisation.group("Related concepts")
    assert related.origin is RelationshipOrigin.INFERRED
    ends = {related.relationship.from_concept_id, related.relationship.to_concept_id}
    assert {_name(connection, c) for c in ends} == {"Full adder", "Carry"}
    (basis,) = related.bases
    assert related.occurrences == ()  # no source states this relation
    assert (basis.inference.rule, basis.inference.rule_version, basis.inference.matched_text) == (
        "R1", "1", "carry")
    assert [_name(connection, c) for c in basis.mentioning_concept_ids] == ["Full adder"]
    assert basis.occurrence.original_text.startswith("A full adder is a circuit")
    for group in ("Prerequisites", "Dependencies", "Applications"):
        assert organisation.group(group) == ()  # none stated, none inferred (P6-7)


def test_13b_the_cli_draws_the_section_193_structure(tmp_path, monkeypatch, capsys):
    for key in [k for k in os.environ if k.startswith("RUDRA_")]:
        monkeypatch.delenv(key)
    project = tmp_path / "project"
    project.mkdir()
    pdf = tmp_path / "adders.pdf"
    pdf.write_bytes(make_pdf(SECTION_193_PAGES))
    assert main(["extract", str(pdf), "--project-root", str(project)]) == 0
    capsys.readouterr()

    assert main(["classify", "--run", "RUN-00000001", "--project-root", str(project)]) == 0
    out = capsys.readouterr().out
    tree = [line for line in out.splitlines() if line.startswith("  ") and (
        line.strip() == "Full adder" or line.lstrip().startswith(("|-- ", "`-- ")))]
    assert tree[0] == "  Full adder"
    assert tree[1].startswith("  |-- Half adder    [COMPOSED_OF, EXPLICIT - stated: RO-")
    assert tree[2].startswith("  |-- OR gate    [COMPOSED_OF, EXPLICIT - stated: RO-")
    assert tree[3].startswith("  `-- Carry    [RELATED_TO, INFERRED - R1 v1: the definition of Full adder (S-")
    assert tree[3].endswith('names "carry"]')
    assert "Committed  : 1 new INFERRED RELATED_TO edges, 1 new basis rows" in out
    assert "0 skipped: their exact source text could not be proved" in out
    assert "Run status : COMPLETED" in out
    for area, status in AREA_STATUS:
        assert area in out and status in out


# ------------------------------------------------------ 14. failure behaviour


def test_14_ambiguous_unknown_missing_and_unfinished_runs_are_refused_with_nothing_written(world):
    connection, repository = world["connection"], world["repository"]
    twice = world["ingest"](SECTION_193_PAGES, "twice.pdf")
    world["extract"](twice.id)
    world["extract"](twice.id, trigger=ExtractionTrigger.USER_REQUESTED)
    never = world["ingest"](BIOLOGY_PAGES, "never.pdf")
    unfinished = world["ingest"](NO_DEFINITION_PAGES, "unfinished.pdf")
    pipeline = ExtractionPipeline(repository)
    running = pipeline.start(unfinished.id)  # recorded, committed, never executed
    connection.commit()
    digest = _rows(connection)

    def refused(**selection) -> RudraError:
        with pytest.raises(InvalidInputError) as caught:
            world["classify"](**selection)
        report = caught.value.report
        assert report.data_changed is False and report.retry_safe is True
        assert report.next_options and report.stage == "classification.select_run"
        return caught.value

    both = refused(run_id="RUN-00000001", document_id=twice.id)
    assert "Both a run and a document" in both.report.summary
    assert "No extraction run was named" in refused().report.summary
    assert "no extraction run with that identifier" in refused(run_id="RUN-99999999").report.summary
    assert "no extraction run with that identifier" in refused(run_id=twice.id).report.summary
    assert "no document with that identifier" in refused(document_id="DOC-99999999").report.summary
    ambiguous = refused(document_id=twice.id).report
    assert "more than one extraction run" in ambiguous.summary
    assert len(ambiguous.available) == 2  # every run listed, with its details
    assert all("trigger" in a and f"extractor v{EXTRACTOR_VERSION}" in a and "status" in a
               for a in ambiguous.available)
    assert "never extracted" in refused(document_id=never.id).report.reason
    assert "is RUNNING" in refused(run_id=running.id).report.reason
    assert "is RUNNING" in refused(document_id=unfinished.id).report.reason
    assert _rows(connection) == digest

    pipeline.mark_failed(running)  # what the CLI records when extraction fails
    connection.commit()
    digest = _rows(connection)
    assert "is FAILED" in refused(run_id=running.id).report.reason
    assert _rows(connection) == digest


def test_14b_the_document_form_is_accepted_only_when_it_has_exactly_one_run(adders):
    report = adders["classify"](document_id=adders["document"].id)
    assert report.run.id == adders["run"].id and report.edges_created == 1


def test_14c_a_partial_run_is_classified_and_its_status_disclosed(world, tmp_path, monkeypatch, capsys):
    run = world["extract"](world["ingest"](PARTIAL_PAGES).id)
    assert run.status is ExtractionRunStatus.PARTIAL  # genuinely partial, from Phase 5
    report = world["classify"](run_id=run.id)
    assert report.partial and report.basis_rows_added >= 1
    assert report.issue_counts == queries.issue_counts_for_run(world["connection"], run.id)
    assert sum(report.issue_counts.values()) > 0
    # The disclosure is also reconstructable later from the data, through the basis.
    for row in world["connection"].execute(
        "SELECT x.status FROM relationship_inference i "
        "JOIN source_occurrence s ON s.id = i.basis_occurrence_id "
        "JOIN extraction_run x ON x.id = s.extraction_run_id"
    ):
        assert row[0] == "PARTIAL"

    for key in [k for k in os.environ if k.startswith("RUDRA_")]:
        monkeypatch.delenv(key)
    project = tmp_path / "project"
    project.mkdir()
    pdf = tmp_path / "technical.pdf"
    pdf.write_bytes(make_pdf(PARTIAL_PAGES))
    assert main(["extract", str(pdf), "--project-root", str(project)]) == 0
    capsys.readouterr()
    assert main(["classify", "--run", "RUN-00000001", "--project-root", str(project)]) == 0
    out = capsys.readouterr().out
    assert "Run status : PARTIAL" in out
    assert "This run is PARTIAL" in out and "what it discarded is absent from it" in out
    assert "Run issues : recorded by that extraction run" in out


def test_14d_a_failure_while_writing_leaves_nothing_behind(adders, monkeypatch):
    """ADR 0027: an edge is never visible without its basis. Fail on the basis write,
    after the edge was inserted, and the whole classification must roll back."""
    connection = adders["connection"]
    digest = _rows(connection)
    original = RepositoryClass.add

    def failing_add(self, entity):
        if isinstance(entity, RelationshipInference):
            raise sqlite3.OperationalError("disk I/O error (simulated)")
        return original(self, entity)

    monkeypatch.setattr(RepositoryClass, "add", failing_add)
    with pytest.raises(sqlite3.OperationalError):
        adders["classify"](run_id=adders["run"].id)
    assert not connection.in_transaction
    assert _rows(connection) == digest
    monkeypatch.setattr(RepositoryClass, "add", original)
    assert adders["classify"](run_id=adders["run"].id).edges_created == 1  # a retry is safe


def test_14e_classification_will_not_start_on_top_of_uncommitted_work(adders):
    connection = adders["connection"]
    connection.execute("UPDATE database_metadata SET value = value WHERE key = 'role'")
    assert connection.in_transaction
    with pytest.raises(InvalidInputError, match="uncommitted work"):
        Classifier(adders["repository"]).classify(adders["run"])
    assert connection.in_transaction  # the caller's work was neither committed nor lost
    connection.rollback()


def test_14f_the_cli_refuses_with_an_actionable_report(adders, tmp_path, monkeypatch, capsys):
    for key in [k for k in os.environ if k.startswith("RUDRA_")]:
        monkeypatch.delenv(key)
    project = tmp_path / "project"
    project.mkdir()
    assert main(["classify", "--project-root", str(project)]) == 2
    err = capsys.readouterr().err
    assert "No extraction run was named" in err and "Data changed: no" in err
    assert "python -m app classify --run <RUN-id>" in err


# ------------------------------------------------ R1 output rule (ADR 0028)


def test_r1_stands_down_where_a_source_already_states_related_to(adders):
    connection, repository = adders["connection"], adders["repository"]
    by_name = {_name(connection, c): c for c in _run_concepts(connection, adders["run"].id)}
    document = adders["document"]
    source = queries.sources_for_document(connection, document.id)[0]
    # A stated RELATED_TO, reversed relative to the canonical order on purpose.
    ConceptService(repository).attach_relationship(
        relation_type=RelationType.RELATED_TO, origin=RelationshipOrigin.EXPLICIT,
        from_concept_id=max(by_name["Carry"], by_name["Full adder"]),
        to_concept_id=min(by_name["Carry"], by_name["Full adder"]),
        evidence=Evidence(source_id=source.id, document_id=document.id,
                          text="A carry is related to a full adder."),
    )
    connection.commit()
    report = adders["classify"](run_id=adders["run"].id)
    assert (report.edges_created, report.basis_rows_added, report.pairs_skipped_explicit) == (0, 0, 1)
    assert [p.outcome for p in report.pairs] == ["EXPLICIT_EXISTS"]
    assert _inferred_edges(connection) == []
    assert _count(connection, "relationship_inference") == 0


def test_r1_adds_a_basis_to_an_existing_inferred_edge_in_either_direction(adders):
    connection, repository = adders["connection"], adders["repository"]
    by_name = {_name(connection, c): c for c in _run_concepts(connection, adders["run"].id)}
    a, b = sorted((by_name["Carry"], by_name["Full adder"]))
    existing = ConceptService(repository).attach_relationship(
        relation_type=RelationType.RELATED_TO, origin=RelationshipOrigin.INFERRED,
        from_concept_id=b, to_concept_id=a,  # the non-canonical direction
    )
    connection.commit()
    assert queries.graph_integrity(connection).inferred_without_basis == (existing.id,)
    report = adders["classify"](run_id=adders["run"].id)
    assert (report.edges_created, report.basis_rows_added) == (0, 1)
    assert [p.outcome for p in report.pairs] == ["BASIS_ADDED"]
    assert [e["id"] for e in _inferred_edges(connection)] == [existing.id]
    assert queries.graph_integrity(connection).inferred_without_basis == ()


def test_r1_stores_one_edge_per_unordered_pair_in_canonical_order(world):
    connection = world["connection"]
    world["classify"](run_id=world["extract"](world["ingest"](BIOLOGY_PAGES).id).id)
    edges = _inferred_edges(connection)
    for edge in edges:
        assert edge["from_concept_id"] < edge["to_concept_id"]
    pairs = [frozenset((e["from_concept_id"], e["to_concept_id"])) for e in edges]
    assert len(pairs) == len(set(pairs))


def test_r1_skips_an_ambiguous_name_and_an_ambiguous_definition(adders):
    connection, repository = adders["connection"], adders["repository"]
    by_name = {_name(connection, c): c for c in _run_concepts(connection, adders["run"].id)}
    service = ConceptService(repository)
    # Two concepts of the run now answer to "carry" (possible only by hand).
    service.add_alias(by_name["Half adder"], "Carry")
    connection.commit()
    report = adders["classify"](run_id=adders["run"].id)
    assert report.ambiguous_mentions >= 1
    assert report.edges_created == 0 and _inferred_edges(connection) == []


def test_r1_skips_a_definition_whose_direction_would_be_ambiguous(adders):
    connection, repository = adders["connection"], adders["repository"]
    by_name = {_name(connection, c): c for c in _run_concepts(connection, adders["run"].id)}
    knowledge_id = connection.execute(
        "SELECT to_knowledge_id FROM relationship WHERE relation_type = 'DEFINED_BY' "
        "AND from_concept_id = ?", (by_name["Full adder"],)).fetchone()[0]
    document = adders["document"]
    source = queries.sources_for_document(connection, document.id)[0]
    # A second DEFINED_BY edge onto the same definition: whose definition is it now?
    ConceptService(repository).attach_relationship(
        relation_type=RelationType.DEFINED_BY, origin=RelationshipOrigin.EXPLICIT,
        from_concept_id=by_name["Half adder"], to_knowledge_id=knowledge_id,
        evidence=Evidence(source_id=source.id, document_id=document.id, text="(manual)"),
    )
    connection.commit()
    report = adders["classify"](run_id=adders["run"].id)
    assert knowledge_id in report.definitions_ambiguous
    assert report.edges_created == 0


# ---------------------------------------- the stored matched text (I6-B)


CAPITALISED_PAGES = [
    "Adder Circuits\n"
    "A full adder is a circuit that adds three input bits and produces a Carry output.\n"
    "A carry is a digit that is transferred to the next column of an addition.",
]


def test_i6b_the_stored_matched_text_is_the_source_text_not_its_normalised_form(world):
    connection, repository = world["connection"], world["repository"]
    run = world["extract"](world["ingest"](CAPITALISED_PAGES).id)
    report = world["classify"](run_id=run.id)
    assert (report.edges_created, report.unmappable_mentions) == (1, 0)
    (row,) = connection.execute(
        "SELECT i.matched_text, s.original_text, s.char_start, s.char_end, g.text "
        "FROM relationship_inference i "
        "JOIN source_occurrence s ON s.id = i.basis_occurrence_id "
        "JOIN document_segment g ON g.id = s.segment_id").fetchall()
    assert row["matched_text"] == "Carry"  # as printed, not "carry"
    assert "produces a Carry output" in row["original_text"]
    assert row["matched_text"] in row["text"][row["char_start"]:row["char_end"]]  # on the page
    (related,) = organisation_view(repository, run.id).group("Related concepts")
    assert related.bases[0].inference.matched_text == "Carry"


# --------------------------------------------- stored names only (I6-E)


def test_i6e_r1_uses_the_stored_normalized_alias_and_does_not_recompute_it(adders):
    """Change a stored normalised name and R1 follows the stored value: "carry" is
    no longer in the name set, so nothing is inferred - proof nothing recomputes it."""
    connection = adders["connection"]
    connection.execute("UPDATE concept_alias SET normalized_alias = 'kerry' WHERE alias = 'Carry'")
    connection.commit()
    report = adders["classify"](run_id=adders["run"].id)
    assert report.names == 4 and report.edges_created == 0
    assert _inferred_edges(connection) == []


@pytest.mark.parametrize("stored", ["2", None])
def test_i6e_an_unsupported_or_missing_normalisation_version_is_refused(adders, stored):
    connection = adders["connection"]
    if stored is None:
        connection.execute("DELETE FROM database_metadata WHERE key = 'alias_normalization_version'")
    else:
        connection.execute("UPDATE database_metadata SET value = ? "
                           "WHERE key = 'alias_normalization_version'", (stored,))
    connection.commit()
    digest = _rows(connection)
    with pytest.raises(InvalidInputError) as caught:
        adders["classify"](run_id=adders["run"].id)
    report = caught.value.report
    assert "alias normalisation" in report.summary and report.data_changed is False
    assert _rows(connection) == digest


def test_i6e_a_canonical_name_without_its_stored_alias_row_is_refused(adders):
    connection = adders["connection"]
    connection.execute("UPDATE concept_alias SET lifecycle_status = 'ARCHIVED' WHERE alias = 'Carry'")
    connection.commit()
    digest = _rows(connection)
    with pytest.raises(InvalidInputError) as caught:
        adders["classify"](run_id=adders["run"].id)
    report = caught.value.report
    assert "no stored normalised form" in report.summary
    assert report.missing and report.data_changed is False
    assert not connection.in_transaction
    assert _rows(connection) == digest


# ------------------------------------------------------------- views V1 / V2


def test_v1_keeps_every_real_relation_type_and_shows_multiple_parents(world):
    connection, repository = world["connection"], world["repository"]
    run = world["extract"](world["ingest"](BIOLOGY_PAGES).id)
    by_name = {_name(connection, c): c for c in _run_concepts(connection, run.id)}
    document = connection.execute("SELECT id FROM document").fetchone()[0]
    source = queries.sources_for_document(connection, document)[0]
    # A second stated parent for Ribosome, so it must appear under both.
    ConceptService(repository).attach_relationship(
        relation_type=RelationType.PART_OF, origin=RelationshipOrigin.EXPLICIT,
        from_concept_id=by_name["Ribosome"], to_concept_id=by_name["Cell"],
        evidence=Evidence(source_id=source.id, document_id=document, text="A ribosome is part of a cell."),
    )
    connection.commit()

    view = hierarchy_view(repository, run.id)
    shape = {
        root.concept.canonical_name: [
            (child.concept.canonical_name, child.via.relationship.relation_type, child.via.origin)
            for child in root.children
        ]
        for root in view.roots
    }
    assert shape == {
        "Cell": [("Nucleus", "PART_OF", "EXPLICIT"), ("Ribosome", "PART_OF", "EXPLICIT")],
        "Organelle": [("Ribosome", "INSTANCE_OF", "EXPLICIT")],
    }
    assert view.cycles == ()
    assert _count(connection, "relationship", "relation_type = 'PARENT_OF'") == 0  # never relabelled


def test_v1_reports_a_cycle_and_still_terminates(adders):
    connection, repository = adders["connection"], adders["repository"]
    by_name = {_name(connection, c): c for c in _run_concepts(connection, adders["run"].id)}
    document = adders["document"]
    source = queries.sources_for_document(connection, document.id)[0]
    ConceptService(repository).attach_relationship(
        relation_type=RelationType.COMPOSED_OF, origin=RelationshipOrigin.EXPLICIT,
        from_concept_id=by_name["Half adder"], to_concept_id=by_name["Full adder"],
        evidence=Evidence(source_id=source.id, document_id=document.id, text="(a cycle, by hand)"),
    )
    connection.commit()
    view = hierarchy_view(repository, adders["run"].id)
    assert view.cycles
    names = {_name(connection, c) for cycle in view.cycles for c in cycle}
    assert names == {"Full adder", "Half adder"}


def test_v2_shows_stated_prerequisites_as_explicit_and_infers_none(world):
    connection, repository = world["connection"], world["repository"]
    run = world["extract"](world["ingest"](PARTIAL_PAGES).id)
    world["classify"](run_id=run.id)
    view = organisation_view(repository, run.id)
    assert view.group("Prerequisites") and view.group("Related concepts")
    for group in ("Prerequisites", "Dependencies", "Applications"):
        for edge in view.group(group):
            assert edge.origin is RelationshipOrigin.EXPLICIT and edge.occurrences
    for edge in view.group("Related concepts"):
        assert edge.origin is RelationshipOrigin.INFERRED and edge.bases
    assert _count(connection, "relationship",
                  "origin = 'INFERRED' AND relation_type IN "
                  "('PREREQUISITE_OF','DEPENDS_ON','REQUIRES','APPLIES_TO','USES')") == 0


def test_the_views_store_nothing_and_never_read_page_text_or_structure(world):
    """P6-5 and P6-17, asked of SQLite: classification and both views read neither
    `document_segment` nor `document_structure`, and the views write nothing."""
    connection, repository = world["connection"], world["repository"]
    run = world["extract"](world["ingest"](PARTIAL_PAGES).id)
    assert _count(connection, "document_structure") > 0  # there is structure to avoid
    with _recorded(connection) as (reads, writes):
        report = world["classify"](run_id=run.id)
    assert report.edges_created > 0
    assert {"concept", "source_occurrence", "relationship_inference"} <= reads
    assert not reads & {"document_segment", "document_structure"}, reads
    digest = _rows(connection)
    with _recorded(connection) as (reads, writes):
        hierarchy_view(repository, run.id)
        organisation_view(repository, run.id)
    assert writes == set()
    assert not reads & {"document_segment", "document_structure"}, reads
    assert _rows(connection) == digest


# ------------------------------------------------ CLI and first migration


def test_the_first_classify_on_a_version_4_database_migrates_it_with_a_backup(tmp_path, monkeypatch):
    project = tmp_path / "project"
    db = project / "data" / "database" / "knowledge.db"
    db.parent.mkdir(parents=True)
    connection = connect(db)
    real = migrator.available_migrations()
    with mock.patch.object(migrator, "available_migrations",
                           lambda: tuple(m for m in real if m.version <= 4)), \
         mock.patch.object(migrator, "CODE_SCHEMA_VERSION", 4):
        migrate(connection, database_path=db)
    connection.commit()
    repository = Repository(connection)
    pdf = tmp_path / "adders.pdf"
    pdf.write_bytes(make_pdf(SECTION_193_PAGES))
    document = IngestionPipeline(repository, PypdfParser(), project / "data" / "documents").ingest(pdf).document
    connection.commit()
    pipeline = ExtractionPipeline(repository)
    run = pipeline.start(document.id)
    connection.commit()
    pipeline.execute(run)
    connection.commit()
    connection.close()

    env = {k: v for k, v in os.environ.items() if not k.startswith("RUDRA_")}
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "app", "classify", "--run", run.id, "--project-root", str(project)],
        capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT), timeout=180,
    )
    elapsed = time.perf_counter() - started
    assert result.returncode == 0, result.stderr
    # Option 1 (ADR 0030): the CLI migrates to this build's version, whatever it is.
    assert f"Database   : migrated 4 -> {migrator.CODE_SCHEMA_VERSION}; backup " in result.stdout
    backups = list((project / "data" / "backups").glob("knowledge-pre-migration-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 4
    assert "Committed  : 1 new INFERRED RELATED_TO edges" in result.stdout
    assert elapsed < 30  # resource impact: one process start, one migration, one run


def test_15_resource_impact_of_one_classification_is_small(world):
    """Part 4 section 171 asks for resource impact to be understood; the real
    measurements are in PHASE_6.md. This guards against a gross regression only."""
    run = world["extract"](world["ingest"](PARTIAL_PAGES).id)
    started = time.perf_counter()
    report = world["classify"](run_id=run.id)
    assert time.perf_counter() - started < 5
    assert report.edges_created >= 1
