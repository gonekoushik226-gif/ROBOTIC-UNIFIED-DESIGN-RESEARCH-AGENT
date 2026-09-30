"""`merge` - duplicates stored before Phase 8, superseded in place (ADR 0033 P8-19,
ADR 0034 P8-30; `docs/phases/PHASE_8.md` section 7).

The duplicates are built directly in the test's own database, in the shape the
Phase 5 writer (extractor v3) stored them: a document extracted twice, each run with
its own concepts and its own copy of every object at the same location, plus a
second document. A Phase 6 inferred edge has its basis on the copy that will be
superseded. The live database is never opened.
"""

from __future__ import annotations

import pathlib
from dataclasses import replace

import pytest

from app.core.errors import InvalidInputError
from app.deduplication import Merger, review_knowledge
from app.documents import IngestionPipeline, PypdfParser
from app.extraction import ExtractionPipeline
from app.knowledge import ConceptService, Evidence
from app.models import (
    Authorization,
    CertaintyState,
    Document,
    DocumentProcessingStatus,
    DocumentSegment,
    Equation,
    ExtractionRun,
    ExtractionRunStatus,
    ExtractionTrigger,
    KnowledgeObject,
    KnowledgeType,
    LifecycleStatus,
    RelationshipInference,
    RelationshipOrigin,
    RelationType,
    Rule,
    Source,
    SourceAvailability,
    SourceCategory,
    SourceOccurrence,
    TextOrigin,
)
from app.models.base import utc_now
from app.storage import Repository, connect, migrate, queries
from app.ui.cli.main import main
from tests.unit.pdf_fixtures import make_pdf

FLIP_FLOP = "A flip-flop is a bistable circuit that stores one bit of data."
DIODE_07 = "A silicon diode is a diode with a forward voltage of 0.7 V."
DIODE_03 = "A silicon diode is a diode with a forward voltage of 0.3 V."
EQUATION = "V = IR"
RULE = "Dependent sources are never deactivated during superposition."
PAGE_1 = "\n".join(("Book One", FLIP_FLOP, DIODE_07, EQUATION, RULE))
PAGE_2 = "\n".join(("Book Two", DIODE_03, "Ohm", EQUATION))

#: Tables whose rows a merge must leave exactly as they were.
UNTOUCHED = (
    "document", "document_segment", "document_structure", "document_version", "source",
    "source_occurrence", "concept", "concept_alias", "concept_occurrence", "relationship",
    "relationship_occurrence", "relationship_inference", "extraction_run", "extraction_issue",
)


