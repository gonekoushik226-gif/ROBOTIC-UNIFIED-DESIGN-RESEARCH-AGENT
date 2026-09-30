"""Part 5 section 191, against the one real technical document the user supplied.

    Using one technical document, demonstrate:
      At least one concept extracted.
      At least one definition extracted.
      At least one relationship extracted.
      At least one source reference attached.
      At least one page reference attached.
    If the document contains suitable equations, demonstrate equation extraction too.

The document is GATE study notes on Network Theory, 137 pages, supplied by the user
on 2026-09-21 and accepted at the source-document readiness gate. Its SHA-256 is
pinned below, so these assertions can only ever pass against *that* document.

Everything runs in a temporary database; the project's own database is untouched.
If the PDF is not on this machine, the module is skipped - and says so - rather
than silently passing.

Page numbers are **PDF page indices** (1-137). The document prints its own labels
("3.11" on PDF page 7); those are not stored (limitation R-5).
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest

from app.documents import IngestionPipeline, PypdfParser
from app.extraction import CATEGORIES, ExtractionPipeline
from app.extraction.normalize import collapse
from app.models import (
    Document,
    DocumentProcessingStatus,
    ExtractionIssueType,
    ExtractionRunStatus,
)
from app.storage import Repository, connect, migrate, queries

#: The real textbook these acceptance tests read, named by an environment variable so no
#: machine's path is written here. Without it (or when the file is absent) they are skipped.
ACCEPTANCE_PDF = Path(os.environ.get("RUDRA_TEST_REAL_PDF") or "real-document-not-configured.pdf")
ACCEPTANCE_SHA256 = "2ECF3FE87E749729793FC2A80BBC1669383592C14237D4F0F481A5ED84F6315D"

pytestmark = pytest.mark.skipif(
    not ACCEPTANCE_PDF.exists(),
    reason=f"the section 191 acceptance document is not on this machine: {ACCEPTANCE_PDF}",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest().upper()


@pytest.fixture(scope="module")
def accepted(tmp_path_factory):
    """Ingest and extract the real document once, in a throwaway database."""
    before_hash = _sha256(ACCEPTANCE_PDF)
    before_mtime = ACCEPTANCE_PDF.stat().st_mtime_ns
    assert before_hash == ACCEPTANCE_SHA256, "this is not the accepted document"

    root = tmp_path_factory.mktemp("phase5_acceptance")
    db = root / "database" / "knowledge.db"
    db.parent.mkdir(parents=True)
    connection = connect(db)
    migrate(connection, database_path=db)
    connection.commit()
    repository = Repository(connection)

    started = time.perf_counter()
    ingestion = IngestionPipeline(repository, PypdfParser(), root / "documents").ingest(ACCEPTANCE_PDF)
    connection.commit()
    ingested = time.perf_counter()
    pipeline = ExtractionPipeline(repository)
    run = pipeline.start(ingestion.document.id)
    connection.commit()
    report = pipeline.execute(run)
    connection.commit()
    finished = time.perf_counter()

    yield {
        "connection": connection,
        "repository": repository,
        "document": ingestion.document,
        "report": report,
        "before_hash": before_hash,
        "before_mtime": before_mtime,
        "ingest_seconds": ingested - started,
        "extract_seconds": finished - ingested,
    }
    connection.close()


def _definition(connection, sentence_fragment: str):
    return connection.execute(
        "SELECT k.id, k.statement, s.page_number, s.segment_id, s.char_start, s.char_end, "
        "s.source_id, s.extraction_run_id, g.text "
        "FROM knowledge_object k JOIN source_occurrence s ON s.knowledge_id = k.id "
        "JOIN document_segment g ON g.id = s.segment_id "
        "WHERE k.knowledge_type = 'DEFINITION' AND k.statement LIKE ?",
        (f"%{sentence_fragment}%",),
    ).fetchone()


# ------------------------------------------------------------- section 191, 1-5


def test_191_at_least_one_concept_is_extracted(accepted):
    connection = accepted["connection"]
    row = connection.execute(
        "SELECT c.canonical_name, o.page_number, o.surface_form FROM concept c "
        "JOIN concept_occurrence o ON o.concept_id = c.id WHERE c.canonical_name = 'Node'"
    ).fetchone()
    assert tuple(row) == ("Node", 7, "Node")
    assert accepted["report"].stored["concepts"] >= 1


def test_191_at_least_one_definition_is_extracted(accepted):
    row = _definition(accepted["connection"], "connected together is called node")
    assert row is not None
    assert row["statement"] == (
        "A point at which two or more elements are connected together is called node."
    )


def test_191_at_least_one_relationship_is_extracted(accepted):
    """Every stored edge is EXPLICIT and carries its sentence as evidence.

    Honest about what kind: on this document the relationships that survive are the
    DEFINED_BY edges joining each concept to its definition, stated by the defining
    sentence itself. Other stated relations were found but could not be resolved
    to two defined concepts and were recorded as BROKEN_RELATIONSHIP, not stored.
    """
    connection = accepted["connection"]
    row = connection.execute(
        "SELECT r.relation_type, r.origin, c.canonical_name, o.page_number, o.original_text "
        "FROM relationship r JOIN concept c ON c.id = r.from_concept_id "
        "JOIN relationship_occurrence o ON o.relationship_id = r.id "
        "WHERE c.canonical_name = 'Node' AND r.relation_type = 'DEFINED_BY'"
    ).fetchone()
    assert tuple(row)[:4] == ("DEFINED_BY", "EXPLICIT", "Node", 7)
    assert "is called node" in row["original_text"]
    assert accepted["report"].defined_by_edges >= 1


def test_191_at_least_one_source_reference_is_attached(accepted):
    connection = accepted["connection"]
    row = _definition(connection, "connected together is called node")
    source = connection.execute(
        "SELECT s.source_category, s.authorization, d.file_hash FROM source s "
        "JOIN document d ON d.id = s.document_id WHERE s.id = ?",
        (row["source_id"],),
    ).fetchone()
    assert (source["source_category"], source["authorization"]) == (
        "USER_PROVIDED_SOURCE", "AUTHORIZED")
    # The source is bound to exactly the accepted document's bytes.
    assert source["file_hash"].upper() == ACCEPTANCE_SHA256


def test_191_at_least_one_page_reference_is_attached(accepted):
    """Page 7, segment of page 7, and a span that points at the exact sentence."""
    row = _definition(accepted["connection"], "connected together is called node")
    assert row["page_number"] == 7
    assert collapse(row["text"][row["char_start"]: row["char_end"]]) == row["statement"]


def test_191_equations_are_extracted_because_the_document_contains_them(accepted):
    connection = accepted["connection"]
    row = connection.execute(
        "SELECT e.expression, e.canonical_form, s.page_number FROM equation e "
        "JOIN source_occurrence s ON s.knowledge_id = e.knowledge_id "
        "WHERE e.expression = 'Req = R1 + R2'"
    ).fetchone()
    assert row is not None
    assert row["canonical_form"] is None  # parsing is Phase 11
    certainty = accepted["connection"].execute(
        "SELECT DISTINCT certainty FROM knowledge_object WHERE knowledge_type = 'EQUATION'"
    ).fetchall()
    # About 45% of a random sample were flattened fragments, so none is claimed
    # with more certainty than the text layer supports.
    assert [r[0] for r in certainty] == ["UNCERTAIN"]
    assert row["page_number"] == 18
    assert accepted["report"].stored["equations"] >= 1


# ------------------------------------------------------ section 190, all eleven


def test_190_all_eleven_extractors_ran_on_the_real_document(accepted):
    report = accepted["report"]
    assert tuple(report.found) == CATEGORIES and len(CATEGORIES) == 11
    for category in ("concepts", "definitions", "equations", "variables", "units",
                     "properties", "rules", "examples", "procedures"):
        assert report.stored[category] >= 1, category


def test_190_the_one_documented_procedure_is_found(accepted):
    """The Superposition steps on page 28 - the only 'Step N' sequence in the book."""
    row = accepted["connection"].execute(
        "SELECT p.documentation_status, p.execution_count, s.page_number FROM procedure p "
        "JOIN source_occurrence s ON s.knowledge_id = p.knowledge_id"
    ).fetchone()
    assert tuple(row) == ("DOCUMENTED_PROCEDURE", 0, 28)


def test_190_zero_prerequisites_is_the_correct_answer_for_this_document(accepted):
    """The document contains no prerequisite language. Any prerequisite here would
    have been invented - so this guards against a false positive, not a gap."""
    connection = accepted["connection"]
    assert accepted["report"].found["prerequisites"] == 0
    assert connection.execute(
        "SELECT count(*) FROM relationship WHERE relation_type = 'PREREQUISITE_OF'"
    ).fetchone()[0] == 0


# ------------------------------------------------------------- truthfulness


def test_multiple_choice_distractors_are_never_stored_as_knowledge(accepted):
    """Page 129's option "(iv) Resonant frequency depends on resistance." is false."""
    connection = accepted["connection"]
    hits = connection.execute(
        "SELECT count(*) FROM knowledge_object WHERE statement LIKE '%Resonant frequency depends on resistance%'"
    ).fetchone()[0]
    assert hits == 0


