"""Part 5 section 197 - canonical knowledge matching - and the Phase 8 fixtures.

    Create at least three test documents containing the same conceptual information.
    Ingest all three.  Verify: Canonical knowledge count = 1;
                               Source occurrence count >= 3.
    Then ingest a fourth document with slightly different wording.
    Verify that semantic equivalence is evaluated.
    Verify that a contextually different statement is not blindly merged.
    Verify that contradictory information remains distinguishable.

Every document is generated here (no copyrighted text) and taken through the real
Phase 4 ingestion and Phase 5 extraction with stage 15 (ADR 0032, P8-8; the fixture
design of `docs/phases/PHASE_8.md` section 7). The section 197 chain is also run as
separate processes through the CLI, the Phase 7 precedent. Everything is in
temporary project roots; the live database is never opened.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

from app.core.errors import InvalidInputError
from app.deduplication import declare_edition, normalized_statement
from app.documents import IngestionPipeline, PypdfParser
from app.extraction import ExtractionPipeline
from app.models import (
    Document,
    ExtractionRunStatus,
    ExtractionTrigger,
    KnowledgeType,
)
from app.storage import Repository, connect, migrate, queries
from app.ui.cli.main import main
from tests.conftest import PROJECT_ROOT
from tests.unit.pdf_fixtures import make_pdf

FLIP_FLOP = "A flip-flop is a bistable circuit that stores one bit of data."
FLIP_FLOP_REWORDED = "A flip-flop is a circuit with two stable states."
GAIN_AMPLIFIER = "The gain is the ratio of output voltage to input voltage in an amplifier."
GAIN_CONTROL = "The gain is the ratio of the controller output to the error signal."
DIODE_07 = "A silicon diode is a diode with a forward voltage of 0.7 V."
DIODE_03 = "A silicon diode is a diode with a forward voltage of 0.3 V."

#: One sentence per category that has no concept, three with companion rows.
COMPANION_LINES = (
    "The electric current is measured in Ampere (A).",
    "Resistance is the property of a material to oppose the flow of current.",
    "Dependent sources are never deactivated during superposition.",
    "The resistance is found from Ohm's law, where R is the resistance of the element.",
    "Step 1: Select one independent source and deactivate the others.",
    "Step 2: Find the response due to that source alone.",
    "Example 1",
    "A resistor of 10 ohm carries a current of 2 A through it.",
)

CAPACITOR = "A capacitor is a device that stores electric charge."
OWNED_PROPERTY = "The characteristic of a capacitor is its ability to store charge."  # pattern 2
SUBJECT_PROPERTY = "Capacitance is the property of a capacitor to store charge."  # pattern 1
UNOWNED_PROPERTY = "The characteristic of an inductor is its ability to store energy."


def page(title: str, *sentences: str) -> list[str]:
    """One page: a short title (never prose) makes each document's bytes distinct."""
    return [title + "\n" + "\n".join(sentences)]


#: The section 197 documents (PHASE_8.md section 7).
SECTION_197 = {
    "A": page("Book A", FLIP_FLOP, GAIN_AMPLIFIER),
    "B": page("Book B", FLIP_FLOP),
    "C": page("Book C", FLIP_FLOP),
    "D": page("Book D", FLIP_FLOP_REWORDED),
    "E": page("Book E", GAIN_CONTROL),
    "F": page("Book F", DIODE_07),
    "G": page("Book G", DIODE_03),
}


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def world(tmp_path: pathlib.Path):
    """A migrated scratch project with the real ingestion and extraction pipelines."""
    db_path = tmp_path / "data" / "database" / "knowledge.db"
    db_path.parent.mkdir(parents=True)
    connection = connect(db_path)
    migrate(connection, database_path=db_path)
    connection.commit()
    repository = Repository(connection)

    def ingest(pages: list[str], name: str) -> Document:
        pdf = tmp_path / f"{name}.pdf"
        pdf.write_bytes(make_pdf(pages))
        report = IngestionPipeline(repository, PypdfParser(), tmp_path / "data" / "documents").ingest(pdf)
        connection.commit()
        return report.document

    def extract(document_id: str, trigger=ExtractionTrigger.FIRST_EXTRACTION):
        pipeline = ExtractionPipeline(repository)
        run = pipeline.start(document_id, trigger=trigger)
        connection.commit()
        report = pipeline.execute(run)
        connection.commit()
        return report

    def both(pages: list[str], name: str):
        document = ingest(pages, name)
        return document, extract(document.id)

    yield {"connection": connection, "repository": repository, "ingest": ingest,
           "extract": extract, "both": both, "tmp": tmp_path, "db_path": db_path}
    connection.close()