def _stored_before_phase_8(repository: Repository) -> dict:
    """Two documents, three v3 runs, and the duplicates Phase 5 left (ADR 0020 D-47)."""
    service = ConceptService(repository)
    now = utc_now()

    def document(name: str, text: str):
        doc = repository.add(Document(
            id=repository.new_id(Document), created_at=now, updated_at=now,
            filename=f"{name}.pdf", original_filename=f"{name}.pdf", source_type="PDF",
            file_path=f"/documents/{name}.pdf", file_hash=f"hash-{name}", file_size=1,
            mime_type="application/pdf", ingested_at=now,
            processing_status=DocumentProcessingStatus.PROCESSED, processing_version=1,
            page_count=1))
        source = repository.add(Source(
            id=repository.new_id(Source), created_at=now, updated_at=now, name=name,
            source_category=SourceCategory.USER_PROVIDED_SOURCE,
            authorization=Authorization.AUTHORIZED, availability=SourceAvailability.AVAILABLE,
            document_id=doc.id, file_hash=f"hash-{name}"))
        segment = repository.add(DocumentSegment(
            id=repository.new_id(DocumentSegment), created_at=now, updated_at=now,
            document_id=doc.id, page_number=1, ordinal=0, text=text,
            extraction_method="pypdf", text_origin=TextOrigin.NATIVE_TEXT))
        return doc, source, segment, text

    def run(doc, number: int):
        return repository.add(ExtractionRun(
            id=repository.new_id(ExtractionRun), created_at=now, updated_at=now,
            document_id=doc.id, run_number=number, trigger=ExtractionTrigger.FIRST_EXTRACTION,
            extractor_version="3", parser_name="pypdf", started_at=now,
            status=ExtractionRunStatus.COMPLETED, completed_at=now))

    def evidence(where, the_run, text: str, name: str | None = None) -> Evidence:
        doc, source, segment, page = where
        start = page.index(text)
        return Evidence(
            source_id=source.id, document_id=doc.id, text=name or text,
            extraction_method="deterministic/Candidate@v3", segment_id=segment.id,
            page_number=1, char_start=start, char_end=start + len(text),
            extraction_run_id=the_run.id)

    def occurrence(knowledge_id: str, ev: Evidence) -> SourceOccurrence:
        return repository.add(SourceOccurrence(
            id=repository.new_id(SourceOccurrence), created_at=now, updated_at=now,
            knowledge_id=knowledge_id, source_id=ev.source_id, document_id=ev.document_id,
            original_text=ev.text, extraction_method=ev.extraction_method,
            extraction_timestamp=now, segment_id=ev.segment_id, page_number=ev.page_number,
            char_start=ev.char_start, char_end=ev.char_end, extraction_run_id=ev.extraction_run_id))

    def definition(where, the_run, name: str, statement: str):
        concept = service.create_concept(name)
        service.attach_concept_provenance(concept.id, evidence(where, the_run, statement, name))
        knowledge, _ = service.attach_definition(
            concept.id, statement, label=f"Definition: {name}", evidence=evidence(where, the_run, statement))
        return concept, knowledge, occurrence(knowledge.id, evidence(where, the_run, statement))

    def plain(where, the_run, knowledge_type, statement, certainty=CertaintyState.REPORTED_BY_SOURCE):
        knowledge = repository.add(KnowledgeObject(
            id=repository.new_id(KnowledgeObject), created_at=now, updated_at=now,
            knowledge_type=knowledge_type, canonical_name=f"{knowledge_type}: {statement}",
            statement=statement, lifecycle_status=LifecycleStatus.ACTIVE, certainty=certainty,
            knowledge_version=1))
        occurrence(knowledge.id, evidence(where, the_run, statement))
        return knowledge

    one = document("one", PAGE_1)
    two = document("two", PAGE_2)
    made: dict = {"flip_flop": [], "diode_07": [], "equation": [], "rule": [], "concepts": []}
    runs = [run(one[0], 1), run(one[0], 2)]
    for the_run in runs:  # the same document extracted twice: every object twice
        concept, knowledge, basis = definition(one, the_run, "Flip-flop", FLIP_FLOP)
        made["flip_flop"].append(knowledge.id)
        made["concepts"].append(concept.id)
        diode, knowledge, _ = definition(one, the_run, "Silicon diode", DIODE_07)
        made["diode_07"].append(knowledge.id)
        equation = plain(one, the_run, KnowledgeType.EQUATION, EQUATION, CertaintyState.UNCERTAIN)
        repository.add(Equation(id=repository.new_id(Equation), created_at=now, updated_at=now,
                                expression=EQUATION, lifecycle_status=LifecycleStatus.ACTIVE,
                                knowledge_id=equation.id))
        made["equation"].append(equation.id)
        rule = plain(one, the_run, KnowledgeType.RULE, RULE)
        repository.add(Rule(id=repository.new_id(Rule), created_at=now, updated_at=now, name="Rule",
                            statement=RULE, lifecycle_status=LifecycleStatus.ACTIVE, knowledge_id=rule.id))
        made["rule"].append(rule.id)
    # Phase 6: an INFERRED edge whose basis is the second run's flip-flop definition.
    edge = service.attach_relationship(
        relation_type=RelationType.RELATED_TO, origin=RelationshipOrigin.INFERRED,
        from_concept_id=concept.id, to_concept_id=diode.id)
    repository.add(RelationshipInference(
        id=repository.new_id(RelationshipInference), created_at=now, updated_at=now,
        relationship_id=edge.id, rule="R1", rule_version="1", basis_occurrence_id=basis.id,
        matched_text="silicon diode"))
    third = run(two[0], 1)
    _, diode_03, _ = definition(two, third, "Silicon diode", DIODE_03)
    made["diode_03"] = diode_03.id
    made["equation_elsewhere"] = plain(two, third, KnowledgeType.EQUATION, EQUATION,
                                       CertaintyState.UNCERTAIN).id
    made["basis"] = basis.id
    made["edge"] = edge.id
    repository.connection.commit()
    return made


