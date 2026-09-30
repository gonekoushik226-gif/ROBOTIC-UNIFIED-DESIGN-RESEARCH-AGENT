"""The document ingestion pipeline, stages 1-8 (Part 5 section 188).

Part 2 section 31 is emphatic: *"Each stage must have a defined input and output.
Do not make the entire ingestion process one giant function."* So `ingest()` is a
**coordinator** - it calls stages and records what they produced. The work lives in
`intake`, the parser adapter, `text_quality` and `structure`, each independently
testable.

Where Phase 4 stops (ADR 0016): stage 8. No knowledge object, concept,
relationship or occurrence is created here. Part 5 section 188 says it in its own
words - *"Do not attempt to extract every possible type of knowledge immediately.
First establish reliable document processing."* A test asserts the knowledge tables
are still empty after an ingestion.

Two behaviours worth knowing before reading the code:

* **Same file twice.** Part 3 section 80: if the hash matches, the document is not
  ingested again and the existing record is reported. No duplicate source is
  created.
* **Partial failure.** Part 2 section 61: a document whose pages partly failed is
  recorded as `PARTIALLY_PROCESSED` with the affected pages and the reason, not
  discarded and not reported as complete.
"""

import json
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.core.errors import RudraError
from app.documents import intake, pdfmath
from app.documents.ports import PdfParser
from app.documents.structure import DetectedHeading, assign_parents, detect_headings
from app.documents.text_quality import TextQuality, assess
from app.models.base import utc_now
from app.models.entities import (
    Document,
    DocumentSegment,
    DocumentStructure,
    Source,
)
from app.models.enums import (
    Authorization,
    DocumentKind,
    DocumentProcessingStatus,
    SourceAvailability,
    SourceCategory,
    StructureOrigin,
    TextOrigin,
)
from app.storage import queries
from app.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class PageFailure:
    """One page that could not be read (Part 2 section 61)."""

    page_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """What an ingestion actually did. Every field is a fact, not a promise."""

    document: Document
    source: Source | None
    pages_extracted: int
    segments_created: int
    structure_elements: int
    #: Pages that yielded no usable text and would need OCR (Part 2 section 33).
    #: Phase 4 detects them; no OCR engine exists (decision D-04 open).
    pages_needing_ocr: tuple[int, ...] = ()
    #: Pages the parser could not read at all (Part 2 section 61).
    failures: tuple[PageFailure, ...] = ()
    #: True when this file was already in the knowledge base (Part 3 section 80).
    already_ingested: bool = False
    #: True when a re-upload restored a source that had been marked
    #: DELETED_BY_USER back to AVAILABLE (Part 7 section 20).
    source_reassociated: bool = False
    #: True when a re-upload put back RUDRA's preserved copy, which the user had
    #: deleted to save storage (Part 7 sections 14, 20; ADR 0055).
    file_restored: bool = False
    quality: tuple[TextQuality, ...] = field(default=())
    #: What the reader reported along the way (OCR used, OCR unavailable, ...).
    notes: tuple[str, ...] = ()
    #: Pages whose text was recognized by OCR (marked OCR, treated as uncertain).
    pages_ocr: tuple[int, ...] = ()

    @property
    def status(self) -> DocumentProcessingStatus:
        return self.document.processing_status

    @property
    def needs_ocr(self) -> bool:
        return bool(self.pages_needing_ocr)