@pytest.fixture
def section197(world):
    documents, reports = {}, {}
    for name, pages in SECTION_197.items():
        documents[name], reports[name] = world["both"](pages, name)
    return {**world, "documents": documents, "reports": reports}


def _count(connection, table: str, where: str = "1=1", params=()) -> int:
    return connection.execute(f'SELECT count(*) FROM "{table}" WHERE {where}', params).fetchone()[0]


def _objects_with(connection, statement: str, knowledge_type=KnowledgeType.DEFINITION) -> list[str]:
    """ACTIVE objects of a type whose statement is `statement` after normalisation."""
    target = normalized_statement(statement)
    return [
        knowledge_id
        for knowledge_id, found_type, stored in queries.active_knowledge_statements(connection)
        if found_type == knowledge_type.value and normalized_statement(stored) == target
    ]


def _digest(connection) -> dict[str, frozenset]:
    tables = [r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {t: frozenset(tuple(r) for r in connection.execute(f'SELECT * FROM "{t}"')) for t in tables}


def _occurrence_rows(connection) -> dict[str, frozenset]:
    return {
        table: frozenset(tuple(r) for r in connection.execute(f"SELECT * FROM {table}"))
        for table in ("source_occurrence", "concept_occurrence", "relationship_occurrence")
    }


# ------------------------------------------------ section 197, steps 1-2 (and 164)


def test_197_three_documents_give_one_canonical_object_and_three_occurrences(section197):
    connection = section197["connection"]
    (canonical,) = _objects_with(connection, FLIP_FLOP)  # Canonical knowledge count = 1
    occurrences = queries.occurrences_of_knowledge(connection, canonical)
    assert len(occurrences) >= 3  # Source occurrence count >= 3
    documents = {section197["documents"][n].id for n in "ABC"}
    assert {o.document_id for o in occurrences} == documents  # one per document
    # The linked evidence keeps each source's own text, page, span and run (P8-4).
    runs = {section197["reports"][n].run.id for n in "ABC"}
    assert {o.extraction_run_id for o in occurrences} == runs
    assert all(o.original_text == FLIP_FLOP and o.char_start is not None for o in occurrences)
    # A DEFINED_BY edge from each run's own concept; concepts are never merged.
    assert len(queries.concepts_defined_by(connection, canonical)) == 3
    # Two EXACT_DUPLICATE records, each naming the linked occurrence and its run.
    exact = [r for r in queries.equivalences_of_knowledge(connection, canonical)
             if r.outcome.value == "EXACT_DUPLICATE"]
    assert len(exact) == 2
    assert {r.extraction_run_id for r in exact} == {section197["reports"][n].run.id for n in "BC"}
    assert all(r.linked_occurrence_id and r.rule == "P8-12" for r in exact)
    # B and C created no knowledge object: they linked.
    for name in "BC":
        report = section197["reports"][name]
        assert report.linked["definitions"] == report.stored["definitions"] == 1


def test_197_the_concepts_of_the_three_documents_are_possible_equivalents(section197):
    connection = section197["connection"]
    names = {r[0]: r[1] for r in connection.execute("SELECT id, canonical_name FROM concept")}
    pairs = connection.execute(
        "SELECT concept_a_id, concept_b_id, status, basis, shared_name FROM concept_equivalence"
    ).fetchall()
    flip_flop = [p for p in pairs if names[p[0]] == names[p[1]] == "Flip-flop"]
    assert len(flip_flop) == 6  # four documents name it: every unordered pair once
    assert all(p[2] == "POSSIBLE_EQUIVALENT" and p[3] == "SHARED_NAME_OTHER_DOCUMENT"
               and p[4] == "flip-flop" for p in flip_flop)
    assert all(p[0] < p[1] for p in pairs)  # canonical storage order
    # Never merged: every run keeps its own concept.
    assert _count(connection, "concept", "canonical_name = 'Flip-flop'") == 4
    issues = _count(connection, "extraction_issue", "issue_type = 'DUPLICATE_CONCEPT'")
    assert issues == len(pairs)  # one issue per record (the chosen granularity)


# ------------------------------------------------- step 3: slightly different wording


def test_197_a_reworded_statement_is_evaluated_and_not_linked(section197):
    connection = section197["connection"]
    (canonical,) = _objects_with(connection, FLIP_FLOP)
    (reworded,) = _objects_with(connection, FLIP_FLOP_REWORDED)
    assert reworded != canonical  # a new object: never merged on wording
    (record,) = [r for r in queries.equivalences_of_knowledge(connection, reworded)]
    assert (record.canonical_knowledge_id, record.other_knowledge_id) == (canonical, reworded)
    assert record.outcome.value == "POSSIBLE_DUPLICATE" and record.rule == "P8-13"
    assert record.extraction_run_id == section197["reports"]["D"].run.id
    assert len(queries.occurrences_of_knowledge(connection, canonical)) == 3  # nothing added


# --------------------------------------------- step 4: contextually different


def test_197_a_contextually_different_statement_is_not_blindly_merged(section197):
    connection = section197["connection"]
    (amplifier,) = _objects_with(connection, GAIN_AMPLIFIER)
    (control,) = _objects_with(connection, GAIN_CONTROL)
    assert amplifier != control
    (record,) = queries.equivalences_of_knowledge(connection, control)
    assert record.outcome.value == "POSSIBLE_DUPLICATE"
    assert (record.canonical_knowledge_id, record.other_knowledge_id) == (amplifier, control)
    for knowledge_id in (amplifier, control):
        assert len(queries.occurrences_of_knowledge(connection, knowledge_id)) == 1
    assert _count(connection, "conflict", "claim_a_id = ? OR claim_b_id = ?", (control, control)) == 0


# ------------------------------------------------------- step 5: contradiction


def test_197_contradictory_information_remains_distinguishable(section197):
    connection = section197["connection"]
    (first,) = _objects_with(connection, DIODE_07)
    (second,) = _objects_with(connection, DIODE_03)
    (conflict,) = queries.conflicts_of_knowledge(connection, first)
    assert (conflict.claim_a_id, conflict.claim_b_id) == (first, second)
    assert conflict.cause.value == "UNDETERMINED" and conflict.lifecycle_status.value == "ACTIVE"
    assert conflict.context is None  # nothing stored to establish one; none invented
    (record,) = queries.equivalences_of_knowledge(connection, second)
    assert record.outcome.value == "CONTRADICTORY" and record.rule == "C1"
    assert record.conflict_id == conflict.id
    # Both claims stay ACTIVE, unchanged, each with its own evidence.
    for knowledge_id, statement in ((first, DIODE_07), (second, DIODE_03)):
        row = connection.execute(
            "SELECT lifecycle_status, certainty, statement FROM knowledge_object WHERE id = ?",
            (knowledge_id,)).fetchone()
        assert tuple(row) == ("ACTIVE", "REPORTED_BY_SOURCE", statement)
        (occurrence,) = queries.occurrences_of_knowledge(connection, knowledge_id)
        assert occurrence.original_text == statement
    # A POTENTIAL_CONTRADICTION issue in the run that found it; the run is not PARTIAL.
    report = section197["reports"]["G"]
    issue = connection.execute(
        "SELECT page_number, segment_id, excerpt, detail FROM extraction_issue "
        "WHERE issue_type = 'POTENTIAL_CONTRADICTION' AND extraction_run_id = ?",
        (report.run.id,)).fetchone()
    assert issue["page_number"] == 1 and issue["segment_id"] and issue["excerpt"] == DIODE_03
    assert conflict.id in issue["detail"]
    assert report.status is ExtractionRunStatus.COMPLETED
    assert report.conflicts == ((conflict.id, first, second),)


def test_197_whole_database_invariants(section197):
    connection = section197["connection"]
    assert _count(connection, "knowledge_object", "normalized_hash IS NOT NULL") == 0
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert queries.graph_integrity(connection).is_clean
    orphans = connection.execute(
        "SELECT k.id FROM knowledge_object k WHERE NOT EXISTS ("
        " SELECT 1 FROM source_occurrence s WHERE s.knowledge_id = k.id"
        "  AND s.document_id IS NOT NULL AND s.page_number IS NOT NULL"
        "  AND s.segment_id IS NOT NULL AND s.extraction_run_id IS NOT NULL"
        "  AND s.char_start IS NOT NULL AND s.char_end IS NOT NULL)").fetchall()
    assert orphans == []
    # Stage 15 never supersedes anything and never runs merge (P8-28, P8-30).
    assert _count(connection, "knowledge_object", "lifecycle_status <> 'ACTIVE'") == 0
    assert _count(connection, "knowledge_equivalence", "extraction_run_id IS NULL") == 0


def test_197_later_documents_never_change_or_delete_earlier_evidence(world):
    connection = world["connection"]
    before = None
    for name, pages in SECTION_197.items():
        world["both"](pages, name)
        now = _occurrence_rows(connection)
        if before is not None:
            for table, rows in before.items():
                assert rows <= now[table], table  # nothing deleted, nothing rewritten
        before = now


# ---------------------------------------------------- section 197 as processes


def _cli(project: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUDRA_")}
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "app", *args, "--project-root", str(project)],
        capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT), timeout=180,
    )


