"""Phase 4 ingestion, end to end (Part 5 sections 188-189).

**These tests run against synthetic PDFs.** No prototype technical document has been
supplied, so the Part 5 section 189 acceptance criteria are exercised but **not
claimed as met against a real book** - see `docs/phases/PHASE_4.md`. What is
established here is that every stage behaves correctly on input whose exact content
is known.
"""

from __future__ import annotations

import pathlib

import pytest

from app.core.errors import RudraError
from app.documents import IngestionPipeline, PypdfParser
from app.models import (
    Document,
    DocumentKind,
    DocumentProcessingStatus,
    DocumentSegment,
    DocumentStructure,
    Source,
    SourceAvailability,
    TextOrigin,
)
from app.storage import Repository, connect, migrate, queries
from tests.unit.pdf_fixtures import (
    flat_pdf,
    image_only_pdf,
    make_pdf,
    textbook_pdf,
    unit_module_pdf,
)


@pytest.fixture
def ingestion(tmp_path: pathlib.Path):
    """A migrated database, a pipeline, and somewhere to put documents."""
    (tmp_path / "database").mkdir()
    (tmp_path / "backups").mkdir()
    documents = tmp_path / "documents"
    db = tmp_path / "database" / "knowledge.db"
    connection = connect(db)
    migrate(connection, database_path=db)
    repository = Repository(connection)
    pipeline = IngestionPipeline(repository, PypdfParser(), documents)

    def write(data: bytes, name: str = "book.pdf") -> pathlib.Path:
        path = tmp_path / name
        path.write_bytes(data)
        return path

    yield {
        "connection": connection,
        "repository": repository,
        "pipeline": pipeline,
        "documents": documents,
        "write": write,
        "db": db,
    }
    connection.close()


# ------------------------------------------- Part 5 section 189, step by step


def test_the_section_189_flow(ingestion):
    """PDF -> Validated -> Hashed -> Stored/preserved -> Pages extracted
    -> Text associated with pages -> Metadata stored, original intact."""
    source = ingestion["write"](textbook_pdf(), "Digital_Electronics.pdf")
    original_bytes = source.read_bytes()

    report = ingestion["pipeline"].ingest(source, source_name="Digital Electronics")
    ingestion["connection"].commit()

    # Validated + Hashed
    assert report.document.file_hash and len(report.document.file_hash) == 64
    assert report.document.mime_type == "application/pdf"

    # Stored / preserved
    stored = pathlib.Path(report.document.file_path)
    assert stored.exists()
    assert stored.parent == ingestion["documents"]
    assert stored.read_bytes() == original_bytes

    # THE original must remain intact - section 189's only `must`.
    assert source.exists()
    assert source.read_bytes() == original_bytes

    # Pages extracted, text associated with pages
    assert report.pages_extracted == 4
    segments = queries.segments_for_document(ingestion["connection"], report.document.id)
    assert [s.page_number for s in segments] == [1, 2, 3, 4]
    assert "flip-flop" in segments[1].text.lower()
    assert all(s.text_origin is TextOrigin.NATIVE_TEXT for s in segments)
    assert all(s.document_id == report.document.id for s in segments)

    # Metadata stored
    assert report.document.page_count == 4
    assert report.document.original_filename == "Digital_Electronics.pdf"
    assert report.document.processing_status is DocumentProcessingStatus.PROCESSED


def test_text_is_not_one_giant_string(ingestion):
    """Part 2 section 32 forbids producing only one giant text string."""
    source = ingestion["write"](textbook_pdf())
    report = ingestion["pipeline"].ingest(source)
    ingestion["connection"].commit()
    segments = queries.segments_for_document(ingestion["connection"], report.document.id)
    assert len(segments) == 4
    assert len({s.page_number for s in segments}) == 4


def test_the_original_survives_a_reopened_database(ingestion):
    source = ingestion["write"](textbook_pdf())
    before = source.read_bytes()
    report = ingestion["pipeline"].ingest(source)
    ingestion["connection"].commit()
    ingestion["connection"].close()

    reopened = connect(ingestion["db"])
    try:
        stored = Repository(reopened).get(Document, report.document.id)
        assert stored is not None
        assert stored.file_hash == report.document.file_hash
        assert source.read_bytes() == before
    finally:
        reopened.close()