class IngestionPipeline:
    """Stages 1-8, composed. Holds no state between documents."""

    __slots__ = ("_repository", "_parser", "_documents_dir", "_last_orphan")

    def __init__(
        self, repository: Repository, parser: PdfParser, documents_dir: Path
    ) -> None:
        self._repository = repository
        self._parser = parser
        self._documents_dir = Path(documents_dir)
        self._last_orphan: Path | None = None

    # ------------------------------------------------------------------ stages

    def ingest(
        self,
        path: Path,
        *,
        source_name: str | None = None,
        max_bytes: int | None = None,
    ) -> IngestionReport:
        """Run the pipeline for one file.

        Does **not** commit. The caller owns the transaction boundary, which is
        what lets a failed ingestion roll back cleanly and what satisfies Part 6
        section 19's *prepare → commit* shape.
        """
        # Stage 1-2: validate, hash. Neither touches the database. A multi-format reader
        # accepts any supported format, identified by its bytes (app/documents/formats.py).
        validated = intake.validate(path, max_bytes=max_bytes,
                                    any_format=bool(getattr(self._parser, "multi_format", False)))
        file_hash = intake.hash_file(validated.path)

        # Part 3 section 80: same bytes, same document. Checked before anything is
        # written, so a re-upload costs nothing.
        existing = self._find_by_hash(file_hash)
        if existing is not None:
            return self._reassociate(existing, validated, file_hash)

        # Stage 4 runs BEFORE stage 3 on purpose (see the class docstring). Opening
        # the ORIGINAL first means an encrypted or unreadable PDF is rejected before
        # any durable copy exists, so a failed ingestion cannot leave an orphan in
        # `data/documents/`. Opening is read-only, so Part 2 section 30 rule 6 -
        # "Never modify the original file during extraction" - is untouched.
        parsed = self._open(validated)
        if parsed.encrypted:
            raise RudraError.of(
                "That PDF is encrypted, so RUDRA cannot read it.",
                "Nothing was stored: the file is rejected before RUDRA keeps a copy. "
                "The original file was not modified.",
                stage="documents.pipeline.identify",
                data_changed=False,
                retry_safe=False,
                detail=str(validated.path),
                next_options=(
                    "Provide a decrypted copy.",
                    "RUDRA does not handle passwords for user files.",
                ),
            )

        # Stage 3: preserve the original, verified by hash. From here on, a failure
        # must undo this copy - see `_rollback_preserved`.
        preserved = intake.preserve(validated, file_hash, self._documents_dir)

        try:
            return self._ingest_preserved(validated, preserved, parsed, source_name)
        except Exception:
            self._rollback_preserved(preserved)
            raise

    def _ingest_preserved(self, validated, preserved, parsed, source_name):
        """Stages 5-8, once a verified copy exists."""
        now = utc_now()
        document = self._repository.add(
            Document(
                id=self._repository.new_id(Document),
                created_at=now,
                updated_at=now,
                filename=preserved.stored_path.name,
                original_filename=validated.path.name,
                source_type=validated.source_type,
                file_path=str(preserved.stored_path),
                file_hash=preserved.file_hash,
                file_size=preserved.size,
                mime_type=validated.mime_type,
                ingested_at=now,
                # PROCESSING until the pages are in. Part 6 section 19: nothing is
                # presented as ingested before it is.
                processing_status=DocumentProcessingStatus.PROCESSING,
                processing_version=1,
                document_title=parsed.metadata.title,
                author=parsed.metadata.author,
                publisher=parsed.metadata.publisher,
                publication_date=parsed.metadata.publication_date,
                language=parsed.metadata.language,
                page_count=parsed.page_count,
            )
        )

        source = self._repository.add(
            Source(
                id=self._repository.new_id(Source),
                created_at=now,
                updated_at=now,
                name=source_name or (parsed.metadata.title or validated.path.name),
                source_category=SourceCategory.USER_PROVIDED_SOURCE,
                authorization=Authorization.AUTHORIZED,
                availability=SourceAvailability.AVAILABLE,
                document_id=document.id,
                file_hash=preserved.file_hash,
            )
        )

        # Stages 5-8, page by page (Part 4 section 157).
        segments, quality, failures, headings, ocr_pages, layout_notes = self._read_pages(
            document, preserved.stored_path
        )
        structure_count = self._store_structure(document, headings)

        needing_ocr = tuple(q.page_number for q in quality if q.needs_ocr)
        final_status = self._final_status(quality, failures)

        document = self._repository.update(
            replace(document, processing_status=final_status)
        )

        return IngestionReport(
            document=document,
            source=source,
            pages_extracted=len(quality),
            segments_created=segments,
            structure_elements=structure_count,
            pages_needing_ocr=needing_ocr,
            failures=failures,
            already_ingested=False,
            quality=quality,
            notes=tuple(getattr(self._parser, "notes", ()) or ()) + layout_notes,
            pages_ocr=ocr_pages,
        )

    # ----------------------------------------------------------------- internals

    def _open(self, validated):
        """Stage 4's first half: what the reader reports before any text is read."""
        from app.documents.readers import DocumentReadError

        try:
            return self._parser.open(validated.path)
        except DocumentReadError as exc:
            raise RudraError.of(
                "RUDRA could not read that document.",
                f"{exc} Nothing was stored; the original file was not modified.",
                stage="documents.pipeline.identify",
                data_changed=False,
                retry_safe=False,
                detail=str(validated.path),
                next_options=("Check that the file opens in its own program.",
                              "Save it again in the same format, or as PDF, and import that."),
            ) from exc

    def _keep_math(self, stored: Path, page_number: int, placements: list) -> None:
        """Display equations rebuilt from a PDF page's glyph layout, with their evidence and doubts.

        Kept beside the document in the extracted/ folder: the rebuilt form, the page's
        original flat text of each equation, whether it was certain enough to replace
        that text, and the structural decisions behind it.
        """
        folder = self._documents_dir.parent / "extracted" / stored.stem
        folder.mkdir(parents=True, exist_ok=True)
        record = {"format": pdfmath.FORMAT_VERSION, "method": pdfmath.METHOD, "page": page_number,
                  "equations": [p.to_json() for p in placements]}
        (folder / f"page-{page_number}.math.json").write_text(json.dumps(record, ensure_ascii=False, indent=1),
                                                               encoding="utf-8")

    def _keep_layout(self, stored: Path, page_number: int, layout: dict) -> None:
        """OCR lines and word boxes, beside the document in the rebuildable extracted/ folder."""
        folder = self._documents_dir.parent / "extracted" / stored.stem
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"page-{page_number}.ocr.json").write_text(json.dumps(layout, ensure_ascii=False),
                                                              encoding="utf-8")

    def _read_pages(self, document: Document, stored: Path):
        """Stages 5-7: text extraction, quality assessment, page segmentation."""
        segments = 0
        quality: list[TextQuality] = []
        failures: list[PageFailure] = []
        headings: list = []
        ocr_pages: list[int] = []
        rebuilt = uncertain = 0
        unreadable_layout: list[int] = []

        for page in self._parser.pages(stored):
            if page.failed:
                failures.append(
                    PageFailure(
                        page_number=page.page_number,
                        reason=page.failure_reason or "unknown",
                    )
                )

            verdict = assess(page.page_number, page.text)
            if page.origin == "OCR":
                # Recognized text is kept as OCR, never as the page's own text layer.
                recognized = bool(page.text.strip())
                verdict = replace(verdict, needs_ocr=not recognized,
                                  origin=TextOrigin.OCR if recognized else TextOrigin.UNKNOWN,
                                  reason="recognized by OCR" if recognized else verdict.reason)
                if recognized:
                    ocr_pages.append(page.page_number)
                if page.layout:
                    self._keep_layout(stored, page.page_number, page.layout)
            elif page.origin == "NATIVE_TEXT" and page.text.strip():
                # A structured document's text is its own, whatever its density.
                verdict = replace(verdict, needs_ocr=False, origin=TextOrigin.NATIVE_TEXT)
            quality.append(verdict)

            # Display equations rebuilt from the page's glyph layout replace their
            # flattened text only when every glyph's place was settled; the rest keep
            # the text as the page's text layer holds it (app/documents/pdfmath.py).
            text = page.text
            if page.equations and page.origin != "OCR":
                text, placements = pdfmath.place(page.text, list(page.equations))
                rebuilt += sum(1 for p in placements if p.certain)
                uncertain += sum(1 for p in placements if not p.certain)
                self._keep_math(stored, page.page_number, placements)
            if page.layout_note:
                unreadable_layout.append(page.page_number)

            now = utc_now()
            # The segment is stored even when the text is empty. Part 2 section 61:
            # a page that yielded nothing is a recorded gap, not an absent page.
            self._repository.add(
                DocumentSegment(
                    id=self._repository.new_id(DocumentSegment),
                    created_at=now,
                    updated_at=now,
                    document_id=document.id,
                    page_number=page.page_number,
                    ordinal=0,
                    text=text,
                    extraction_method=page.extraction_method,
                    text_origin=verdict.origin,
                    # Only a confidence the recognizer reported; never an invented one
                    # (Part 6 section 12). Windows' OCR engine reports none.
                    confidence=page.confidence,
                )
            )
            segments += 1

            if page.headings:
                headings.extend(_explicit_headings(page.page_number, page.headings))
            elif verdict.usable:
                headings.extend(detect_headings(page.page_number, text))

        notes: list[str] = []
        if rebuilt or uncertain:
            notes.append(f"{rebuilt + uncertain} display equation(s) were rebuilt from the page layout: {rebuilt} "
                         f"certain (stored in structured form), {uncertain} uncertain (the page text is kept as "
                         "printed and the equations are marked uncertain).")
        if unreadable_layout:
            notes.append(f"The glyph layout of {len(unreadable_layout)} page(s) could not be read, so their "
                         "equations keep the text layer's flat form.")
        return segments, tuple(quality), tuple(failures), tuple(headings), tuple(ocr_pages), tuple(notes)

    def _store_structure(self, document: Document, headings: tuple) -> int:
        """Stage 8: persist the detected structure (Part 2 section 35)."""
        if not headings:
            return 0
        created: list[str] = []
        for ordinal, (heading, parent_index) in enumerate(assign_parents(headings)):
            now = utc_now()
            element = self._repository.add(
                DocumentStructure(
                    id=self._repository.new_id(DocumentStructure),
                    created_at=now,
                    updated_at=now,
                    document_id=document.id,
                    kind=heading.kind,
                    ordinal=ordinal,
                    origin=heading.origin,
                    parent_id=created[parent_index] if parent_index is not None else None,
                    label=heading.label,
                    title=heading.title,
                    page_start=heading.page_number,
                    page_end=heading.page_number,
                )
            )
            created.append(element.id)
        return len(created)

    def _final_status(
        self, quality: tuple[TextQuality, ...], failures: tuple[PageFailure, ...]
    ) -> DocumentProcessingStatus:
        """Part 2 section 61, and the honesty rule behind it.

        A document is `PROCESSED` only when every page yielded usable text. If any
        page failed outright, or needs OCR that does not exist, the truthful answer
        is `PARTIALLY_PROCESSED` - the knowledge from those pages is missing, and
        reporting completion would hide that.
        """
        if not quality:
            return DocumentProcessingStatus.FAILED
        if failures or any(q.needs_ocr for q in quality):
            return DocumentProcessingStatus.PARTIALLY_PROCESSED
        return DocumentProcessingStatus.PROCESSED

    def _find_by_hash(self, file_hash: str) -> Document | None:
        return queries.document_by_hash(self._repository.connection, file_hash)

    def _reassociate(self, existing: Document, validated, file_hash: str) -> IngestionReport:
        """The same file, uploaded again (Part 3 section 80, Part 7 section 20).

        Section 80 settles what must *not* happen: no second document, no second
        source. Part 7 section 20 settles what must happen when the source had been
        marked deleted - it calls this "an important edge case" and prescribes:

            Existing source identity
                    -> Source file restored/re-associated
                    -> Existing knowledge reused
                    -> Source availability updated to AVAILABLE

        The user has just supplied the bytes again, so the evidence really is
        reachable once more, and leaving `availability` at `DELETED_BY_USER` would
        misreport the system's own state. Only that flag changes: the document, the
        source identity and any knowledge hanging off them are untouched, which is
        what "existing knowledge reused" means.

        A source whose file was never marked missing is left exactly as it is.

        If RUDRA's preserved copy is gone - the user deleted it to save storage (Part 7
        section 14; `python -m app source … --delete-file`) - the bytes just supplied are
        preserved again by the same verified stage 3, so AVAILABLE is true of the file,
        not only of the flag (ADR 0055).
        """
        file_restored = False
        if not Path(existing.file_path).exists():
            intake.preserve(validated, file_hash, self._documents_dir)
            file_restored = Path(existing.file_path).exists()
        sources = queries.sources_for_document(
            self._repository.connection, existing.id
        )
        source = sources[0] if sources else None
        restored = False
        if source is not None and source.availability is not SourceAvailability.AVAILABLE:
            source = self._repository.update(
                replace(source, availability=SourceAvailability.AVAILABLE)
            )
            restored = True
        return IngestionReport(
            document=existing,
            source=source,
            pages_extracted=0,
            segments_created=0,
            structure_elements=0,
            already_ingested=True,
            source_reassociated=restored,
            file_restored=file_restored,
        )

    def _rollback_preserved(self, preserved) -> None:
        if preserved.created:
            shutil.rmtree(self._documents_dir.parent / "extracted" / preserved.stored_path.stem, ignore_errors=True)
        self._rollback_copy(preserved)

    def _rollback_copy(self, preserved) -> None:
        """Undo this run's copy after a later stage failed (F-2).

        A preserved file with no document record is not preservation, it is a leak.
        Removing it puts the filesystem back where it was, which is the only state
        in which a failure report may honestly say `data_changed=False`.

        If removal fails, the original error is still what the caller sees - it is
        the more useful one - but the leftover path is recorded on this pipeline so
        `last_orphan` can report it rather than leaving it silent.
        """
        self._last_orphan = (
            None if intake.discard_preserved(preserved) else preserved.stored_path
        )

    @property
    def last_orphan(self):
        """The preserved copy a failed ingestion could not remove, if any.

        `None` means the last failure left nothing behind. This exists because
        "RUDRA wrote a file it could not clean up" is a fact the user is entitled
        to, and swallowing it would be the silent behaviour Part 1 section 6 rules
        out.
        """
        return self._last_orphan


def _explicit_headings(page_number: int, stated: tuple[tuple[int, str], ...]) -> list[DetectedHeading]:
    """Headings a structured format states (Word heading styles, HTML h1-h6, slide titles)."""
    kinds = {1: DocumentKind.CHAPTER, 2: DocumentKind.SECTION, 3: DocumentKind.SUBSECTION}
    return [DetectedHeading(page_number=page_number, kind=kinds.get(level, DocumentKind.SUB_SUBSECTION),
                            label=None, title=title[:300], depth=max(0, level - 1),
                            origin=StructureOrigin.EXPLICIT_STRUCTURE)
            for level, title in stated if title.strip()]