def test_197_through_the_cli_one_process_per_step(tmp_path):
    """Import -> extract (with stage 15) -> close, per document; then review."""
    project = tmp_path / "project"
    for name, pages in SECTION_197.items():
        pdf = tmp_path / f"{name}.pdf"
        pdf.write_bytes(make_pdf(pages))
        result = _cli(project, "extract", str(pdf))
        assert result.returncode == 0, result.stderr
        assert "extractor v6" in result.stdout and "not indexed (Phase 9)" in result.stdout
        if name in "BC":
            assert "EXACT_DUPLICATE 1" in result.stdout
        if name == "G":
            assert "CONTRADICTORY 1" in result.stdout and "review: python -m app review" in result.stdout

    db = project / "data" / "database" / "knowledge.db"
    connection = connect(db, read_only=True)
    try:
        (canonical,) = _objects_with(connection, FLIP_FLOP)
        (claim_b,) = _objects_with(connection, DIODE_03)
    finally:
        connection.close()
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    shown = _cli(project, "review", canonical, "--json")
    assert shown.returncode == 0, shown.stderr
    data = json.loads(shown.stdout)
    assert data["database"]["read_only"] is True
    assert data["sources"] == {"number_of_sources": 3, "source_occurrences": 3, "superseded_into": []}
    outcomes = sorted(a["record"]["outcome"] for a in data["assessments"])
    assert outcomes == ["EXACT_DUPLICATE", "EXACT_DUPLICATE", "POSSIBLE_DUPLICATE"]

    conflict = _cli(project, "review", claim_b)
    assert conflict.returncode == 0, conflict.stderr
    assert "Conflicts  : 1" in conflict.stdout and "cause UNDETERMINED" in conflict.stdout
    assert "CONTRADICTORY  rule C1 v1" in conflict.stdout
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before  # a review never writes


