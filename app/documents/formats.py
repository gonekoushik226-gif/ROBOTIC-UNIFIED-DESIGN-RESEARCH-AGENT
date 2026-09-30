"""Document formats: what a file is, and how its text is laid out for extraction.

**Identification never trusts the extension alone.** A file is recognised by its own
bytes where the format has a signature (PDF, PNG, JPEG, TIFF, RTF, and the ZIP
containers DOCX, PPTX, XLSX and EPUB, told apart by the parts inside them). Plain-text
formats have no signature, so for them the extension chooses among TXT, Markdown, CSV and
HTML, and the bytes must decode as text. Legacy binary Office files (DOC, XLS, PPT) are
recognised and refused, as are formats not in `FORMATS`.

**Readers** (in `app/documents/readers.py`) turn a file into `Block`s - headings,
paragraphs, list items, tables, captions and equations - which `units()` lays out as
located text: a page for a PDF or an image, a slide, a sheet, or a section of a document
without pages. Each unit becomes one stored segment, so every extracted statement keeps
the document and the location it came from.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

#: Plain-text files larger than this are refused rather than read into memory.
MAX_TEXT_BYTES = 64 * 1024 * 1024
#: A unit (section) grows to about this many characters before the next one starts.
UNIT_CHARACTERS = 6000


class UnsupportedFormat(ValueError):
    """The file is not a format RUDRA reads. `reason` says what it is, when known."""


@dataclass(frozen=True)
class DocumentFormat:
    key: str
    label: str
    #: Stored as the document's source_type.
    source_type: str
    mime_type: str
    #: The extension RUDRA's preserved copy is stored with.
    suffix: str
    #: What one stored location is: page, slide, sheet, section or image.
    unit: str
    #: True when text is obtained by optical character recognition.
    ocr: bool = False
    extensions: tuple[str, ...] = ()


FORMATS: dict[str, DocumentFormat] = {f.key: f for f in (
    DocumentFormat("pdf", "PDF document", "PDF", "application/pdf", ".pdf", "page", extensions=(".pdf",)),
    DocumentFormat("docx", "Word document", "DOCX",
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx", "section",
                   extensions=(".docx",)),
    DocumentFormat("pptx", "PowerPoint presentation", "PPTX",
                   "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx", "slide",
                   extensions=(".pptx",)),
    DocumentFormat("xlsx", "Excel workbook", "XLSX",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx", "sheet",
                   extensions=(".xlsx",)),
    DocumentFormat("epub", "EPUB book", "EPUB", "application/epub+zip", ".epub", "section", extensions=(".epub",)),
    DocumentFormat("html", "HTML page", "HTML", "text/html", ".html", "section",
                   extensions=(".html", ".htm", ".xhtml")),
    DocumentFormat("markdown", "Markdown text", "MARKDOWN", "text/markdown", ".md", "section",
                   extensions=(".md", ".markdown")),
    DocumentFormat("txt", "Plain text", "TEXT", "text/plain", ".txt", "section", extensions=(".txt", ".text")),
    DocumentFormat("csv", "CSV table", "CSV", "text/csv", ".csv", "section", extensions=(".csv",)),
    DocumentFormat("rtf", "Rich Text document", "RTF", "application/rtf", ".rtf", "section", extensions=(".rtf",)),
    DocumentFormat("png", "PNG image", "IMAGE", "image/png", ".png", "image", ocr=True, extensions=(".png",)),
    DocumentFormat("jpeg", "JPEG image", "IMAGE", "image/jpeg", ".jpg", "image", ocr=True,
                   extensions=(".jpg", ".jpeg")),
    DocumentFormat("tiff", "TIFF image", "IMAGE", "image/tiff", ".tiff", "page", ocr=True,
                   extensions=(".tif", ".tiff")),
)}

#: Every extension the file dialogs offer.
SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(sorted({e for f in FORMATS.values() for e in f.extensions}))
#: Every stored-copy suffix (the backup format accepts exactly these for documents).
STORED_SUFFIXES: tuple[str, ...] = tuple(sorted({f.suffix for f in FORMATS.values()} | {".htm"}))

#: Formats recognised and deliberately not read, with the reason given to the user.
REFUSED = {
    "doc": "Legacy Word 97-2003 (.doc) files are not supported. Save the file as .docx (Word: File > Save As) "
           "and import that.",
    "xls": "Legacy Excel 97-2003 (.xls) files are not supported. Save the file as .xlsx and import that.",
    "ppt": "Legacy PowerPoint 97-2003 (.ppt) files are not supported. Save the file as .pptx and import that.",
    "json": "JSON files hold data rather than prose, and RUDRA does not read data structures as statements.",
    "xml": "Generic XML files are not read: their meaning depends on a schema RUDRA does not know.",
}


def detect_format(path: Path) -> DocumentFormat:
    """What `path` is, from its bytes (and, for plain text, its extension)."""
    path = Path(path)
    try:
        with path.open("rb") as handle:
            head = handle.read(4096)
    except OSError as exc:
        raise UnsupportedFormat(f"The file could not be read: {exc}") from exc
    extension = path.suffix.lower()
    if head.startswith(b"%PDF-"):
        return FORMATS["pdf"]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return FORMATS["png"]
    if head.startswith(b"\xff\xd8\xff"):
        return FORMATS["jpeg"]
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return FORMATS["tiff"]
    if head.startswith(b"{\\rtf"):
        return FORMATS["rtf"]
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        kind = {".xls": "xls", ".ppt": "ppt"}.get(extension, "doc")
        raise UnsupportedFormat(REFUSED[kind])
    if head.startswith(b"PK\x03\x04"):
        return _zip_format(path)
    if extension in (".json",):
        raise UnsupportedFormat(REFUSED["json"])
    if extension in (".xml",):
        raise UnsupportedFormat(REFUSED["xml"])
    if b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise UnsupportedFormat("This is a binary file of a kind RUDRA does not read. " + supported_summary())
    for key in ("html", "markdown", "csv", "txt"):
        if extension in FORMATS[key].extensions:
            return FORMATS[key]
    sniff = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if sniff.startswith((b"<!doctype html", b"<html")):
        return FORMATS["html"]
    raise UnsupportedFormat(f"RUDRA does not read {extension or 'files without an extension'}. "
                            + supported_summary())


def _zip_format(path: Path) -> DocumentFormat:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "mimetype" in names and archive.read("mimetype")[:40].strip() == b"application/epub+zip":
                return FORMATS["epub"]
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        raise UnsupportedFormat(f"The file looks like a ZIP container but cannot be opened: {exc}") from exc
    if "word/document.xml" in names:
        return FORMATS["docx"]
    if "ppt/presentation.xml" in names:
        return FORMATS["pptx"]
    if "xl/workbook.xml" in names:
        return FORMATS["xlsx"]
    raise UnsupportedFormat("This ZIP file is not a Word, PowerPoint, Excel or EPUB document. "
                            + supported_summary())


def supported_summary() -> str:
    labels = ", ".join(sorted({f.label for f in FORMATS.values()}))
    return f"Supported: {labels}."


def decode_text(data: bytes) -> tuple[str, str]:
    """Text and the encoding it was read with: a byte-order mark, else UTF-8, else Windows-1252."""
    for bom, encoding in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            return data.decode(encoding), encoding
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


# ------------------------------------------------------------------ blocks and units


@dataclass
class Block:
    """One piece of a document's structure.

    kind: heading, paragraph, item (list item), table, caption, equation, code, note
    """

    kind: str
    text: str = ""
    level: int = 0
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Unit:
    """One located piece of text: what becomes one stored segment."""

    number: int
    label: str
    text: str
    headings: list[tuple[int, str]] = field(default_factory=list)


def render(blocks: list[Block]) -> tuple[str, list[tuple[int, str]]]:
    """The text of `blocks`, laid out for extraction, and the headings it holds."""
    lines: list[str] = []
    headings: list[tuple[int, str]] = []
    for block in blocks:
        text = " ".join(block.text.split())
        if block.kind == "heading" and text:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(text)
            headings.append((max(1, block.level), text))
        elif block.kind == "item" and text:
            lines.append(("  " * max(0, block.level)) + "- " + text)
        elif block.kind == "table":
            lines.extend(_table_lines(block.rows))
            lines.append("")
        elif block.kind == "equation" and text:
            lines.append(text)
        elif block.kind == "code":
            lines.extend(line.rstrip() for line in block.text.splitlines())
            lines.append("")
        elif text:
            lines.append(text)
            if block.kind in ("paragraph", "caption", "note"):
                lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines), headings


def _table_lines(rows: list[list[str]]) -> list[str]:
    """Rows as text. A two-column table reads as 'Term: description' - how glossaries are laid out."""
    cleaned = [[" ".join(cell.split()) for cell in row] for row in rows if any(cell.strip() for cell in row)]
    if not cleaned:
        return []
    width = max(len(row) for row in cleaned)
    header: list[str] = []
    if len(cleaned) > 1 and all(len(cell.split()) <= 3 for cell in cleaned[0]):
        header = [" | ".join(cleaned[0])]  # a header row names columns; it is not a statement
        cleaned = cleaned[1:]
    if width == 2:
        return header + [f"{row[0]}: {row[1]}" if len(row) == 2 and row[0] and row[1] else " ".join(row)
                         for row in cleaned]
    return header + [" | ".join(row) for row in cleaned]


def units(blocks: list[Block], unit_word: str = "Section", *, split_level: int = 2,
          limit: int = UNIT_CHARACTERS) -> list[Unit]:
    """Group blocks into located units: a new unit at each heading of `split_level` or above,
    or when a unit grows past `limit` characters (at a block boundary)."""
    groups: list[list[Block]] = []
    current: list[Block] = []
    size = 0
    for block in blocks:
        starts = block.kind == "heading" and block.level <= split_level and current
        if starts or (size >= limit and current):
            groups.append(current)
            current, size = [], 0
        current.append(block)
        size += len(block.text) + sum(len(" ".join(row)) for row in block.rows)
    if current:
        groups.append(current)
    out = []
    for number, group in enumerate(groups, start=1):
        text, headings = render(group)
        if not text.strip():
            continue
        title = next((b.text for b in group if b.kind == "heading" and b.text.strip()), "")
        label = f"{unit_word} {len(out) + 1}" + (f": {' '.join(title.split())[:80]}" if title else "")
        out.append(Unit(len(out) + 1, label, text, headings))
    return out