# --------------------------------------------------- repeated ingestion (§80)


def test_the_same_file_twice_creates_one_document(ingestion):
    """Part 3 section 80: the same file must not become a second document."""
    source = ingestion["write"](textbook_pdf())
    first = ingestion["pipeline"].ingest(source)
    ingestion["connection"].commit()

    second = ingestion["pipeline"].ingest(source)
    ingestion["connection"].commit()

    assert second.already_ingested is True
    assert second.document.id == first.document.id
    assert ingestion["repository"].count(Document) == 1
    assert ingestion["repository"].count(Source) == 1


def test_a_renamed_copy_is_still_the_same_document(ingestion):
    """Identity is the content hash, not the filename."""
    data = textbook_pdf()
    first = ingestion["pipeline"].ingest(ingestion["write"](data, "a.pdf"))
    second = ingestion["pipeline"].ingest(ingestion["write"](data, "b.pdf"))
    ingestion["connection"].commit()
    assert second.already_ingested is True
    assert second.document.id == first.document.id


def test_a_different_document_is_ingested_separately(ingestion):
    first = ingestion["pipeline"].ingest(ingestion["write"](textbook_pdf(), "a.pdf"))
    second = ingestion["pipeline"].ingest(ingestion["write"](flat_pdf(), "b.pdf"))
    ingestion["connection"].commit()
    assert second.already_ingested is False
    assert first.document.id != second.document.id
    assert ingestion["repository"].count(Document) == 2


# --------------------------------------------------------- OCR detection (§33)


def test_a_page_without_text_is_reported_as_needing_ocr(ingestion):
    """Part 2 section 33's detection step. No OCR engine exists (D-04 open), so the
    honest outcome is a reported gap, not a silently empty document."""
    source = ingestion["write"](image_only_pdf())
    report = ingestion["pipeline"].ingest(source)
    ingestion["connection"].commit()

    assert report.needs_ocr is True
    assert report.pages_needing_ocr == (1,)
    assert report.status is DocumentProcessingStatus.PARTIALLY_PROCESSED

    segments = queries.segments_for_document(ingestion["connection"], report.document.id)
    assert len(segments) == 1
    # The page is stored as a recorded gap, not dropped.
    assert segments[0].text_origin is TextOrigin.UNKNOWN
    assert segments[0].text_origin is not TextOrigin.OCR


def test_a_readable_document_needs_no_ocr(ingestion):
    report = ingestion["pipeline"].ingest(ingestion["write"](textbook_pdf()))
    ingestion["connection"].commit()
    assert report.needs_ocr is False
    assert report.pages_needing_ocr == ()


# ------------------------------------------------- structure detection (§35)


def test_decimal_structure_is_detected_and_nested(ingestion):
    report = ingestion["pipeline"].ingest(ingestion["write"](textbook_pdf()))
    ingestion["connection"].commit()

    elements = queries.structure_for_document(ingestion["connection"], report.document.id)
    # Keyed by (kind, label), not label alone: this fixture deliberately contains
    # both "5.1  Flip-Flops" and "Definition 5.1", which share a label. ADR 0015
    # declined a uniqueness constraint on (document_id, label) for exactly this
    # reason - a book may legitimately reuse a number.
    by_key = {(e.kind, e.label): e for e in elements}
    chapter = by_key[(DocumentKind.CHAPTER, "5")]
    section_one = by_key[(DocumentKind.SECTION, "5.1")]
    section_two = by_key[(DocumentKind.SECTION, "5.2")]
    subsection = by_key[(DocumentKind.SUBSECTION, "5.2.1")]

    assert chapter.parent_id is None
    assert section_one.parent_id == chapter.id
    assert section_two.parent_id == chapter.id
    assert subsection.parent_id == section_two.id


def test_a_repeated_label_is_allowed(ingestion):
    """ADR 0015: a textbook may reuse "5.1" for a section and a definition."""
    report = ingestion["pipeline"].ingest(ingestion["write"](textbook_pdf()))
    ingestion["connection"].commit()
    elements = queries.structure_for_document(ingestion["connection"], report.document.id)
    labels = [e.label for e in elements]
    assert labels.count("5.1") == 2
    kinds = {e.kind for e in elements if e.label == "5.1"}
    assert kinds == {DocumentKind.SECTION, DocumentKind.DEFINITION}