# --------------------------------------------------------------- re-extraction


def test_re_extraction_links_everything_and_creates_no_knowledge(world):
    connection = world["connection"]
    document, first = world["both"](SECTION_197["A"], "A")
    knowledge_before = _count(connection, "knowledge_object")
    second = world["extract"](document.id, ExtractionTrigger.USER_REQUESTED)

    assert _count(connection, "knowledge_object") == knowledge_before  # section 80
    assert second.stored["definitions"] == second.linked["definitions"] == 2
    assert second.assessments == {"EXACT_DUPLICATE": 2}
    assert second.status is ExtractionRunStatus.COMPLETED  # stage 15 issues discard nothing
    bases = {r[0] for r in connection.execute("SELECT basis FROM concept_equivalence")}
    assert bases == {"SHARED_NAME_SAME_DOCUMENT"}
    assert second.concept_equivalences == 2 == second.issues["DUPLICATE_CONCEPT"]
    # Each run still has its own concepts (I-A); none was merged.
    assert _count(connection, "concept") == 4
    for knowledge_id in _objects_with(connection, FLIP_FLOP):
        assert {o.extraction_run_id for o in queries.occurrences_of_knowledge(connection, knowledge_id)} \
            == {first.run.id, second.run.id}


# ------------------------------------------------------------------- equations


