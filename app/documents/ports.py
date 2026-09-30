"""The PDF parser port and the value types crossing it (Phase 4, ADR 0014).

Part 1 section 4 requires the database to be replaceable; the same reasoning
applies to the parser. `pypdf` is the adopted implementation (decision D-03) and is
imported by **exactly one module** - `app/documents/pypdf_adapter.py`. Everything
else, including the pipeline, depends on `PdfParser` and on the plain value types
below.

That constraint is enforced by a test, not by convention: swapping to `pymupdf`
later must be one adapter, and it only stays one adapter if nothing else reaches
for the library.

The types here are deliberately thin. They carry what Part 2 section 32 requires an
extracted segment to retain - `page_number`, `text`, order, extraction method - and
nothing the parser cannot honestly supply. In particular there are **no bounding
boxes**: section 34 asks for a bounding region *"where practical"*, and with
`pypdf` it is not practical (ADR 0014).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PdfMetadata:
    """Bibliographic metadata as the file itself reports it (Part 2 section 29).

    Every field is optional and defaults to `None`, because section 29 is explicit:
    *"Do not invent metadata that is not available. Unknown metadata should remain
    unknown."* A missing title is `None`, never an empty string and never a guess
    derived from the filename.

    The seven "where available" fields of section 29 - ISBN, DOI, URL, copyright,
    description, subject, keywords - are **not** here: they are deferred
    (ADR 0016), and a field with nowhere to be stored would be a false promise.
    """

    title: str | None = None
    author: str | None = None
    publisher: str | None = None
    publication_date: str | None = None
    language: str | None = None
    page_count: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """One page's text, as extracted (Part 2 section 32).

    Pages are yielded one at a time rather than collected, because Part 4 section
    157 forbids loading an enormous document entirely into RAM when it is
    unnecessary.

    `page_number` is **1-based**, matching how a reader cites a page and how Part 2
    section 34 expects provenance to read. `failed` records a page the parser could
    not read at all - which Part 2 section 61 says must be recorded rather than
    silently dropped.
    """

    page_number: int
    text: str
    #: How the text was obtained. Phase 4 produces only native extraction; OCR
    #: would set this differently, and is not implemented (ADR 0016).
    extraction_method: str = "pypdf"
    #: Set when this page could not be read. The page still appears in the
    #: sequence, with empty text, so the gap is visible rather than invisible.
    failed: bool = False
    failure_reason: str | None = None
    #: "NATIVE_TEXT" or "OCR" when the reader knows how the text was obtained; None lets
    #: the pipeline judge native PDF text by its quality, as it always has.
    origin: str | None = None
    #: A confidence the recognizer itself reported, never an invented one.
    confidence: float | None = None
    #: Headings the format states explicitly (level, title), e.g. Word heading styles.
    headings: tuple[tuple[int, str], ...] = ()
    #: How a reader names this location: "Slide 3", "Sheet Data", "Section 2: Units".
    location: str | None = None
    #: OCR layout (lines and word boxes), kept beside the document for inspection.
    layout: dict | None = None
    #: Display equations rebuilt from the page's glyph placement (`pdfmath.LaidOutEquation`).
    equations: tuple = ()
    #: Why the page's glyph layout could not be read, when it could not.
    layout_note: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """What a parser reports about a file before its pages are read."""

    page_count: int
    metadata: PdfMetadata = field(default_factory=PdfMetadata)
    #: True when the file is encrypted and could not be opened for reading.
    encrypted: bool = False


@runtime_checkable
class PdfParser(Protocol):
    """What the pipeline needs from a PDF library, and nothing more.

    Kept narrow on purpose: the smaller this surface, the cheaper a future swap to
    a different library is.
    """

    @property
    def name(self) -> str:
        """Identifies the implementation, recorded as the extraction method."""

    def open(self, path: Path) -> ParsedDocument:
        """Read page count and metadata without extracting text."""

    def pages(self, path: Path):
        """Yield `ParsedPage` objects one at a time, in document order.

        An iterator rather than a list: Part 4 section 157.
        """