def test_the_unit_module_scheme_is_detected(ingestion):
    """Part 2 section 35's second numbering scheme. This is what "adaptive" means."""
    report = ingestion["pipeline"].ingest(ingestion["write"](unit_module_pdf()))
    ingestion["connection"].commit()
    elements = queries.structure_for_document(ingestion["connection"], report.document.id)
    # Exact, against the known fixture: "Unit III", "Module 2", "Topic A".
    assert report.structure_elements == 3
    assert [(e.kind, e.label) for e in elements] == [
        (DocumentKind.CHAPTER, "III"),
        (DocumentKind.SECTION, "2"),
        (DocumentKind.SUBSECTION, "A"),
    ]


def test_a_document_with_no_headings_yields_no_structure(ingestion):
    """Section 35: "another may have no explicit hierarchy". Nothing is invented."""
    report = ingestion["pipeline"].ingest(ingestion["write"](flat_pdf()))
    ingestion["connection"].commit()
    assert report.structure_elements == 0
    assert queries.structure_for_document(ingestion["connection"], report.document.id) == ()


def test_all_detected_structure_is_explicit(ingestion):
    from app.models.enums import StructureOrigin

    report = ingestion["pipeline"].ingest(ingestion["write"](textbook_pdf()))
    ingestion["connection"].commit()
    elements = queries.structure_for_document(ingestion["connection"], report.document.id)
    assert elements
    assert all(e.origin is StructureOrigin.EXPLICIT_STRUCTURE for e in elements)


# --------------------------------------------- the Phase 4 stop line (ADR 0016)


def test_ingestion_creates_no_knowledge(ingestion):
    """Part 5 section 188: "Do not attempt to extract every possible type of
    knowledge immediately." Phase 4 stops at stage 8."""
    ingestion["pipeline"].ingest(ingestion["write"](textbook_pdf()))
    ingestion["connection"].commit()
    counts = {
        table: ingestion["connection"]
        .execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table names
        .fetchone()[0]
        for table in (
            "knowledge_object",
            "concept",
            "concept_alias",
            "relationship",
            "source_occurrence",
            "concept_occurrence",
            "relationship_occurrence",
            "equation",
        )
    }
    assert counts == dict.fromkeys(counts, 0), counts


def test_ingestion_creates_an_authorized_available_source(ingestion):
    report = ingestion["pipeline"].ingest(ingestion["write"](textbook_pdf()))
    ingestion["connection"].commit()
    assert report.source is not None
    assert report.source.document_id == report.document.id
    assert report.source.availability is SourceAvailability.AVAILABLE
    assert report.source.file_hash == report.document.file_hash


# --------------------------------------------------------- failure behaviour


def test_a_non_pdf_is_refused_and_writes_nothing(ingestion):
    bad = ingestion["write"](b"PK\x03\x04 not a pdf", "fake.pdf")
    with pytest.raises(RudraError, match="not a PDF"):
        ingestion["pipeline"].ingest(bad)
    assert ingestion["repository"].count(Document) == 0
    # Unconditional: an `or` on `.exists()` would short-circuit and never check
    # the directory contents at all.
    stored = (
        sorted(p.name for p in ingestion["documents"].iterdir())
        if ingestion["documents"].exists()
        else []
    )
    assert stored == [], f"validation wrote something it should not have: {stored}"


def test_a_missing_file_is_refused(ingestion, tmp_path):
    with pytest.raises(RudraError, match="does not exist"):
        ingestion["pipeline"].ingest(tmp_path / "nope.pdf")
    assert ingestion["repository"].count(Document) == 0


def test_part_7_still_holds_for_an_ingested_document(ingestion):
    """Deleting a source row is blocked while a document depends on it."""
    import sqlite3

    report = ingestion["pipeline"].ingest(ingestion["write"](textbook_pdf()))
    ingestion["connection"].commit()
    with pytest.raises(sqlite3.IntegrityError):
        ingestion["connection"].execute(
            "DELETE FROM document WHERE id = ?", (report.document.id,)
        )
    ingestion["connection"].rollback()
    assert ingestion["repository"].count(Document) == 1