def test_an_identical_equation_elsewhere_is_a_possible_duplicate_and_the_same_one_links(world):
    connection = world["connection"]
    first_doc, _ = world["both"](page("Book H", "Ohm's relation", "V = IR"), "H")
    _, second = world["both"](page("Book I", "Ohm's relation", "V = IR"), "I")
    equations = _objects_with(connection, "V = IR", KnowledgeType.EQUATION)
    assert len(equations) == 2  # section 77: never linked across locations
    assert second.assessments == {"POSSIBLE_DUPLICATE": 1}
    (record,) = queries.equivalences_of_knowledge(connection, equations[1])
    assert record.rule == "P8-12" and record.outcome.value == "POSSIBLE_DUPLICATE"
    assert _count(connection, "equation") == 2

    again = world["extract"](first_doc.id, ExtractionTrigger.USER_REQUESTED)
    assert again.linked["equations"] == 1  # the same location: section 80
    assert len(_objects_with(connection, "V = IR", KnowledgeType.EQUATION)) == 2
    assert _count(connection, "equation") == 2  # no companion row duplicated


# ------------------------------------------------------ types and companion rows


def test_identical_statements_of_every_other_type_link_without_new_companion_rows(world):
    connection = world["connection"]
    _, first = world["both"](page("Book J", *COMPANION_LINES), "J")
    categories = ("units", "properties", "rules", "variables", "examples", "procedures")
    assert all(first.stored[c] == 1 and first.linked[c] == 0 for c in categories)
    tables = ("knowledge_object", "variable", "rule", "procedure", "equation")
    before = {t: _count(connection, t) for t in tables}

    _, second = world["both"](page("Book K", *COMPANION_LINES), "K")
    assert all(second.stored[c] == second.linked[c] == 1 for c in categories)
    assert {t: _count(connection, t) for t in tables} == before  # nothing new, nothing duplicated
    assert second.assessments == {"EXACT_DUPLICATE": len(categories)}
    for table in ("variable", "rule", "procedure"):
        assert _count(connection, table, "lifecycle_status = 'ACTIVE'") == 1, table


# ---------------------------------------------------------- properties (P8-24)


def _has_property(connection) -> list[tuple]:
    return [tuple(r) for r in connection.execute(
        "SELECT c.id, c.canonical_name, k.id, k.statement, r.origin, r.id FROM relationship r "
        "JOIN concept c ON c.id = r.from_concept_id JOIN knowledge_object k ON k.id = r.to_knowledge_id "
        "WHERE r.relation_type = 'HAS_PROPERTY' ORDER BY r.id")]


def test_only_a_property_of_x_sentence_with_a_defined_owner_gets_an_edge(world):
    connection = world["connection"]
    _, report = world["both"](
        page("Book P", CAPACITOR, OWNED_PROPERTY, SUBJECT_PROPERTY, UNOWNED_PROPERTY), "P")
    assert report.stored["properties"] == 3  # every property is still stored
    ((concept, name, knowledge, statement, origin, edge),) = _has_property(connection)
    assert (name, statement, origin) == ("Capacitor", OWNED_PROPERTY, "EXPLICIT")
    (evidence,) = queries.occurrences_for_relationship(connection, edge)
    assert evidence.original_text == OWNED_PROPERTY and evidence.extraction_run_id == report.run.id
    # The owner concept cites the property sentence, as a stated relationship does.
    surfaces = {o.page_number for o in queries.occurrences_for_concept(connection, concept)}
    assert surfaces == {1}
    assert report.has_property_edges == 1
    # No edge from a pattern-1 subject; no concept invented for the undefined inductor,
    # and no issue for it: nothing was discarded.
    assert _count(connection, "concept") == 1
    assert report.issues == {}