def test_glyph_corrupted_text_is_recorded_not_stored(accepted):
    """On page 83 the proportional sign arrives as 'a' ("V a I")."""
    connection = accepted["connection"]
    assert connection.execute(
        "SELECT count(*) FROM knowledge_object WHERE statement LIKE '%V a I%'"
    ).fetchone()[0] == 0
    issue = connection.execute(
        "SELECT detail FROM extraction_issue WHERE issue_type = 'OCR_UNCERTAINTY' "
        "AND page_number = 83 AND excerpt LIKE '%V a I%'"
    ).fetchone()
    assert issue is not None and "No OCR ran" in issue["detail"]


def test_malformed_equations_are_recorded_verbatim(accepted):
    connection = accepted["connection"]
    excerpts = {r[0] for r in connection.execute(
        "SELECT excerpt FROM extraction_issue WHERE issue_type = 'INVALID_EQUATION_STRUCTURE'")}
    assert excerpts
    stored = {r[0] for r in connection.execute("SELECT expression FROM equation")}
    assert not (excerpts & stored)  # nothing recorded as broken was also stored


def test_issue_types_recorded_on_the_real_document(accepted):
    issues = accepted["report"].issues
    for issue_type in ("INVALID_EQUATION_STRUCTURE", "OCR_UNCERTAINTY", "UNKNOWN_VARIABLE",
                       "BROKEN_RELATIONSHIP"):
        assert issues.get(issue_type, 0) >= 1, issue_type
    assert issues.get("MALFORMED_METADATA") == 2  # raw PDF date; /Producer as publisher
    assert "DUPLICATE_CONCEPT" not in issues and "POTENTIAL_CONTRADICTION" not in issues