def test_structure_rows_block_deleting_their_document(ingestion):
    import sqlite3

    report = ingestion["pipeline"].ingest(ingestion["write"](textbook_pdf()))
    ingestion["connection"].commit()
    assert ingestion["repository"].count(DocumentStructure) > 0
    with pytest.raises(sqlite3.IntegrityError):
        ingestion["connection"].execute(
            "DELETE FROM document WHERE id = ?", (report.document.id,)
        )
    ingestion["connection"].rollback()


def test_segments_are_stored_for_every_page_including_empty_ones(ingestion):
    """Part 2 section 61: a page that yielded nothing is a recorded gap."""
    data = make_pdf(["Chapter 1  Real text on this page, plenty of it.", " "])
    report = ingestion["pipeline"].ingest(ingestion["write"](data))
    ingestion["connection"].commit()
    segments = queries.segments_for_document(ingestion["connection"], report.document.id)
    assert len(segments) == 2
    assert report.status is DocumentProcessingStatus.PARTIALLY_PROCESSED
    assert 2 in report.pages_needing_ocr
    assert ingestion["repository"].count(DocumentSegment) == 2


# ------------------------------------ F-1: Part 7 section 20, duplicate re-upload


def test_re_uploading_a_deleted_source_restores_it(ingestion):
    """Part 7 section 20 - "This is an important edge case."

    The prescribed flow is: existing source identity -> file re-associated ->
    existing knowledge reused -> **availability updated to AVAILABLE**. The user
    has just supplied the bytes again, so leaving the source at DELETED_BY_USER
    would misreport the system's own state.
    """
    from dataclasses import replace

    source_path = ingestion["write"](textbook_pdf())
    first = ingestion["pipeline"].ingest(source_path)
    ingestion["connection"].commit()

    ingestion["repository"].update(
        replace(first.source, availability=SourceAvailability.DELETED_BY_USER)
    )
    ingestion["connection"].commit()
    assert (
        ingestion["repository"].get(Source, first.source.id).availability
        is SourceAvailability.DELETED_BY_USER
    )

    again = ingestion["pipeline"].ingest(source_path)
    ingestion["connection"].commit()

    # Section 80 still holds: nothing new was created.
    assert again.already_ingested is True
    assert again.document.id == first.document.id
    assert ingestion["repository"].count(Document) == 1
    assert ingestion["repository"].count(Source) == 1

    # Part 7 section 20: the existing source is returned and restored.
    assert again.source is not None
    assert again.source.id == first.source.id
    assert again.source_reassociated is True
    assert (
        ingestion["repository"].get(Source, first.source.id).availability
        is SourceAvailability.AVAILABLE
    )


def test_re_uploading_an_available_source_changes_nothing(ingestion):
    """A source that was never marked missing is left exactly as it is."""
    source_path = ingestion["write"](textbook_pdf())
    first = ingestion["pipeline"].ingest(source_path)
    ingestion["connection"].commit()

    again = ingestion["pipeline"].ingest(source_path)
    ingestion["connection"].commit()

    assert again.already_ingested is True
    assert again.source_reassociated is False
    assert again.source.id == first.source.id
    assert again.source.availability is SourceAvailability.AVAILABLE


# ------------------------- F-2/F-3: encrypted PDF leaves nothing behind


def _encrypted_pdf() -> bytes:
    """A genuinely password-protected PDF."""
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_an_encrypted_pdf_is_refused_and_leaves_no_preserved_copy(ingestion):
    """F-2. The PDF is opened before anything durable is written.

    An earlier arrangement preserved the copy first and detected encryption
    second, which left a file in `data/documents/` with no document record - and
    reported `data_changed=False` while having written it. Both are asserted
    against here.
    """
    locked = ingestion["write"](_encrypted_pdf(), "locked.pdf")
    before = locked.read_bytes()

    with pytest.raises(RudraError, match="encrypted") as caught:
        ingestion["pipeline"].ingest(locked)
    ingestion["connection"].rollback()

    report = caught.value.report
    assert report.data_changed is False, "nothing was written, so this must be False"

    stored = (
        sorted(p.name for p in ingestion["documents"].iterdir())
        if ingestion["documents"].exists()
        else []
    )
    assert stored == [], f"orphaned preserved copy: {stored}"
    assert ingestion["repository"].count(Document) == 0
    assert ingestion["repository"].count(Source) == 0
    assert locked.read_bytes() == before
    assert ingestion["pipeline"].last_orphan is None