def test_the_same_owned_property_in_two_documents_is_one_object_with_an_edge_per_run(world):
    connection = world["connection"]
    lines = (CAPACITOR, OWNED_PROPERTY)
    world["both"](page("Book P1", *lines), "P1")
    _, second = world["both"](page("Book P2", *lines), "P2")
    (knowledge,) = _objects_with(connection, OWNED_PROPERTY, KnowledgeType.PROPERTY)
    edges = _has_property(connection)
    assert len(edges) == 2 and {e[2] for e in edges} == {knowledge}  # one object ...
    assert len({e[0] for e in edges}) == 2  # ... an edge from each run's concept
    assert len(queries.occurrences_of_knowledge(connection, knowledge)) == 2
    assert second.linked["properties"] == 1 and second.has_property_edges == 1


def test_differing_properties_of_one_owner_are_compared_and_grouped(world):
    """Section 76: one owner's properties, never collapsed; C1 for numeric-only."""
    connection = world["connection"]
    world["both"](page("Book Q", CAPACITOR, "The characteristic of a capacitor is its leakage of 2 nA."), "Q")
    _, second = world["both"](page("Book R", CAPACITOR,
                                    "The characteristic of a capacitor is its leakage of 5 nA."), "R")
    # The capacitor definition is identical (linked); the two leakage properties of
    # the same owner differ only in a number (rule C1).
    assert second.assessments == {"EXACT_DUPLICATE": 1, "CONTRADICTORY": 1}
    ((conflict, claim_a, claim_b),) = second.conflicts
    kinds = {r[0] for r in connection.execute(
        "SELECT knowledge_type FROM knowledge_object WHERE id IN (?, ?)", (claim_a, claim_b))}
    assert kinds == {"PROPERTY"}
    assert len(_has_property(connection)) == 2  # both grouped under their owner


def test_a_property_stored_before_phase_8_is_not_linked_until_its_document_is_re_extracted(world):
    """P8-24: a stored property has no recorded owner; nothing rewrites it."""
    connection = world["connection"]
    document, _ = world["both"](page("Book S", CAPACITOR, OWNED_PROPERTY), "S")
    # Reproduce the pre-Phase-8 shape: the property exists, its edge does not.
    connection.execute("DELETE FROM relationship_occurrence WHERE relationship_id IN "
                       "(SELECT id FROM relationship WHERE relation_type = 'HAS_PROPERTY')")
    connection.execute("DELETE FROM relationship WHERE relation_type = 'HAS_PROPERTY'")
    connection.commit()
    world["both"](page("Book T", "A resistor is a component that opposes current."), "T")
    assert _has_property(connection) == []  # another document's extraction adds nothing
    world["extract"](document.id, ExtractionTrigger.USER_REQUESTED)
    assert len(_has_property(connection)) == 1  # re-extraction records the owner


def test_lookup_prints_a_has_property_edge_to_a_knowledge_object(world, capsys):
    world["both"](page("Book P", CAPACITOR, OWNED_PROPERTY), "P")
    world["connection"].close()
    code = main(["lookup", "--name", "Capacitor", "--project-root", str(world["tmp"])])
    out = capsys.readouterr().out
    world["connection"] = connect(world["db_path"])
    assert code == 0
    line = next(l for l in out.splitlines() if "HAS_PROPERTY" in l)
    assert "HAS_PROPERTY EXPLICIT [ACTIVE]  -> knowledge_object K-" in line
    assert "Property of Capacitor" in line
    assert OWNED_PROPERTY in out  # the edge's stated evidence


# ------------------------------------------------------------ edition (P8-27)


def test_edition_records_two_document_version_rows_and_nothing_else(world):
    connection, repository = world["connection"], world["repository"]
    work, _ = world["both"](SECTION_197["A"], "A")
    edition, _ = world["both"](SECTION_197["B"], "B")
    before = _digest(connection)
    declared = declare_edition(repository, edition_id=edition.id, work_id=work.id,
                               label="2nd edition", work_label="1st edition")
    connection.commit()
    assert [(r.document_id, r.version_label, r.file_hash, r.ingested_at, r.notes)
            for r in declared.written] == [
        (work.id, "1st edition", work.file_hash, work.ingested_at, None),
        (work.id, "2nd edition", edition.file_hash, edition.ingested_at, None),
    ]
    after = _digest(connection)
    changed = {t for t in after if after[t] != before[t]}
    assert changed == {"document_version", "id_sequence"}
    # Phase 4 is unchanged: no document row and no occurrence names a version.
    assert _count(connection, "source_occurrence", "document_version_id IS NOT NULL") == 0
    assert after["document"] == before["document"]


