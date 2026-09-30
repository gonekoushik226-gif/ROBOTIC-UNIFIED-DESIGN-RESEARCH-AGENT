"""Phase 4 unit tests: validation, hashing, preservation, quality, structure.

Each test names the specification section it defends. Nothing here needs a
database, and nothing here needs the prototype PDF that has not been supplied.
"""

from __future__ import annotations

import pathlib

import pytest

from app.core.errors import RudraError
from app.documents import intake
from app.documents.ports import PdfParser
from app.documents.pypdf_adapter import PypdfParser
from app.documents.structure import assign_parents, detect_headings
from app.documents.text_quality import MIN_CHARACTERS, assess
from app.models.enums import DocumentKind, StructureOrigin, TextOrigin
from tests.unit.pdf_fixtures import flat_pdf, textbook_pdf


@pytest.fixture
def pdf(tmp_path: pathlib.Path):
    def write(data: bytes, name: str = "book.pdf") -> pathlib.Path:
        path = tmp_path / name
        path.write_bytes(data)
        return path

    return write


# ------------------------------------------------------- validation (section 188)


def test_a_valid_pdf_passes(pdf):
    result = intake.validate(pdf(textbook_pdf()))
    assert result.size > 0
    assert result.mime_type == "application/pdf"


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(RudraError, match="does not exist"):
        intake.validate(tmp_path / "absent.pdf")


def test_a_directory_is_refused(tmp_path):
    with pytest.raises(RudraError, match="not a file"):
        intake.validate(tmp_path)


def test_an_empty_file_is_refused(pdf):
    with pytest.raises(RudraError, match="empty"):
        intake.validate(pdf(b""))


def test_the_extension_is_not_trusted(pdf):
    """Part 5 section 188 calls this "PDF detection". The bytes decide, not the name."""
    with pytest.raises(RudraError, match="not a PDF"):
        intake.validate(pdf(b"PK\x03\x04 this is a zip pretending", "book.pdf"))


def test_a_pdf_with_a_wrong_extension_is_still_a_pdf(pdf):
    assert intake.validate(pdf(textbook_pdf(), "book.bin")).size > 0


def test_a_file_over_the_limit_is_refused(pdf):
    with pytest.raises(RudraError, match="larger than"):
        intake.validate(pdf(textbook_pdf()), max_bytes=10)


# ---------------------------------------------------------- hashing (section 30)


def test_hashing_is_sha256_and_stable(pdf):
    import hashlib

    data = textbook_pdf()
    path = pdf(data)
    assert intake.hash_file(path) == hashlib.sha256(data).hexdigest()
    assert intake.hash_file(path) == intake.hash_file(path)


def test_different_bytes_hash_differently(pdf):
    a = intake.hash_file(pdf(textbook_pdf(), "a.pdf"))
    b = intake.hash_file(pdf(flat_pdf(), "b.pdf"))
    assert a != b


def test_hashing_streams_rather_than_loading(pdf, monkeypatch):
    """Part 4 section 157: a large document must not be read into RAM at once."""
    reads: list[int] = []
    original = pathlib.Path.open

    def spy(self, *args, **kwargs):
        handle = original(self, *args, **kwargs)
        real_read = handle.read

        def counted(size=-1):
            reads.append(size)
            return real_read(size)

        handle.read = counted  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(pathlib.Path, "open", spy)
    intake.hash_file(pdf(textbook_pdf()))
    assert reads, "no reads observed"
    assert all(size == intake._BLOCK_SIZE for size in reads), reads


# ------------------------------------------------------ preservation (section 30)


def test_preservation_copies_and_leaves_the_original_untouched(pdf, tmp_path):
    """Part 5 section 189's only `must`: "The original PDF must remain intact." """
    source = pdf(textbook_pdf())
    before = source.read_bytes()
    before_mtime = source.stat().st_mtime

    validated = intake.validate(source)
    digest = intake.hash_file(source)
    preserved = intake.preserve(validated, digest, tmp_path / "documents")

    assert source.exists()
    assert source.read_bytes() == before
    assert source.stat().st_mtime == before_mtime
    assert preserved.stored_path.exists()
    assert preserved.stored_path != source
    assert preserved.stored_path.read_bytes() == before
    assert preserved.verified is True


def test_the_stored_copy_is_named_by_content_hash(pdf, tmp_path):
    """A hostile original filename cannot influence where the copy lands."""
    source = pdf(textbook_pdf(), "..\\..\\evil name.pdf".replace("\\", "_"))
    validated = intake.validate(source)
    digest = intake.hash_file(source)
    preserved = intake.preserve(validated, digest, tmp_path / "documents")
    assert preserved.stored_path.name == f"{digest}.pdf"
    assert preserved.stored_path.parent == (tmp_path / "documents")


def test_preserving_the_same_bytes_twice_reuses_one_copy(pdf, tmp_path):
    source = pdf(textbook_pdf())
    validated = intake.validate(source)
    digest = intake.hash_file(source)
    first = intake.preserve(validated, digest, tmp_path / "documents")
    second = intake.preserve(validated, digest, tmp_path / "documents")
    assert first.stored_path == second.stored_path
    assert len(list((tmp_path / "documents").iterdir())) == 1


def test_a_corrupted_copy_is_detected(pdf, tmp_path, monkeypatch):
    """The copy is re-hashed, not assumed. A silent bad copy would be worse than a
    failed ingestion."""
    source = pdf(textbook_pdf())
    validated = intake.validate(source)
    digest = intake.hash_file(source)

    import shutil

    def bad_copy(src, dst, **kwargs):
        pathlib.Path(dst).write_bytes(b"%PDF-1.4 corrupted")
        return dst

    monkeypatch.setattr(shutil, "copy2", bad_copy)
    with pytest.raises(RudraError, match="does not match the original"):
        intake.preserve(validated, digest, tmp_path / "documents")