def test_a_failure_after_preserving_removes_the_copy(ingestion, monkeypatch):
    """F-2, the other half: a stage that fails *after* the copy exists.

    Opening first removes the encrypted case, but any later failure could still
    orphan a file. The pipeline undoes its own copy, so `data_changed=False` stays
    truthful.
    """
    source_path = ingestion["write"](textbook_pdf())

    def explode(*args, **kwargs):
        raise RuntimeError("page reading blew up")

    monkeypatch.setattr(type(ingestion["pipeline"]), "_read_pages", explode)

    with pytest.raises(RuntimeError, match="blew up"):
        ingestion["pipeline"].ingest(source_path)
    ingestion["connection"].rollback()

    stored = (
        sorted(p.name for p in ingestion["documents"].iterdir())
        if ingestion["documents"].exists()
        else []
    )
    assert stored == [], f"orphaned preserved copy: {stored}"
    assert ingestion["pipeline"].last_orphan is None
    assert ingestion["repository"].count(Document) == 0


def test_a_preserved_copy_that_predates_this_run_is_not_deleted(ingestion, monkeypatch):
    """Cleanup removes only what this run created, never an earlier copy."""
    source_path = ingestion["write"](textbook_pdf())
    first = ingestion["pipeline"].ingest(source_path)
    ingestion["connection"].commit()
    stored_path = pathlib.Path(first.document.file_path)
    assert stored_path.exists()

    # Force a later run past the duplicate check. It then fails at the database:
    # `ux_document_file_hash` refuses a second document with the same bytes, which
    # is section 80 enforced at the schema level. Either way it is a failure that
    # happens *after* preserve() ran, which is what this test needs.
    monkeypatch.setattr(
        type(ingestion["pipeline"]), "_find_by_hash", lambda self, digest: None
    )

    with pytest.raises(RudraError, match="same unique value"):
        ingestion["pipeline"].ingest(source_path)
    ingestion["connection"].rollback()

    # The pre-existing copy is still there: it was not this run's to delete.
    assert stored_path.exists()


# ---------------------------------- F-3: page-level parser failure (§61)


class _FlakyParser:
    """Yields three pages; the middle one fails, as a malformed page would."""

    name = "flaky"

    def open(self, path):
        from app.documents.ports import ParsedDocument, PdfMetadata

        return ParsedDocument(page_count=3, metadata=PdfMetadata(page_count=3))

    def pages(self, path):
        from app.documents.ports import ParsedPage

        yield ParsedPage(
            1, "Chapter 1  Real prose here, long enough to be usable.", "flaky"
        )
        yield ParsedPage(
            2, "", "flaky", failed=True, failure_reason="PdfReadError: broken xref"
        )
        yield ParsedPage(
            3, "More real prose on the third page, also long enough.", "flaky"
        )


def test_a_page_that_fails_to_parse_is_recorded_not_discarded(ingestion):
    """Part 2 section 61: "Do not discard the entire source silently."

    Records the pages affected, the extraction method and the failure reason, and
    keeps every page - including the one that failed.
    """
    from app.documents import IngestionPipeline

    source_path = ingestion["write"](textbook_pdf())
    pipeline = IngestionPipeline(
        ingestion["repository"], _FlakyParser(), ingestion["documents"]
    )
    report = pipeline.ingest(source_path)
    ingestion["connection"].commit()

    # Status is honest about the gap.
    assert report.status is DocumentProcessingStatus.PARTIALLY_PROCESSED

    # The failure is reported with its page and its reason.
    assert [f.page_number for f in report.failures] == [2]
    assert "broken xref" in report.failures[0].reason

    # Every page is still stored, including the one that failed.
    segments = queries.segments_for_document(
        ingestion["connection"], report.document.id
    )
    assert [s.page_number for s in segments] == [1, 2, 3]
    assert report.segments_created == 3

    failed_segment = segments[1]
    assert failed_segment.text == ""
    assert failed_segment.text_origin is TextOrigin.UNKNOWN
    assert failed_segment.text_origin is not TextOrigin.OCR
    assert failed_segment.extraction_method == "flaky"

    # The successful pages survived intact.
    assert "real prose" in segments[0].text.lower()
    assert "third page" in segments[2].text.lower()

    # A failed page is also a page needing OCR, and is reported as such.
    assert 2 in report.pages_needing_ocr