# ----------------------------------------------- invariants over the real database


def test_every_span_on_the_real_document_points_at_its_evidence(accepted):
    rows = accepted["connection"].execute(
        "SELECT o.original_text, o.char_start, o.char_end, g.text FROM source_occurrence o "
        "JOIN document_segment g ON g.id = o.segment_id "
        "UNION ALL SELECT o.original_text, o.char_start, o.char_end, g.text "
        "FROM relationship_occurrence o JOIN document_segment g ON g.id = o.segment_id"
    ).fetchall()
    mismatched = [r[0][:60] for r in rows if collapse(r[3][r[1]: r[2]]) != collapse(r[0])]
    assert rows and mismatched == []


def test_every_real_knowledge_object_has_complete_provenance(accepted):
    orphans = accepted["connection"].execute(
        "SELECT count(*) FROM knowledge_object k WHERE NOT EXISTS ("
        " SELECT 1 FROM source_occurrence s WHERE s.knowledge_id = k.id AND s.page_number IS NOT NULL"
        " AND s.segment_id IS NOT NULL AND s.extraction_run_id IS NOT NULL AND s.char_start IS NOT NULL)"
    ).fetchone()[0]
    assert orphans == 0


def test_the_real_graph_is_explicit_and_evidenced(accepted):
    connection = accepted["connection"]
    assert connection.execute("SELECT count(*) FROM relationship WHERE origin <> 'EXPLICIT'").fetchone()[0] == 0
    integrity = queries.graph_integrity(connection)
    assert integrity.explicit_without_evidence == () and integrity.foreign_key_violations == ()


def test_no_phase_8_or_later_state_on_the_real_document(accepted):
    connection = accepted["connection"]
    assert connection.execute(
        "SELECT count(*) FROM knowledge_object WHERE normalized_hash IS NOT NULL").fetchone()[0] == 0
    assert connection.execute(
        "SELECT count(*) FROM equation WHERE canonical_form IS NOT NULL").fetchone()[0] == 0
    for table in ("conflict", "verification", "intent", "action", "execution_plan"):
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table


# --------------------------------------------------------- run, status, original


def test_the_real_run_is_partial_and_the_document_is_downgraded_not_completed(accepted):
    report = accepted["report"]
    assert report.status is ExtractionRunStatus.PARTIAL
    assert report.document_status_before is DocumentProcessingStatus.PROCESSED
    assert report.document_status_after is DocumentProcessingStatus.PARTIALLY_PROCESSED
    stored = accepted["repository"].get(Document, accepted["document"].id)
    assert stored.processing_status is DocumentProcessingStatus.PARTIALLY_PROCESSED


def test_the_question_bank_was_recognised(accepted):
    report = accepted["report"]
    assert report.pages_examined == 137
    assert report.question_bank_pages >= 50


def test_the_original_pdf_is_intact(accepted):
    """Part 5 section 189's one `must`, re-checked after Phase 5 has read the file."""
    assert _sha256(ACCEPTANCE_PDF) == accepted["before_hash"] == ACCEPTANCE_SHA256
    assert ACCEPTANCE_PDF.stat().st_mtime_ns == accepted["before_mtime"]


def test_extraction_fits_this_machine(accepted):
    """Measured, loosely bounded: a regression guard, not a performance claim."""
    assert accepted["ingest_seconds"] < 60
    assert accepted["extract_seconds"] < 120