# ------------------------------------------------------- text quality (section 33)


def test_ordinary_prose_needs_no_ocr():
    verdict = assess(1, "A flip-flop is a bistable circuit that stores one bit.")
    assert verdict.needs_ocr is False
    assert verdict.usable is True
    assert verdict.origin is TextOrigin.NATIVE_TEXT
    assert verdict.reason is None


def test_an_empty_page_is_reported_as_needing_ocr():
    verdict = assess(3, "   ")
    assert verdict.needs_ocr is True
    assert verdict.page_number == 3
    assert "characters" in (verdict.reason or "")


def test_a_page_of_noise_is_reported_as_needing_ocr():
    verdict = assess(1, "!@#$%^&*()_+-=[]{};':\",./<>?|\\~`!@#$%^&*()_+-=[]{}")
    assert verdict.needs_ocr is True
    assert "letters or digits" in (verdict.reason or "")


def test_a_page_needing_ocr_is_never_labelled_as_ocr_derived():
    """Part 2 section 33 / Part 1 section 6: no OCR ran, so claiming OCR provenance
    would be inventing one."""
    verdict = assess(1, "")
    assert verdict.origin is TextOrigin.UNKNOWN
    assert verdict.origin is not TextOrigin.OCR


def test_the_threshold_boundary_behaves():
    assert assess(1, "x" * (MIN_CHARACTERS - 1)).needs_ocr is True
    assert assess(1, "a" * MIN_CHARACTERS).needs_ocr is False


# --------------------------------------------------- structure detection (section 35)


def test_decimal_numbering_is_detected():
    found = detect_headings(1, "5.2  Counters\nA counter counts pulses.")
    assert [h.kind for h in found] == [DocumentKind.SECTION]
    assert found[0].label == "5.2"
    assert found[0].title == "Counters"


def test_depth_comes_from_the_label_shape():
    assert detect_headings(1, "5.1  A")[0].kind is DocumentKind.SECTION
    assert detect_headings(1, "5.1.1  A")[0].kind is DocumentKind.SUBSECTION
    assert detect_headings(1, "5.1.1.1  A")[0].kind is DocumentKind.SUB_SUBSECTION


def test_the_unit_module_scheme_is_detected():
    """Part 2 section 35's second example. A decimal-only detector would miss it,
    and section 35 says detection "must be adaptive"."""
    found = detect_headings(1, "Unit III  Analog Electronics")
    assert found and found[0].kind is DocumentKind.CHAPTER
    assert found[0].label == "III"

    module = detect_headings(1, "Module 2  Amplifiers")
    assert module and module[0].kind is DocumentKind.SECTION


def test_labelled_kinds_are_detected():
    for line, kind in [
        ("Definition 5.1  A latch stores a level.", DocumentKind.DEFINITION),
        ("Theorem 2  Superposition holds.", DocumentKind.THEOREM),
        ("Example 3.2  Compute the gain.", DocumentKind.EXAMPLE),
        ("Proof  By induction on n.", DocumentKind.PROOF),
    ]:
        found = detect_headings(1, line)
        assert found and found[0].kind is kind, line


def test_a_document_with_no_hierarchy_yields_nothing():
    """Section 35: "another may have no explicit hierarchy". Nothing is invented."""
    for page in ("Some prose about semiconductors.", "More prose, still no heading."):
        assert detect_headings(1, page) == ()


def test_prose_that_merely_starts_with_a_number_is_not_a_heading():
    long_line = "5.2 " + "word " * 60
    assert detect_headings(1, long_line) == ()


def test_everything_detected_is_explicit_structure():
    """Phase 4 reads headings the document printed; it infers none (ADR 0015)."""
    found = detect_headings(1, "Chapter 5  Sequential Logic\n5.1  Flip-Flops")
    assert found
    assert all(h.origin is StructureOrigin.EXPLICIT_STRUCTURE for h in found)


def test_parents_follow_depth():
    headings = (
        detect_headings(1, "Chapter 5  Sequential Logic")
        + detect_headings(2, "5.1  Flip-Flops")
        + detect_headings(3, "5.2  Counters")
        + detect_headings(3, "5.2.1  Ripple Counters")
    )
    pairs = assign_parents(headings)
    assert [parent for _, parent in pairs] == [None, 0, 0, 2]


# -------------------------------------------------------------- parser adapter


def test_the_adapter_reports_pages_and_metadata(pdf):
    parser = PypdfParser()
    parsed = parser.open(pdf(textbook_pdf()))
    assert parsed.page_count == 4
    assert parsed.encrypted is False


def test_the_adapter_yields_pages_in_order(pdf):
    parser = PypdfParser()
    pages = list(parser.pages(pdf(textbook_pdf())))
    assert [p.page_number for p in pages] == [1, 2, 3, 4]
    assert "flip-flop" in pages[1].text.lower()
    assert all(p.extraction_method == "pypdf" for p in pages)


def test_the_adapter_refuses_a_non_pdf(tmp_path):
    bad = tmp_path / "x.pdf"
    bad.write_bytes(b"not a pdf at all")
    with pytest.raises(RudraError):
        PypdfParser().open(bad)


def test_unknown_metadata_stays_none(pdf):
    """Part 2 section 29: "Unknown metadata should remain unknown." """
    parsed = PypdfParser().open(pdf(textbook_pdf()))
    assert parsed.metadata.title is None
    assert parsed.metadata.author is None
    assert parsed.metadata.language is None
    assert parsed.metadata.page_count == 4


def test_the_adapter_satisfies_the_port():
    assert isinstance(PypdfParser(), PdfParser)