@pytest.fixture
def stored(tmp_path: pathlib.Path):
    db_path = tmp_path / "data" / "database" / "knowledge.db"
    db_path.parent.mkdir(parents=True)
    connection = connect(db_path)
    migrate(connection, database_path=db_path)
    connection.commit()
    repository = Repository(connection)
    made = _stored_before_phase_8(repository)
    yield {"connection": connection, "repository": repository, "made": made,
           "tmp": tmp_path, "db_path": db_path}
    connection.close()


def _rows(connection, table: str) -> frozenset:
    return frozenset(tuple(r) for r in connection.execute(f'SELECT * FROM "{table}"'))


def _all(connection) -> dict[str, frozenset]:
    tables = [r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {t: _rows(connection, t) for t in tables}


def _status(connection, knowledge_id: str) -> str:
    return connection.execute(
        "SELECT lifecycle_status FROM knowledge_object WHERE id = ?", (knowledge_id,)).fetchone()[0]


# ------------------------------------------------------------------ the merge


def test_merge_supersedes_all_but_the_smallest_counter_and_writes_pointers(stored):
    connection, made = stored["connection"], stored["made"]
    report = Merger(stored["repository"]).merge()

    assert len(report.groups) == 4 and report.superseded == report.pointers == 4
    assert report.companions_superseded == 2  # the second equation row and rule row
    for key in ("flip_flop", "diode_07", "equation", "rule"):
        canonical, duplicate = made[key]  # built in counter order
        assert (canonical, duplicate) in {(g[0], g[1]) for g in report.groups}
        assert _status(connection, canonical) == "ACTIVE"
        assert _status(connection, duplicate) == "SUPERSEDED"
        (pointer,) = [r for r in queries.equivalences_of_knowledge(connection, duplicate)]
        assert (pointer.canonical_knowledge_id, pointer.other_knowledge_id) == (canonical, duplicate)
        assert pointer.outcome.value == "EXACT_DUPLICATE" and pointer.extraction_run_id is None
        assert pointer.linked_occurrence_id is None and pointer.rule == "P8-12"
    for table, key in (("equation", "equation"), ("rule", "rule")):
        statuses = dict(connection.execute(
            f"SELECT knowledge_id, lifecycle_status FROM {table}").fetchall())
        assert statuses == {made[key][0]: "ACTIVE", made[key][1]: "SUPERSEDED"}


def test_merge_moves_deletes_and_rewrites_nothing_but_lifecycle(stored):
    connection = stored["connection"]
    before = _all(connection)
    knowledge_before = {r[0]: tuple(r) for r in connection.execute("SELECT * FROM knowledge_object")}
    Merger(stored["repository"]).merge()
    after = _all(connection)

    for table in UNTOUCHED:  # occurrences, edges, relationship occurrences, R1 bases
        assert after[table] == before[table], table
    for table, rows in before.items():  # no row deleted anywhere
        if table != "id_sequence":
            assert {r[0] for r in rows} <= {r[0] for r in after[table]}, table
    columns = [r[1] for r in connection.execute("PRAGMA table_info(knowledge_object)")]
    for row in connection.execute("SELECT * FROM knowledge_object"):
        old = dict(zip(columns, knowledge_before[row[0]]))
        new = dict(zip(columns, tuple(row)))
        changed = {c for c in columns if old[c] != new[c]}
        assert changed <= {"lifecycle_status", "updated_at"}
        assert new["normalized_hash"] is None
    assert connection.execute("SELECT count(*) FROM extraction_issue").fetchone()[0] == 0


def test_every_object_still_resolves_to_its_evidence_and_the_r1_basis_holds(stored):
    connection, made = stored["connection"], stored["made"]
    basis_before = dict(connection.execute(
        "SELECT * FROM source_occurrence WHERE id = ?", (made["basis"],)).fetchone())
    Merger(stored["repository"]).merge()
    orphans = connection.execute(
        "SELECT k.id FROM knowledge_object k WHERE NOT EXISTS ("
        " SELECT 1 FROM source_occurrence s WHERE s.knowledge_id = k.id"
        "  AND s.document_id IS NOT NULL AND s.page_number IS NOT NULL"
        "  AND s.segment_id IS NOT NULL AND s.extraction_run_id IS NOT NULL"
        "  AND s.char_start IS NOT NULL AND s.char_end IS NOT NULL)").fetchall()
    assert orphans == []  # superseded objects included
    (inference,) = queries.inferences_for_relationship(connection, made["edge"])
    basis = dict(connection.execute(
        "SELECT * FROM source_occurrence WHERE id = ?", (inference.basis_occurrence_id,)).fetchone())
    assert basis == basis_before and basis["knowledge_id"] == made["flip_flop"][1]
    report = queries.graph_integrity(connection)
    assert report.is_clean and report.inferred_without_basis == ()
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_merge_then_compares_canonical_members_with_no_run(stored):
    connection, made = stored["connection"], stored["made"]
    report = Merger(stored["repository"]).merge()
    assert report.assessments == {"CONTRADICTORY": 1, "POSSIBLE_DUPLICATE": 1}
    (conflict,) = queries.conflicts_of_knowledge(connection, made["diode_03"])
    assert (conflict.claim_a_id, conflict.claim_b_id) == (made["diode_07"][0], made["diode_03"])
    assert conflict.cause.value == "UNDETERMINED"
    (equation,) = queries.equivalences_of_knowledge(connection, made["equation_elsewhere"])
    assert (equation.outcome.value, equation.rule) == ("POSSIBLE_DUPLICATE", "P8-12")
    assert equation.canonical_knowledge_id == made["equation"][0]
    assert all(r[0] is None for r in connection.execute(
        "SELECT extraction_run_id FROM knowledge_equivalence"))
    # Nothing was compared against a superseded member.
    for superseded in (made["diode_07"][1], made["equation"][1]):
        assert [r.outcome.value for r in queries.equivalences_of_knowledge(connection, superseded)] \
            == ["EXACT_DUPLICATE"]
    bases = sorted(r[0] for r in connection.execute("SELECT basis FROM concept_equivalence"))
    assert report.concept_records == 4 == len(bases)
    assert bases.count("SHARED_NAME_SAME_DOCUMENT") == 2  # the two runs of document one
    assert all(r[0] is None for r in connection.execute(
        "SELECT extraction_run_id FROM concept_equivalence"))


def test_a_second_merge_writes_nothing(stored):
    connection = stored["connection"]
    Merger(stored["repository"]).merge()
    before = _all(connection)
    report = Merger(stored["repository"]).merge()
    assert not report.changed and report.groups == ()
    assert _all(connection) == before


def test_number_of_sources_counts_the_objects_superseded_into_it(stored):
    connection, made = stored["connection"], stored["made"]
    Merger(stored["repository"]).merge()
    canonical, duplicate = made["flip_flop"]
    review = review_knowledge(stored["repository"], canonical)
    assert review.superseded_into == (duplicate,)
    assert review.source_occurrences == 2  # its own, plus the superseded twin's
    assert review.number_of_sources == 1  # both from the same source document
    (pointer,) = [a for a in review.assessments if a.is_merge_pointer]
    assert [row["id"] for row in pointer.other_evidence] == [
        o.id for o in queries.occurrences_of_knowledge(connection, duplicate)]
    superseded = review_knowledge(stored["repository"], duplicate)
    assert superseded.knowledge.lifecycle_status is LifecycleStatus.SUPERSEDED
    assert [a.this_side for a in superseded.assessments] == ["other"]


def test_lookup_still_shows_a_superseded_definition_under_its_own_concept(stored):
    made = stored["made"]
    Merger(stored["repository"]).merge()
    view = ConceptService(stored["repository"]).retrieve(made["concepts"][1])
    (definition,) = view.definitions
    assert definition.knowledge.id == made["flip_flop"][1]
    assert definition.knowledge.lifecycle_status is LifecycleStatus.SUPERSEDED
    assert definition.relationship.lifecycle_status is LifecycleStatus.ACTIVE  # edge untouched


def test_a_merge_refuses_to_start_on_uncommitted_work(stored):
    connection = stored["connection"]
    repository = stored["repository"]
    document = repository.get(Document, "DOC-00000001")
    repository.update(replace(document, language="en"))
    assert connection.in_transaction
    with pytest.raises(InvalidInputError):
        Merger(repository).merge()
    connection.rollback()
    assert connection.execute(
        "SELECT count(*) FROM knowledge_object WHERE lifecycle_status = 'SUPERSEDED'").fetchone()[0] == 0


# -------------------------------------------------------- order independence


def _new_document(world_tmp: pathlib.Path, repository: Repository):
    pdf = world_tmp / "fresh.pdf"
    pdf.write_bytes(make_pdf(["Book Three\nA flip-flop is a circuit with two stable states."]))
    document = IngestionPipeline(repository, PypdfParser(), world_tmp / "documents").ingest(pdf).document
    repository.connection.commit()
    pipeline = ExtractionPipeline(repository)
    run = pipeline.start(document.id)
    repository.connection.commit()
    report = pipeline.execute(run)
    repository.connection.commit()
    return report


def _records(connection) -> tuple[frozenset, frozenset, frozenset, frozenset]:
    knowledge = frozenset(tuple(r) for r in connection.execute(
        "SELECT canonical_knowledge_id, other_knowledge_id, linked_occurrence_id, "
        "extraction_run_id, outcome, rule FROM knowledge_equivalence"))
    conflicts = frozenset(tuple(r) for r in connection.execute(
        "SELECT claim_a_id, claim_b_id, cause FROM conflict"))
    concepts = frozenset(tuple(r) for r in connection.execute(
        "SELECT concept_a_id, concept_b_id, basis, shared_name, extraction_run_id "
        "FROM concept_equivalence"))
    lifecycle = frozenset(tuple(r) for r in connection.execute(
        "SELECT id, lifecycle_status FROM knowledge_object"))
    return knowledge, conflicts, concepts, lifecycle


def test_extraction_before_merge_compares_only_against_canonical_members(stored):
    connection, made = stored["connection"], stored["made"]
    report = _new_document(stored["tmp"], stored["repository"])
    (knowledge_id,) = [r[0] for r in connection.execute(
        "SELECT knowledge_id FROM source_occurrence WHERE extraction_run_id = ?", (report.run.id,))]
    (record,) = queries.equivalences_of_knowledge(connection, knowledge_id)
    assert record.canonical_knowledge_id == made["flip_flop"][0]  # never the twin
    assert queries.equivalences_of_knowledge(connection, made["flip_flop"][1]) == ()


def test_the_order_of_merge_and_extraction_changes_no_result(tmp_path):
    results = []
    for order in ("extract-first", "merge-first"):
        root = tmp_path / order
        db_path = root / "data" / "database" / "knowledge.db"
        db_path.parent.mkdir(parents=True)
        connection = connect(db_path)
        migrate(connection, database_path=db_path)
        connection.commit()
        repository = Repository(connection)
        _stored_before_phase_8(repository)
        if order == "extract-first":
            _new_document(root, repository)
            Merger(repository).merge()
        else:
            Merger(repository).merge()
            _new_document(root, repository)
        results.append(_records(connection))
        connection.close()
    assert results[0] == results[1]


# ------------------------------------------------------------------- the CLI


def test_merge_runs_only_when_asked_and_twice_writes_nothing(stored, capsys):
    connection, root = stored["connection"], str(stored["tmp"])
    connection.close()
    try:
        # `db` and `extract` never merge (P8-28, P8-30).
        assert main(["db", "--project-root", root]) == 0
        pdf = stored["tmp"] / "other.pdf"
        pdf.write_bytes(make_pdf(["Book Four\nA capacitor is a device that stores charge."]))
        assert main(["extract", str(pdf), "--project-root", root]) == 0
        check = connect(stored["db_path"], read_only=True)
        try:
            assert check.execute("SELECT count(*) FROM knowledge_object "
                                 "WHERE lifecycle_status = 'SUPERSEDED'").fetchone()[0] == 0
        finally:
            check.close()
        capsys.readouterr()

        assert main(["merge", "DOC-00000001", "--project-root", root]) == 2
        assert main(["merge", "--project-root", root]) == 0
        out = capsys.readouterr().out
        assert "Groups     : 4 exact-duplicate groups" in out
        assert "Superseded : 4 knowledge objects and 2 companion rows -> SUPERSEDED" in out
        assert "Pointers   : 4 EXACT_DUPLICATE records with no run" in out
        assert main(["merge", "--project-root", root]) == 0
        assert "Nothing to merge" in capsys.readouterr().out
    finally:
        stored["connection"] = connect(stored["db_path"])
