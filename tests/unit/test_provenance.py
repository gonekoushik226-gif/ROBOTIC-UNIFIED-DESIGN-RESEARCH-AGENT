"""Phase 12: the provenance of stored items (ADR 0044 P12-4 ... P12-8).

What every test holds provenance to: section 49's fields exactly as stored; the quote
check at the recorded span only, by the documented rules; the file check by SHA-256;
"Provenance unavailable." verbatim when nothing can be shown, with no content and no
citation; withheld evidence counted, never shown (P9-5); DELETED and ARCHIVED excluded
(P9-23); the same scope verdicts as Phases 9, 10 and 11; nothing written. Every test works
in its own freshly migrated temporary database; the live database is never opened.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.calculation import CalculationScope
from app.calculation.scope import source_verdict as phase_11_verdict
from app.core.errors import InvalidInputError, StorageError
from app.models import (
    Authorization,
    Document,
    DocumentProcessingStatus,
    Equation,
    KnowledgeType,
    LifecycleStatus,
    RelationshipOrigin,
    RelationType,
    Source,
    SourceAvailability,
    SourceCategory,
    Variable,
)
from app.provenance import (
    UNAVAILABLE_TEXT,
    CheckStatus,
    ProvenanceScope,
    ProvenanceService,
    ProvenanceStatus,
    to_json,
)
from app.provenance.scope import source_verdict
from app.query.requests import SourceScope
from app.query.scope import source_verdict as phase_9_verdict
from app.reasoning.requests import ReasoningScope
from app.reasoning.scope import source_verdict as phase_10_verdict
from app.storage import Repository, connect
from tests.unit.query_rows import QueryRows

PAGE = ("Resistors\nA resistor is a passive element that opposes \ncurrent.\n"
        "The total resistance of resistors in series is their sum.\nRtotal = R1 + R2\n")
SENTENCE = "A resistor is a passive element that opposes current."  # flattened, as extraction stores it
EQUATION = "Rtotal = R1 + R2"


def _library(rows: QueryRows, tmp_path) -> SimpleNamespace:
    """One authorised book whose preserved file really exists, with a page of text."""
    n = SimpleNamespace(rows=rows)
    pdf = tmp_path / "resistors.pdf"
    pdf.write_bytes(b"%PDF-1.4 resistors, original test bytes")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    n.pdf = pdf
    n.book = rows._add(
        Document, filename="resistors.pdf", original_filename="resistors.pdf", source_type="PDF",
        file_path=str(pdf), file_hash=digest, file_size=pdf.stat().st_size, mime_type="application/pdf",
        ingested_at=rows.now, processing_status=DocumentProcessingStatus.PROCESSED, processing_version=1,
        document_title="Resistors",
    )
    n.src = rows.source(n.book)
    n.run = rows.run(n.book)
    n.seg = rows.segment(n.book, 2, text=PAGE)
    start = PAGE.index("A resistor")
    n.sentence_span = (start, start + len("A resistor is a passive element that opposes \ncurrent."))
    eq_start = PAGE.index(EQUATION)
    n.definition = rows.knowledge(KnowledgeType.DEFINITION, SENTENCE)
    n.def_occurrence = rows.knowledge_occurrence(n.definition, n.src, page=2, segment=n.seg,
                                                 span=n.sentence_span, run=n.run)
    n.equation = rows.knowledge(KnowledgeType.EQUATION, EQUATION)
    n.eq_row = rows._add(Equation, expression=EQUATION, lifecycle_status=LifecycleStatus.ACTIVE,
                         knowledge_id=n.equation.id)
    rows.knowledge_occurrence(n.equation, n.src, page=2, segment=n.seg,
                              span=(eq_start, eq_start + len(EQUATION)), run=n.run)
    n.resistor = rows.concept("Resistor")
    rows.concept_occurrence(n.resistor, n.src, page=2, segment=n.seg, span=n.sentence_span, run=n.run)
    return n


@pytest.fixture
def lib(repo, tmp_path):
    return _library(QueryRows(repo), tmp_path)


def _of(repo, identifier, scope=ProvenanceScope.MY_BOOKS):
    return ProvenanceService(repo).of_item(identifier, scope)


# --------------------------------------------------- source information when available


def test_a_knowledge_object_shows_section_49s_fields_and_verified_checks(repo, lib):
    found = _of(repo, lib.definition.id)

    assert found.status is ProvenanceStatus.AVAILABLE and found.item == lib.definition
    (citation,) = found.citations
    row = citation.evidence
    assert (row.source_id, row.document_id, row.page_number, row.segment_id) == (
        lib.src.id, lib.book.id, 2, lib.seg.id)
    assert (row.char_start, row.char_end) == lib.sentence_span
    assert row.section is None and row.document_version_id is None  # never filled in
    assert citation.source == lib.src and citation.run == lib.run
    # The stored sentence is the flattened page text: verified by the documented rule.
    assert citation.quote.status is CheckStatus.VERIFIED and "flattened" in citation.quote.detail
    (document,) = found.documents
    assert document.preserved_file_present and document.file.status is CheckStatus.VERIFIED
    assert found.history.knowledge_version == 1 and found.history.lifecycle_status == "ACTIVE"
    assert found.verification is CheckStatus.VERIFIED
    assert found.pages == (f"{lib.book.id} p.2",)


def test_an_exact_quote_and_a_concepts_name_within_its_sentence_verify(repo, lib):
    equation = _of(repo, lib.equation.id)
    assert equation.citations[0].quote.status is CheckStatus.VERIFIED
    assert equation.citations[0].quote.detail.endswith("exactly")

    concept = _of(repo, lib.resistor.id)
    assert concept.status is ProvenanceStatus.AVAILABLE
    assert concept.citations[0].quote.status is CheckStatus.VERIFIED
    assert "name occurs within the recorded sentence" in concept.citations[0].quote.detail


def test_an_equation_row_answers_through_its_knowledge_object(repo, lib):
    found = _of(repo, lib.eq_row.id)
    assert found.status is ProvenanceStatus.AVAILABLE and found.kind == "EQUATION"
    assert found.subject_id == lib.equation.id and found.item == lib.eq_row
    assert [c.evidence.subject_id for c in found.citations] == [lib.equation.id]


# ----------------------------------------------------------------- the checks


def test_a_quote_that_is_not_at_its_recorded_span_fails(repo, lib):
    wrong = lib.rows.knowledge(KnowledgeType.DEFINITION, "A capacitor stores charge.")
    lib.rows.knowledge_occurrence(wrong, lib.src, page=2, segment=lib.seg, span=lib.sentence_span, run=lib.run)

    found = _of(repo, wrong.id)

    assert found.status is ProvenanceStatus.AVAILABLE  # the citation is shown, and so is its failure
    assert found.citations[0].quote.status is CheckStatus.FAILED
    assert found.verification is CheckStatus.FAILED


def test_evidence_without_a_span_is_inconclusive_and_never_searched_for(repo, lib):
    bare = lib.rows.knowledge(KnowledgeType.DEFINITION, SENTENCE + " ")
    lib.rows.knowledge_occurrence(bare, lib.src, page=2)

    found = _of(repo, bare.id)

    assert found.citations[0].quote.status is CheckStatus.INCONCLUSIVE
    assert "never searched" in found.citations[0].quote.detail
    assert found.verification is CheckStatus.INCONCLUSIVE


def test_a_missing_preserved_file_is_inconclusive_and_a_changed_one_fails(repo, lib):
    lib.pdf.write_bytes(b"%PDF-1.4 another file altogether")
    changed = _of(repo, lib.definition.id)
    assert changed.documents[0].file.status is CheckStatus.FAILED
    assert changed.verification is CheckStatus.FAILED

    lib.pdf.unlink()
    missing = _of(repo, lib.definition.id)
    assert missing.status is ProvenanceStatus.AVAILABLE  # Part 7: provenance survives the file
    assert not missing.documents[0].preserved_file_present
    assert missing.documents[0].file.status is CheckStatus.INCONCLUSIVE
    assert missing.citations[0].quote.status is CheckStatus.VERIFIED  # the stored page text stands


# ------------------------------------------------------------ Provenance unavailable


def test_an_item_with_no_evidence_is_provenance_unavailable_with_no_citation(repo, lib):
    alone = lib.rows.knowledge(KnowledgeType.CLAIM, "An uncited claim.")

    found = _of(repo, alone.id)

    assert found.status is ProvenanceStatus.UNAVAILABLE
    assert found.message.startswith(UNAVAILABLE_TEXT) and "No evidence is stored" in found.message
    assert found.item is None and found.citations == () and found.documents == ()
    assert "An uncited claim" not in to_json(found)


def test_evidence_from_an_unauthorised_source_is_withheld_never_shown(repo, lib):
    closed = lib.rows.source(lib.rows.document("closed"), authorization=Authorization.NOT_AUTHORIZED)
    hidden = lib.rows.knowledge(KnowledgeType.DEFINITION, "A secret definition.")
    lib.rows.knowledge_occurrence(hidden, closed, page=1)

    found = _of(repo, hidden.id)

    assert found.status is ProvenanceStatus.UNAVAILABLE and found.message.startswith(UNAVAILABLE_TEXT)
    assert "not authorized in the requested scope" in found.message
    assert found.withheld.unauthorized_evidence == 1
    assert "A secret definition" not in to_json(found) and closed.id not in to_json(found)


def test_an_authorised_external_source_is_out_of_my_books_but_in_the_wider_scope(repo, lib):
    web = lib.rows.source(lib.rows.document("web"), category=SourceCategory.AUTHORIZED_EXTERNAL_SOURCE)
    item = lib.rows.knowledge(KnowledgeType.DEFINITION, "An external definition.")
    lib.rows.knowledge_occurrence(item, web, page=1)

    narrow = _of(repo, item.id)
    assert narrow.status is ProvenanceStatus.UNAVAILABLE and narrow.withheld.out_of_scope_evidence == 1
    wide = _of(repo, item.id, ProvenanceScope.AUTHORIZED)
    assert wide.status is ProvenanceStatus.AVAILABLE and wide.citations[0].source == web


@pytest.mark.parametrize("status", [LifecycleStatus.DELETED, LifecycleStatus.ARCHIVED])
def test_deleted_and_archived_items_are_excluded(repo, lib, status):
    repo.update(replace(lib.definition, lifecycle_status=status))
    found = _of(repo, lib.definition.id)
    assert found.status is ProvenanceStatus.EXCLUDED and found.item is None and found.citations == ()


def test_a_nonexistent_identifier_is_not_found_and_text_is_refused(repo, lib):
    assert _of(repo, "K-00009999").status is ProvenanceStatus.NOT_FOUND
    assert _of(repo, "VER-00000001").status is ProvenanceStatus.NOT_FOUND
    with pytest.raises(InvalidInputError):
        _of(repo, "the resistor")


def test_a_row_without_a_knowledge_object_is_unavailable(repo, lib):
    variable = lib.rows._add(Variable, symbol="R", lifecycle_status=LifecycleStatus.ACTIVE)
    found = _of(repo, variable.id)
    assert found.status is ProvenanceStatus.UNAVAILABLE and "without a knowledge object" in found.message


def test_a_kind_rudra_records_no_provenance_for_is_unavailable(repo, lib):
    record = lib.rows.supersede(lib.definition, lib.rows.knowledge(KnowledgeType.DEFINITION, "twin"))
    found = _of(repo, record.id)
    assert found.status is ProvenanceStatus.UNAVAILABLE and "records no provenance" in found.message


# ------------------------------------------------------ relationships and history


def test_an_explicit_relationship_shows_its_origin_and_evidence(repo, lib):
    edge = lib.rows.edge(RelationType.DEFINED_BY, from_concept_id=lib.resistor.id,
                         to_knowledge_id=lib.definition.id)
    lib.rows.relationship_occurrence(edge, lib.src, page=2)

    found = _of(repo, edge.id)

    assert found.status is ProvenanceStatus.AVAILABLE and found.origin == "EXPLICIT"
    assert found.citations[0].evidence.subject_kind == "RELATIONSHIP"


def test_an_inferred_relationship_is_cited_through_its_recorded_basis(repo, lib):
    other = lib.rows.concept("Current")
    edge = lib.rows.edge(RelationType.RELATED_TO, origin=RelationshipOrigin.INFERRED,
                         from_concept_id=lib.resistor.id, to_concept_id=other.id)
    lib.rows.inference(edge, lib.def_occurrence)

    found = _of(repo, edge.id)

    assert found.status is ProvenanceStatus.AVAILABLE and found.origin == "INFERRED"
    assert found.citations == ()
    (basis,) = found.bases
    assert basis.inference.rule == "R1" and basis.citation.evidence.id == lib.def_occurrence.id


def test_a_superseded_object_shows_its_stored_merge_pointer(repo, lib):
    twin = lib.rows.knowledge(KnowledgeType.DEFINITION, SENTENCE)
    lib.rows.knowledge_occurrence(twin, lib.src, page=2, segment=lib.seg, span=lib.sentence_span, run=lib.run)
    lib.rows.supersede(lib.definition, twin)

    found = _of(repo, twin.id)

    assert found.history.superseded_by == lib.definition.id
    assert [outcome for _, outcome, _ in found.history.assessments] == ["EXACT_DUPLICATE"]


def test_a_conflict_shows_both_claims_and_withholds_one_not_in_scope(repo, lib):
    closed = lib.rows.source(lib.rows.document("closed"), authorization=Authorization.NOT_AUTHORIZED)
    rival = lib.rows.knowledge(KnowledgeType.DEFINITION, "A resistor stores energy.")
    lib.rows.knowledge_occurrence(rival, closed, page=1)
    conflict = lib.rows.conflict(lib.definition, rival)

    found = _of(repo, conflict.id)

    assert found.status is ProvenanceStatus.AVAILABLE and found.item == conflict
    first, second = found.claims
    assert first.status is ProvenanceStatus.AVAILABLE and second.status is ProvenanceStatus.UNAVAILABLE
    assert "stores energy" not in to_json(found)


# --------------------------------------------------------------- identity level


def test_a_document_source_run_and_occurrence_answer_at_identity_level(repo, lib):
    document = _of(repo, lib.book.id)
    assert document.status is ProvenanceStatus.AVAILABLE and document.sources == (lib.src,)
    assert document.runs == (lib.run,) and document.documents[0].file.status is CheckStatus.VERIFIED
    assert _of(repo, lib.src.id).status is ProvenanceStatus.AVAILABLE
    assert _of(repo, lib.run.id).status is ProvenanceStatus.AVAILABLE
    assert _of(repo, lib.seg.id).status is ProvenanceStatus.AVAILABLE
    occurrence = _of(repo, lib.def_occurrence.id)
    assert occurrence.status is ProvenanceStatus.AVAILABLE
    assert occurrence.citations[0].evidence.id == lib.def_occurrence.id


def test_a_document_whose_sources_are_not_authorised_is_unavailable(repo, lib):
    closed_book = lib.rows.document("closed")
    lib.rows.source(closed_book, authorization=Authorization.NOT_AUTHORIZED)
    found = _of(repo, closed_book.id)
    assert found.status is ProvenanceStatus.UNAVAILABLE and found.item is None


# ------------------------------------------------------ parity, read-only, schema


def test_provenance_scope_verdicts_equal_phases_9_10_and_11():
    """ADR 0044 P12-14: re-applied, not imported - and the same verdict every time."""
    for category in SourceCategory:
        for authorization in Authorization:
            source = Source(id="SRC-00000001", created_at="t", updated_at="t", name="s",
                            source_category=category, authorization=authorization,
                            availability=SourceAvailability.AVAILABLE)
            for scope in ProvenanceScope:
                ours = source_verdict(source, scope).value
                assert ours == phase_9_verdict(source, SourceScope(scope.value)).value
                assert ours == phase_10_verdict(source, ReasoningScope(scope.value)).value
                assert ours == phase_11_verdict(source, CalculationScope(scope.value)).value


def test_provenance_reads_only_is_deterministic_and_runs_read_only(repo, lib, db_path):
    lib.rows.commit()
    before = repo.connection.total_changes
    first = to_json(_of(repo, lib.definition.id))
    assert to_json(_of(repo, lib.definition.id)) == first
    assert repo.connection.total_changes == before
    reader = connect(db_path, read_only=True)
    try:
        assert to_json(ProvenanceService(Repository(reader)).of_item(lib.definition.id)) == first
    finally:
        reader.close()


def test_an_older_schema_is_refused_and_never_migrated(repo, lib, db_path):
    lib.rows.commit()
    repo.connection.execute("PRAGMA user_version = 5")
    repo.connection.commit()
    reader = connect(db_path, read_only=True)
    try:
        with pytest.raises(StorageError) as caught:
            ProvenanceService(Repository(reader)).of_item(lib.definition.id)
    finally:
        reader.close()
    assert "schema version is 5" in caught.value.report.reason