def test_edition_refuses_what_it_cannot_record_honestly(world):
    connection, repository = world["connection"], world["repository"]
    first, _ = world["both"](SECTION_197["A"], "A")
    second, _ = world["both"](SECTION_197["B"], "B")
    third, _ = world["both"](SECTION_197["C"], "C")

    def refused(**kwargs) -> str:
        with pytest.raises(InvalidInputError) as caught:
            declare_edition(repository, **kwargs)
        connection.rollback()
        assert caught.value.report.data_changed is False
        return caught.value.report.summary

    assert "edition of itself" in refused(edition_id=first.id, work_id=first.id, label="x", work_label="y")
    assert "no document DOC-99999999" in refused(edition_id="DOC-99999999", work_id=first.id, label="x")
    assert "no edition label yet" in refused(edition_id=second.id, work_id=first.id, label="2nd")
    declare_edition(repository, edition_id=second.id, work_id=first.id, label="2nd", work_label="1st")
    connection.commit()
    digest = _digest(connection)
    assert "already declared as an edition of this work" in refused(
        edition_id=second.id, work_id=first.id, label="again")
    assert "already declared as an edition of work" in refused(
        edition_id=second.id, work_id=third.id, label="x", work_label="y")
    assert "already the work of declared editions" in refused(
        edition_id=first.id, work_id=third.id, label="x", work_label="y")
    assert "itself declared as an edition" in refused(
        edition_id=third.id, work_id=second.id, label="x", work_label="y")
    assert "already recorded as" in refused(
        edition_id=third.id, work_id=first.id, label="3rd", work_label="first")
    assert _digest(connection) == digest  # every refusal wrote nothing
    # A third edition of the same work adds exactly one row.
    declared = declare_edition(repository, edition_id=third.id, work_id=first.id, label="3rd")
    connection.commit()
    assert len(declared.written) == 1 and len(declared.rows) == 3


def test_edition_through_the_cli(world, capsys):
    work, _ = world["both"](SECTION_197["A"], "A")
    edition, _ = world["both"](SECTION_197["B"], "B")
    world["connection"].close()
    root = str(world["tmp"])
    try:
        assert main(["edition", "CPT-00000001", "--work", work.id, "--label", "x",
                     "--project-root", root]) == 2
        assert main(["edition", edition.id, "--work", work.id, "--project-root", root]) == 2
        capsys.readouterr()
        assert main(["edition", edition.id, "--work", work.id, "--label", "2nd edition",
                     "--work-label", "1st edition", "--project-root", root]) == 0
        out = capsys.readouterr().out
        assert "Declared   : 2 document_version rows written" in out
        assert main(["edition", edition.id, "--work", work.id, "--label", "2nd edition",
                     "--project-root", root]) == 2  # already declared
    finally:
        world["connection"] = connect(world["db_path"])
    assert _count(world["connection"], "document_version") == 2


# ------------------------------------------------------------- review (P8-26)


def test_review_exit_codes_and_refusals(section197, capsys):
    root = str(section197["tmp"])
    section197["connection"].close()
    try:
        assert main(["review", "CPT-00000001", "--project-root", root]) == 2
        assert main(["review", "not-an-id", "--project-root", root]) == 2
        assert main(["review", "--project-root", root]) == 2
        capsys.readouterr()
        assert main(["review", "K-99999999", "--project-root", root]) == 3
        assert "no knowledge object has this id" in capsys.readouterr().out
        reader = connect(section197["db_path"], read_only=True)
        try:
            (plain,) = _objects_with(reader, GAIN_AMPLIFIER)
        finally:
            reader.close()
        assert main(["review", plain, "--project-root", root]) == 0
    finally:
        section197["connection"] = connect(section197["db_path"])


def test_review_says_so_when_nothing_names_an_object(world, capsys):
    world["both"](page("Book Z", CAPACITOR), "Z")
    (knowledge,) = _objects_with(world["connection"], CAPACITOR)
    world["connection"].close()
    try:
        assert main(["review", knowledge, "--project-root", str(world["tmp"])]) == 0
        out = capsys.readouterr().out
        assert "Nothing to review" in out and "number_of_sources 1" in out
    finally:
        world["connection"] = connect(world["db_path"])
