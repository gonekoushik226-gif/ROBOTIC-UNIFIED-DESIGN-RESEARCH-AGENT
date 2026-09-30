"""Readers for every supported format, behind the one parser port (`ports.PdfParser`).

Each reader offers `name`, `open(path)` (unit count and metadata) and `pages(path)`
(located text units, one at a time), which is all the ingestion pipeline asks of a
parser. `DocumentReader` picks the reader for a file by its detected format.

Everything here uses the standard library - `zipfile`, `xml.etree`, `html.parser`,
`csv` - except PDF text, which stays with the pypdf adapter, and OCR, which uses
Windows' own engine (`app/documents/ocr.py`). Office and EPUB files are ZIP containers of
XML: they are opened read-only, with limits on size and member count, and XML that
declares a DTD or entities is refused rather than expanded.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from app.documents import mathmarkup, ocr
from app.documents.formats import (
    FORMATS,
    MAX_TEXT_BYTES,
    UNIT_CHARACTERS,
    Block,
    DocumentFormat,
    Unit,
    UnsupportedFormat,
    decode_text,
    detect_format,
    render,
    units,
)
from app.documents.ports import ParsedDocument, ParsedPage, PdfMetadata
from app.documents.text_quality import assess

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
MAX_XML_BYTES = 64 * 1024 * 1024
CSV_ROWS_PER_UNIT = 200
#: A page with a little native text is replaced by OCR only when OCR reads at least
#: this many more characters (and at least twice as many).
MIN_OCR_GAIN = 40
#: PDF pages are sent to OCR in batches of this many.
OCR_BATCH = 20

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": mathmarkup.OMML,
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "dc": "http://purl.org/dc/elements/1.1/",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "opf": "http://www.idpf.org/2007/opf",
    "cn": "urn:oasis:names:tc:opendocument:xmlns:container",
}


class DocumentReadError(ValueError):
    """The file has the right format but its content cannot be read safely."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ------------------------------------------------------------------ safe containers


class _Package:
    """A ZIP container opened read-only, with limits."""

    def __init__(self, path: Path):
        try:
            self.archive = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise DocumentReadError(f"The file cannot be opened: {exc}") from exc
        infos = self.archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise DocumentReadError("The file holds more parts than RUDRA accepts.")
        if sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
            raise DocumentReadError("The file expands to more than RUDRA accepts (512 MB).")
        self.names = {info.filename for info in infos}

    def close(self) -> None:
        self.archive.close()

    def read(self, name: str) -> bytes | None:
        if name not in self.names:
            return None
        info = self.archive.getinfo(name)
        if info.file_size > MAX_XML_BYTES:
            raise DocumentReadError(f"{name} is larger than RUDRA reads.")
        return self.archive.read(name)

    def xml(self, name: str) -> ET.Element | None:
        data = self.read(name)
        if data is None:
            return None
        return parse_xml(data, name)

    def relationships(self, part: str) -> dict[str, str]:
        """Relationship id -> target path (resolved against the part's folder) or external URL."""
        folder = PurePosixPath(part).parent
        rels = self.xml(str(folder / "_rels" / (PurePosixPath(part).name + ".rels")))
        found: dict[str, str] = {}
        if rels is None:
            return found
        for rel in rels:
            target = rel.get("Target", "")
            if rel.get("TargetMode") == "External":
                found[rel.get("Id", "")] = target
            else:
                found[rel.get("Id", "")] = _join(folder, target)
        return found


def _join(folder: PurePosixPath, target: str) -> str:
    parts: list[str] = [] if target.startswith("/") else list(folder.parts)
    for piece in target.lstrip("/").split("/"):
        if piece == "..":
            if parts:
                parts.pop()
        elif piece and piece != ".":
            parts.append(piece)
    return "/".join(parts)


def parse_xml(data: bytes, name: str = "part") -> ET.Element:
    """XML parsed without a DTD: a document declaring one is refused, never expanded."""
    head = data[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in data.upper():
        raise DocumentReadError(f"{name} declares a DTD or entities, which RUDRA does not process.")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise DocumentReadError(f"{name} is not well-formed XML: {exc}") from exc


def _core_metadata(package: _Package, count: int) -> PdfMetadata:
    core = package.xml("docProps/core.xml")
    if core is None:
        return PdfMetadata(page_count=count)

    def text(tag: str) -> str | None:
        element = core.find(tag, NS)
        value = (element.text or "").strip() if element is not None else ""
        return value or None

    created = core.find("{http://purl.org/dc/terms/}created")
    return PdfMetadata(title=text("dc:title"), author=text("dc:creator"), language=text("dc:language"),
                       publication_date=(created.text or "").strip()[:10] or None if created is not None else None,
                       page_count=count)


# ------------------------------------------------------------------ base reader


class _StructuredReader:
    """Readers whose documents are read whole into blocks and laid out as units."""

    format_key = ""
    unit_word = "Section"

    @property
    def name(self) -> str:
        return self.format_key

    def read(self, path: Path) -> tuple[list[Unit], PdfMetadata]:
        raise NotImplementedError

    def open(self, path: Path) -> ParsedDocument:
        located, metadata = self.read(Path(path))
        return ParsedDocument(page_count=len(located), metadata=_with_count(metadata, len(located)))

    def pages(self, path: Path) -> Iterator[ParsedPage]:
        located, _metadata = self.read(Path(path))
        for unit in located:
            yield ParsedPage(page_number=unit.number, text=unit.text, extraction_method=self.name,
                             origin="NATIVE_TEXT", headings=tuple(unit.headings), location=unit.label)


def _with_count(metadata: PdfMetadata, count: int) -> PdfMetadata:
    return PdfMetadata(metadata.title, metadata.author, metadata.publisher, metadata.publication_date,
                       metadata.language, count)


# ------------------------------------------------------------------ Word (.docx)


class DocxReader(_StructuredReader):
    format_key = "docx"

    def read(self, path: Path) -> tuple[list[Unit], PdfMetadata]:
        package = _Package(path)
        try:
            document = package.xml("word/document.xml")
            if document is None:
                raise DocumentReadError("The Word document has no body.")
            styles = self._styles(package)
            links = package.relationships("word/document.xml")
            body = document.find("w:body", NS)
            blocks: list[Block] = []
            self.links: list[tuple[str, str]] = []
            if body is not None:
                for child in body:
                    blocks.extend(self._body_element(child, styles, links))
            for part, title in (("word/footnotes.xml", "Footnotes"), ("word/endnotes.xml", "Endnotes")):
                notes = package.xml(part)
                if notes is None:
                    continue
                texts = []
                for note in notes:
                    if note.get(f"{{{NS['w']}}}type") in ("separator", "continuationSeparator"):
                        continue
                    text = " ".join(self._paragraph_text(p, links) for p in note.iter(f"{{{NS['w']}}}p")).strip()
                    if text:
                        texts.append(text)
                if texts:
                    blocks.append(Block("heading", title, 2))
                    blocks.extend(Block("note", text) for text in texts)
            if self.links:
                blocks.append(Block("note", "Links: " + "; ".join(f"{t} <{u}>" for t, u in self.links)))
            located = units(blocks, "Section")
            return located, _core_metadata(package, len(located))
        finally:
            package.close()

    def _styles(self, package: _Package) -> dict[str, tuple[str, int | None]]:
        styles = package.xml("word/styles.xml")
        found: dict[str, tuple[str, int | None]] = {}
        if styles is None:
            return found
        for style in styles.findall("w:style", NS):
            style_id = style.get(f"{{{NS['w']}}}styleId", "")
            name_el = style.find("w:name", NS)
            name = (name_el.get(f"{{{NS['w']}}}val", "") if name_el is not None else "").lower()
            outline = style.find("w:pPr/w:outlineLvl", NS)
            level = int(outline.get(f"{{{NS['w']}}}val", "9")) + 1 if outline is not None else None
            match = re.match(r"heading (\d)", name)
            if match:
                level = int(match.group(1))
            if name == "title":
                level = 1
            found[style_id] = (name, level if level is not None and level <= 6 else None)
        return found

    def _body_element(self, element: ET.Element, styles, links) -> list[Block]:
        name = _local(element.tag)
        if name == "p":
            return self._paragraph(element, styles, links)
        if name == "tbl":
            rows = []
            for row in element.findall("w:tr", NS):
                rows.append([" ".join(self._paragraph_text(p, links) for p in cell.iter(f"{{{NS['w']}}}p"))
                             for cell in row.findall("w:tc", NS)])
            return [Block("table", rows=rows)]
        if name == "sdt":
            content = element.find("w:sdtContent", NS)
            out: list[Block] = []
            if content is not None:
                for child in content:
                    out.extend(self._body_element(child, styles, links))
            return out
        return []

    def _paragraph(self, paragraph: ET.Element, styles, links) -> list[Block]:
        style = paragraph.find("w:pPr/w:pStyle", NS)
        style_id = style.get(f"{{{NS['w']}}}val", "") if style is not None else ""
        style_name, level = styles.get(style_id, (style_id.lower(), None))
        outline = paragraph.find("w:pPr/w:outlineLvl", NS)
        if outline is not None:
            level = int(outline.get(f"{{{NS['w']}}}val", "9")) + 1
        # A paragraph holding only display equations becomes equation blocks.
        content = [c for c in paragraph if _local(c.tag) not in ("pPr", "bookmarkStart", "bookmarkEnd",
                                                                    "proofErr", "commentRangeStart",
                                                                    "commentRangeEnd")]
        maths = [c for c in content if _local(c.tag) == "oMathPara"]
        if maths and all(_local(c.tag) in ("oMathPara", "r") and (_local(c.tag) == "oMathPara"
                                                                 or not self._run_text(c).strip())
                         for c in content):
            out = []
            for para in maths:
                for math in para.iter(f"{{{NS['m']}}}oMath"):
                    text = mathmarkup.omml_to_linear(math)
                    if text:
                        out.append(Block("equation", text))
            return out
        text = self._paragraph_text(paragraph, links)
        if not text.strip():
            return []
        if level is not None and level <= 6 and len(text) <= 200:
            return [Block("heading", text, level)]
        if style_name == "caption":
            return [Block("caption", text)]
        numbering = paragraph.find("w:pPr/w:numPr", NS)
        if numbering is not None or style_name.startswith("list"):
            depth = paragraph.find("w:pPr/w:numPr/w:ilvl", NS)
            return [Block("item", text, int(depth.get(f"{{{NS['w']}}}val", "0")) if depth is not None else 0)]
        return [Block("paragraph", text)]

    def _run_text(self, run: ET.Element) -> str:
        out = []
        for child in run:
            name = _local(child.tag)
            if name == "t":
                out.append(child.text or "")
            elif name in ("tab", "br", "cr"):
                out.append(" ")
            elif name == "noBreakHyphen":
                out.append("-")
            elif name == "sym":
                char = child.get(f"{{{NS['w']}}}char")
                if char:
                    try:
                        out.append(chr(int(char, 16)))
                    except ValueError:
                        out.append("")
        return "".join(out)

    def _paragraph_text(self, element: ET.Element, links) -> str:
        out: list[str] = []
        for child in element:
            name = _local(child.tag)
            if name == "r":
                out.append(self._run_text(child))
            elif name == "oMath":
                out.append(" " + mathmarkup.omml_to_linear(child) + " ")
            elif name == "oMathPara":
                out.extend(" " + mathmarkup.omml_to_linear(m) + " " for m in child.iter(f"{{{NS['m']}}}oMath"))
            elif name == "hyperlink":
                text = self._paragraph_text(child, links)
                target = links.get(child.get(f"{{{NS['r']}}}id", ""), "")
                if target.startswith(("http://", "https://", "mailto:")) and text.strip():
                    self.links.append((text.strip(), target))
                out.append(text)
            elif name in ("ins", "smartTag", "fldSimple", "customXml", "sdt", "sdtContent"):
                out.append(self._paragraph_text(child, links))
            # w:del (deleted revision text), w:instrText (field codes) and drawings are skipped.
        return re.sub(r"[ \t]+", " ", "".join(out)).strip()


# ------------------------------------------------------------------ PowerPoint (.pptx)


class PptxReader(_StructuredReader):
    format_key = "pptx"

    def read(self, path: Path) -> tuple[list[Unit], PdfMetadata]:
        package = _Package(path)
        try:
            presentation = package.xml("ppt/presentation.xml")
            if presentation is None:
                raise DocumentReadError("The presentation has no slide list.")
            rels = package.relationships("ppt/presentation.xml")
            slide_parts = [rels.get(item.get(f"{{{NS['r']}}}id", ""), "")
                           for item in presentation.findall("p:sldIdLst/p:sldId", NS)]
            located: list[Unit] = []
            for number, part in enumerate(slide_parts, start=1):
                slide = package.xml(part) if part else None
                if slide is None:
                    continue
                blocks = self._slide_blocks(slide)
                slide_rels = package.relationships(part)
                notes_part = next((t for t in slide_rels.values() if "notesSlide" in t), None)
                if notes_part:
                    notes = package.xml(notes_part)
                    if notes is not None:
                        text = " ".join(t for t in (self._shape_text(sp)
                                                    for sp in notes.iter(f"{{{NS['p']}}}sp")
                                                    if not self._placeholder(sp, ("sldNum", "sldImg", "hdr",
                                                                                  "ftr", "dt")))
                                        if t).strip()
                        if text:
                            blocks.append(Block("note", f"Speaker notes: {text}"))
                text, headings = render(blocks)
                title = next((b.text for b in blocks if b.kind == "heading"), "")
                if text.strip():
                    located.append(Unit(number, f"Slide {number}" + (f": {title[:80]}" if title else ""),
                                        text, headings))
            return located, _core_metadata(package, len(located))
        finally:
            package.close()

    def _placeholder(self, shape: ET.Element, kinds: tuple[str, ...]) -> bool:
        ph = shape.find("p:nvSpPr/p:nvPr/p:ph", NS)
        return ph is not None and ph.get("type", "body") in kinds

    def _slide_blocks(self, slide: ET.Element) -> list[Block]:
        blocks: list[Block] = []
        tree = slide.find("p:cSld/p:spTree", NS)
        if tree is not None:
            self._shapes(tree, blocks)
        return blocks

    def _shapes(self, tree: ET.Element, blocks: list[Block]) -> None:
        for shape in tree:
            name = _local(shape.tag)
            if name == "grpSp":
                self._shapes(shape, blocks)
            elif name == "sp":
                title = self._placeholder(shape, ("title", "ctrTitle"))
                body = shape.find("p:txBody", NS)
                if body is None:
                    continue
                for paragraph in body.findall("a:p", NS):
                    text = self._paragraph_text(paragraph)
                    if not text:
                        continue
                    if title:
                        blocks.append(Block("heading", text, 1))
                    elif self._only_math(paragraph):
                        blocks.append(Block("equation", text))
                    else:
                        props = paragraph.find("a:pPr", NS)
                        level = int(props.get("lvl", "0")) if props is not None else 0
                        blocks.append(Block("item" if level else "paragraph", text, level))
            elif name == "graphicFrame":
                table = shape.find(".//a:tbl", NS)
                if table is not None:
                    rows = [[" ".join(self._paragraph_text(p) for p in cell.iter(f"{{{NS['a']}}}p"))
                             for cell in row.findall("a:tc", NS)] for row in table.findall("a:tr", NS)]
                    blocks.append(Block("table", rows=rows))

    def _only_math(self, paragraph: ET.Element) -> bool:
        has_math = any(_local(e.tag) == "oMath" for e in paragraph.iter())
        plain = "".join(r.findtext("a:t", "", NS) for r in paragraph.findall("a:r", NS)).strip()
        return has_math and not plain

    def _paragraph_text(self, paragraph: ET.Element) -> str:
        out: list[str] = []
        for child in paragraph:
            name = _local(child.tag)
            if name in ("r", "fld"):
                out.append(child.findtext("a:t", "", NS))
            elif name == "br":
                out.append(" ")
            elif name == "AlternateContent":
                choice = child.find("mc:Choice", NS)
                if choice is not None:
                    for math in choice.iter(f"{{{NS['m']}}}oMath"):
                        out.append(" " + mathmarkup.omml_to_linear(math) + " ")
            elif name == "m":  # a14:m wrapping OMML without AlternateContent
                for math in child.iter(f"{{{NS['m']}}}oMath"):
                    out.append(" " + mathmarkup.omml_to_linear(math) + " ")
        return " ".join("".join(out).split())

    def _shape_text(self, shape: ET.Element) -> str:
        body = shape.find("p:txBody", NS)
        if body is None:
            return ""
        return " ".join(t for t in (self._paragraph_text(p) for p in body.findall("a:p", NS)) if t)


# ------------------------------------------------------------------ Excel (.xlsx)


class XlsxReader(_StructuredReader):
    format_key = "xlsx"

    def read(self, path: Path) -> tuple[list[Unit], PdfMetadata]:
        package = _Package(path)
        try:
            workbook = package.xml("xl/workbook.xml")
            if workbook is None:
                raise DocumentReadError("The workbook has no sheet list.")
            rels = package.relationships("xl/workbook.xml")
            shared = self._shared_strings(package)
            located: list[Unit] = []
            for sheet in workbook.findall("s:sheets/s:sheet", NS):
                name = sheet.get("name", "Sheet")
                part = rels.get(sheet.get(f"{{{NS['r']}}}id", ""), "")
                data = package.xml(part) if part else None
                if data is None:
                    continue
                rows = self._rows(data, shared)
                for start in range(0, len(rows), CSV_ROWS_PER_UNIT):
                    chunk = rows[start:start + CSV_ROWS_PER_UNIT]
                    text, _ = render([Block("heading", f"Sheet {name}", 1), Block("table", rows=chunk)])
                    span = "" if len(rows) <= CSV_ROWS_PER_UNIT else f" (rows {start + 1}-{start + len(chunk)})"
                    located.append(Unit(len(located) + 1, f"Sheet {name}{span}", text, [(1, f"Sheet {name}")]))
            return located, _core_metadata(package, len(located))
        finally:
            package.close()

    def _shared_strings(self, package: _Package) -> list[str]:
        table = package.xml("xl/sharedStrings.xml")
        if table is None:
            return []
        return ["".join(t.text or "" for t in item.iter(f"{{{NS['s']}}}t")) for item in table.findall("s:si", NS)]

    def _rows(self, sheet: ET.Element, shared: list[str]) -> list[list[str]]:
        rows: list[list[str]] = []
        for row in sheet.findall("s:sheetData/s:row", NS):
            cells: dict[int, str] = {}
            for cell in row.findall("s:c", NS):
                column = _column(cell.get("r", ""))
                kind = cell.get("t", "n")
                if kind == "inlineStr":
                    value = "".join(t.text or "" for t in cell.iter(f"{{{NS['s']}}}t"))
                else:
                    raw = cell.findtext("s:v", "", NS)
                    if kind == "s":
                        try:
                            value = shared[int(raw)]
                        except (ValueError, IndexError):
                            value = ""
                    elif kind == "b":
                        value = {"1": "TRUE", "0": "FALSE"}.get(raw, raw)
                    else:
                        value = raw
                cells[column if column is not None else len(cells)] = value
            if cells:
                width = max(cells) + 1
                rows.append([cells.get(i, "") for i in range(width)])
        return rows


def _column(reference: str) -> int | None:
    letters = re.match(r"[A-Z]+", reference or "")
    if not letters:
        return None
    number = 0
    for letter in letters.group():
        number = number * 26 + (ord(letter) - 64)
    return number - 1


# ------------------------------------------------------------------ HTML


_SKIPPED = {"script", "style", "noscript", "template", "svg", "nav", "head", "iframe", "object", "button",
            "select", "form"}
_BLOCK_TAGS = {"p", "div", "section", "article", "main", "blockquote", "pre", "li", "dt", "dd", "h1", "h2", "h3",
               "h4", "h5", "h6", "caption", "figcaption", "figure", "table", "tr", "ul", "ol", "dl", "br", "hr",
               "header", "footer", "aside", "address", "body"}


class _HtmlBlocks(HTMLParser):
    """HTML (or XHTML) into blocks: headings, paragraphs, list items, tables, captions, equations."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self.meta: dict[str, str] = {}
        self._text: list[str] = []
        self._kind = "paragraph"
        self._level = 0
        self._skip = 0
        self._in_title = False
        self._pre = 0
        self._list_depth = 0
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._math: list[mathmarkup.Node] = []
        self._math_display = False
        self._link: tuple[str, list[str]] | None = None
        self._pending_term: str | None = None

    @staticmethod
    def _name(tag: str) -> str:
        return tag.split(":")[-1].lower()

    def _flush(self) -> None:
        text = " ".join("".join(self._text).split()) if not self._pre else "".join(self._text).strip("\n")
        self._text = []
        if not text:
            return
        if self._kind == "dt":
            self._pending_term = text
            return
        if self._kind == "dd" and self._pending_term:
            text = f"{self._pending_term}: {text}"
            self._pending_term = None
            self.blocks.append(Block("paragraph", text))
            return
        kind = {"dd": "paragraph"}.get(self._kind, self._kind)
        self.blocks.append(Block(kind, text, self._level))

    def handle_starttag(self, tag, attrs):
        name = self._name(tag)
        attributes = {k.split(":")[-1].lower(): (v or "") for k, v in attrs}
        if self._math:
            node = mathmarkup.Node(name, attributes)
            self._math[-1].children.append(node)
            self._math.append(node)
            return
        if name == "math":
            node = mathmarkup.Node("math", attributes)
            self._math = [node]
            self._math_display = attributes.get("display") == "block" or not "".join(self._text).strip()
            return
        if name == "title":
            self._in_title = True
            return
        if name == "meta" and attributes.get("name") and attributes.get("content"):
            self.meta[attributes["name"].lower()] = attributes["content"]
            return
        if name in _SKIPPED:
            self._skip += 1
            return
        if self._skip:
            return
        if name == "a" and attributes.get("href", "").startswith(("http://", "https://")):
            self._link = (attributes["href"], [])
        if name == "table":
            self._flush()
            self._table = []
            return
        if self._table is not None:
            if name == "tr":
                self._row = []
            elif name in ("td", "th"):
                self._cell = []
            elif name == "caption":
                self._flush()
                self._kind, self._level = "caption", 0
            return
        if name in ("ul", "ol"):
            self._list_depth += 1
        if name in _BLOCK_TAGS:
            self._flush()
            if re.fullmatch(r"h[1-6]", name):
                self._kind, self._level = "heading", int(name[1])
            elif name == "li":
                self._kind, self._level = "item", max(0, self._list_depth - 1)
            elif name in ("caption", "figcaption"):
                self._kind, self._level = "caption", 0
            elif name in ("dt", "dd"):
                self._kind, self._level = name, 0
            elif name == "pre":
                self._kind, self._level = "code", 0
                self._pre += 1
            else:
                self._kind, self._level = "paragraph", 0

    def handle_endtag(self, tag):
        name = self._name(tag)
        if name == "title":
            self._in_title = False
            return
        if name in _SKIPPED:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if name == "a" and self._link is not None:
            text = " ".join("".join(self._link[1]).split())
            if text:
                self.links.append((text, self._link[0]))
            self._link = None
        if self._table is not None:
            if name in ("td", "th") and self._cell is not None and self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
                self._cell = None
            elif name == "tr" and self._row is not None:
                self._table.append(self._row)
                self._row = None
            elif name == "caption":
                self._flush()
                self._kind = "paragraph"
            elif name == "table":
                self.blocks.append(Block("table", rows=self._table))
                self._table = None
            return
        if name in ("ul", "ol"):
            self._list_depth = max(0, self._list_depth - 1)
        if name == "pre":
            self._pre = max(0, self._pre - 1)
        if name in _BLOCK_TAGS:
            self._flush()
            self._kind, self._level = "paragraph", 0

    def handle_startendtag(self, tag, attrs):
        name = self._name(tag)
        if self._math:
            self._math[-1].children.append(mathmarkup.Node(name, {k: v or "" for k, v in attrs}))
            return
        if name == "br":
            self._append("\n" if self._pre else " ")
        elif name == "meta":
            self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self._math:
            self._math[-1].children.append(data)
            return
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self._append(data)

    def _append(self, data: str) -> None:
        if self._link is not None:
            self._link[1].append(data)
        if self._cell is not None:
            self._cell.append(data)
        elif self._table is None or self._kind == "caption":
            self._text.append(data)

    def close(self):
        super().close()
        self._flush()


class _HtmlBlocksWithMath(_HtmlBlocks):
    """Completes MathML handling: a closed <math> becomes an equation or inline notation."""

    def handle_endtag(self, tag):
        name = self._name(tag)
        if self._math:
            node = self._math.pop()
            if self._math:
                return
            linear = mathmarkup.mathml_to_linear(node)
            if not linear:
                return
            if self._math_display and self._table is None:
                self._flush()
                self.blocks.append(Block("equation", linear))
            else:
                self._append(" " + linear + " ")
            return
        super().handle_endtag(tag)


def html_blocks(text: str) -> tuple[list[Block], _HtmlBlocks]:
    parser = _HtmlBlocksWithMath()
    parser.feed(text)
    parser.close()
    if parser.links:
        parser.blocks.append(Block("note", "Links: " + "; ".join(f"{t} <{u}>" for t, u in parser.links[:200])))
    return parser.blocks, parser


class HtmlReader(_StructuredReader):
    format_key = "html"

    def read(self, path: Path) -> tuple[list[Unit], PdfMetadata]:
        text = _read_text_file(path)
        blocks, parser = html_blocks(text)
        located = units(blocks, "Section")
        title = " ".join(parser.title.split()) or None
        return located, PdfMetadata(title=title, author=parser.meta.get("author"), page_count=len(located))


# ------------------------------------------------------------------ EPUB


class EpubReader(_StructuredReader):
    format_key = "epub"

    def read(self, path: Path) -> tuple[list[Unit], PdfMetadata]:
        package = _Package(path)
        try:
            container = package.xml("META-INF/container.xml")
            rootfile = container.find(".//cn:rootfile", NS) if container is not None else None
            if rootfile is None:
                raise DocumentReadError("The EPUB has no package document.")
            opf_path = rootfile.get("full-path", "")
            opf = package.xml(opf_path)
            if opf is None:
                raise DocumentReadError("The EPUB package document is missing.")
            folder = PurePosixPath(opf_path).parent
            manifest = {item.get("id"): (_join(folder, item.get("href", "")), item.get("media-type", ""))
                        for item in opf.findall("opf:manifest/opf:item", NS)}
            blocks: list[Block] = []
            for itemref in opf.findall("opf:spine/opf:itemref", NS):
                href, media = manifest.get(itemref.get("idref"), ("", ""))
                if not href or "html" not in media:
                    continue
                data = package.read(href)
                if data is None:
                    continue
                chapter, _parser = html_blocks(decode_text(data)[0])
                if chapter and chapter[0].kind != "heading":
                    blocks.append(Block("heading", PurePosixPath(href).stem.replace("_", " "), 2))
                blocks.extend(chapter)
            located = units(blocks, "Section")

            def meta(tag: str) -> str | None:
                element = opf.find(f"opf:metadata/dc:{tag}", NS)
                value = (element.text or "").strip() if element is not None else ""
                return value or None

            return located, PdfMetadata(title=meta("title"), author=meta("creator"), language=meta("language"),
                                        publisher=meta("publisher"), page_count=len(located))
        finally:
            package.close()


# ------------------------------------------------------------------ Markdown, text, CSV


def _read_text_file(path: Path) -> str:
    size = Path(path).stat().st_size
    if size > MAX_TEXT_BYTES:
        raise DocumentReadError("The text file is larger than RUDRA reads (64 MB).")
    return decode_text(Path(path).read_bytes())[0]


class MarkdownReader(_StructuredReader):
    format_key = "markdown"

    def read(self, path: Path) -> tuple[list[Unit], PdfMetadata]:
        blocks = markdown_blocks(_read_text_file(path))
        located = units(blocks, "Section")
        title = next((b.text for b in blocks if b.kind == "heading" and b.level == 1), None)
        return located, PdfMetadata(title=title, page_count=len(located))


def _inline_markdown(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)", r"\1", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(?<![\w*])([*_])(?!\s)(.+?)(?<!\s)\1(?![\w*])", r"\2", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def markdown_blocks(text: str) -> list[Block]:
    blocks: list[Block] = []
    lines = text.splitlines()
    paragraph: list[str] = []
    index = 0

    def flush() -> None:
        if paragraph:
            blocks.append(Block("paragraph", _inline_markdown(" ".join(paragraph))))
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush()
            fence = stripped[:3]
            code = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith(fence):
                code.append(lines[index])
                index += 1
            blocks.append(Block("code", "\n".join(code)))
            index += 1
            continue
        if stripped.startswith("$$"):
            flush()
            body = stripped[2:]
            if body.endswith("$$") and len(body) >= 2:
                blocks.append(Block("equation", body[:-2].strip()))
                index += 1
                continue
            parts = [body]
            index += 1
            while index < len(lines) and "$$" not in lines[index]:
                parts.append(lines[index].strip())
                index += 1
            if index < len(lines):
                parts.append(lines[index].split("$$")[0].strip())
            blocks.append(Block("equation", " ".join(p for p in parts if p)))
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", stripped)
        if heading:
            flush()
            blocks.append(Block("heading", _inline_markdown(heading.group(2)), len(heading.group(1))))
            index += 1
            continue
        if (index + 1 < len(lines) and stripped and re.fullmatch(r"=+|-+", lines[index + 1].strip())
                and not paragraph):
            blocks.append(Block("heading", _inline_markdown(stripped),
                                1 if lines[index + 1].strip().startswith("=") else 2))
            index += 2
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and re.fullmatch(
                r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", lines[index + 1].strip()):
            flush()
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                row = lines[index].strip().strip("|")
                if not re.fullmatch(r"\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*", row):
                    rows.append([_inline_markdown(cell.strip()) for cell in row.split("|")])
                index += 1
            blocks.append(Block("table", rows=rows))
            continue
        item = re.match(r"^(\s*)(?:[-*+]|\d{1,3}[.)])\s+(.*)$", line)
        if item:
            flush()
            blocks.append(Block("item", _inline_markdown(item.group(2)), len(item.group(1)) // 2))
            index += 1
            continue
        if not stripped:
            flush()
            index += 1
            continue
        paragraph.append(stripped.lstrip("> ").strip())
        index += 1
    flush()
    return blocks


class TextReader(_StructuredReader):
    format_key = "txt"

    def read(self, path: Path) -> tuple[list[Unit], PdfMetadata]:
        text = _read_text_file(path)
        blocks = [Block("text", chunk.strip("\n")) for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
        located = []
        # Plain text keeps its own lines: a unit is consecutive paragraphs up to the size limit.
        current: list[str] = []
        size = 0
        for block in blocks:
            if size >= UNIT_CHARACTERS and current:
                located.append("\n\n".join(current))
                current, size = [], 0
            current.append(block.text)
            size += len(block.text)
        if current:
            located.append("\n\n".join(current))
        out = [Unit(n, f"Part {n}", text) for n, text in enumerate(located, start=1)]
        return out, PdfMetadata(page_count=len(out))


class CsvReader(_StructuredReader):
    format_key = "csv"

    def read(self, path: Path) -> tuple[list[Unit], PdfMetadata]:
        text = _read_text_file(path)
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [row for row in csv.reader(io.StringIO(text), dialect) if any(cell.strip() for cell in row)]
        located: list[Unit] = []
        for start in range(0, len(rows), CSV_ROWS_PER_UNIT):
            chunk = rows[start:start + CSV_ROWS_PER_UNIT]
            body, _ = render([Block("table", rows=chunk)])
            located.append(Unit(len(located) + 1, f"Rows {start + 1}-{start + len(chunk)}", body))
        return located, PdfMetadata(page_count=len(located))


# ------------------------------------------------------------------ RTF


_RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "footer", "headerl",
             "headerr", "footerl", "footerr", "headerf", "footerf", "listtable", "listoverridetable",
             "revtbl", "rsidtbl", "generator", "themedata", "colorschememapping", "datastore", "latentstyles",
             "fldinst", "xmlnstbl", "mmathPr", "pgdsctbl", "filetbl", "bkmkstart", "bkmkend"}
_RTF_TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|(\r\n|\r|\n)|([^\\{}\r\n]+)")


def rtf_text(data: bytes) -> str:
    """The visible text of an RTF document: paragraphs kept, control groups skipped."""
    encoding = "cp1252"
    match = re.search(rb"\\ansicpg(\d+)", data[:4096])
    if match:
        encoding = f"cp{int(match.group(1))}"
    out: list[str] = []
    stack: list[tuple[bool, int]] = []
    skip = False
    uc = 1
    pending_skip = 0
    for word, number, hex_code, symbol, brace, newline, text in _RTF_TOKEN.findall(data):
        if brace == b"{":
            stack.append((skip, uc))
            continue
        if brace == b"}":
            skip, uc = stack.pop() if stack else (False, 1)
            continue
        if newline:
            continue
        if symbol:
            if symbol == b"*":
                skip = True
            elif not skip and symbol in (b"\\", b"{", b"}"):
                out.append(symbol.decode())
            elif not skip and symbol == b"~":
                out.append(" ")
            elif not skip and symbol in (b"-", b"_"):
                out.append("-" if symbol == b"_" else "")
            continue
        if hex_code:
            if pending_skip:
                pending_skip -= 1
                continue
            if not skip:
                try:
                    out.append(bytes([int(hex_code, 16)]).decode(encoding, errors="replace"))
                except LookupError:
                    out.append(bytes([int(hex_code, 16)]).decode("cp1252", errors="replace"))
            continue
        if word:
            name = word.decode()
            if name in _RTF_SKIP:
                skip = True
            elif skip:
                continue
            elif name in ("par", "line", "row", "sect", "page"):
                out.append("\n")
            elif name == "cell":
                out.append(" | ")
            elif name == "tab":
                out.append(" ")
            elif name == "uc" and number:
                uc = int(number)
            elif name == "u" and number:
                value = int(number)
                out.append(chr(value + 65536 if value < 0 else value))
                pending_skip = uc
            elif name in ("emdash", "endash"):
                out.append("—" if name == "emdash" else "–")
            elif name in ("lquote", "rquote"):
                out.append("'")
            elif name in ("ldblquote", "rdblquote"):
                out.append('"')
            elif name == "bullet":
                out.append("•")
            continue
        if text and not skip:
            chunk = text.decode(encoding, errors="replace")
            if pending_skip:
                drop = min(pending_skip, len(chunk))
                chunk = chunk[drop:]
                pending_skip -= drop
            out.append(chunk)
    joined = "".join(out)
    return "\n".join(line.strip() for line in joined.splitlines())


class RtfReader(_StructuredReader):
    format_key = "rtf"

    def read(self, path: Path) -> tuple[list[Unit], PdfMetadata]:
        data = Path(path).read_bytes()
        if len(data) > MAX_TEXT_BYTES:
            raise DocumentReadError("The RTF file is larger than RUDRA reads (64 MB).")
        text = rtf_text(data)
        blocks = [Block("paragraph", line) for line in text.splitlines() if line.strip()]
        located = units(blocks, "Section")
        return located, PdfMetadata(page_count=len(located))


# ------------------------------------------------------------------ images (OCR)


class ImageReader:
    """PNG, JPEG and TIFF: text by OCR, one unit per image frame (a TIFF may hold several pages)."""

    name = "windows-ocr"

    def __init__(self):
        self.notes: list[str] = []
        self._cache: dict[str, tuple[ocr.OcrPage, ...]] = {}

    def _recognize(self, path: Path) -> tuple[ocr.OcrPage, ...]:
        key = str(Path(path).resolve())
        if key not in self._cache:
            state = ocr.status()
            if not state.available:
                raise DocumentReadError(f"This image needs OCR, which is not available: {state.reason}")
            try:
                self._cache[key] = ocr.recognize_image(Path(path))
            except ocr.OcrUnavailable as exc:
                raise DocumentReadError(f"The image could not be recognized: {exc}") from exc
        return self._cache[key]

    def open(self, path: Path) -> ParsedDocument:
        pages = self._recognize(Path(path))
        return ParsedDocument(page_count=len(pages), metadata=PdfMetadata(page_count=len(pages)))

    def pages(self, path: Path) -> Iterator[ParsedPage]:
        frames = self._recognize(Path(path))
        for frame in frames:
            label = "Image" if len(frames) == 1 else f"Page {frame.number}"
            yield ParsedPage(page_number=frame.number, text=frame.text, extraction_method=self.name, origin="OCR",
                             confidence=None, location=label, layout=frame.to_json())


# ------------------------------------------------------------------ PDF, with OCR for image-only pages


class OcrPdfReader:
    """Native PDF text first; pages without usable text are recognized by OCR when it is available."""

    def __init__(self, native, *, use_ocr: bool = True):
        self.native = native
        self.use_ocr = use_ocr
        self.notes: list[str] = []

    @property
    def name(self) -> str:
        return self.native.name

    def open(self, path: Path) -> ParsedDocument:
        return self.native.open(path)

    def pages(self, path: Path) -> Iterator[ParsedPage]:
        native = list(self.native.pages(path))
        needing = [p.page_number for p in native if not p.failed and assess(p.page_number, p.text).needs_ocr]
        recognized: dict[int, ocr.OcrPage] = {}
        if needing and self.use_ocr:
            state = ocr.status()
            if not state.available:
                self.notes.append(f"{len(needing)} page(s) have no usable text and OCR is not available: "
                                  f"{state.reason}")
            else:
                for start in range(0, len(needing), OCR_BATCH):
                    batch = needing[start:start + OCR_BATCH]
                    try:
                        for page in ocr.recognize_pdf_pages(Path(path), batch):
                            recognized[page.number] = page
                    except ocr.OcrUnavailable as exc:
                        self.notes.append(f"OCR failed for pages {batch[0]}-{batch[-1]}: {exc}")
                if recognized:
                    self.notes.append(f"{len(recognized)} page(s) were read by OCR; their text is marked OCR "
                                      "and treated as uncertain.")
        kept_native = 0
        for page in native:
            found = recognized.get(page.page_number)
            own = len(page.text.strip())
            # Native text is the page's own: OCR replaces it only when it recovers
            # substantially more (a scanned page with a stray line of text, not a short
            # title page that OCR would merely re-read as uncertain).
            if found is not None and own and len(found.text.strip()) < max(2 * own, own + MIN_OCR_GAIN):
                kept_native += 1
                found = None
            if found is not None and found.text.strip():
                yield ParsedPage(page_number=page.page_number, text=found.text, extraction_method="windows-ocr",
                                 origin="OCR", confidence=None, location=f"Page {page.page_number}",
                                 layout=found.to_json())
            else:
                yield page
        if kept_native:
            self.notes.append(f"{kept_native} short page(s) kept their own text: OCR found nothing more on them.")


# ------------------------------------------------------------------ choosing a reader


def reader_for(fmt: DocumentFormat, pdf_parser=None, *, use_ocr: bool = True):
    if fmt.key == "pdf":
        if pdf_parser is None:
            from app.documents.pypdf_adapter import PypdfParser

            pdf_parser = PypdfParser()
        return OcrPdfReader(pdf_parser, use_ocr=use_ocr)
    readers = {"docx": DocxReader, "pptx": PptxReader, "xlsx": XlsxReader, "epub": EpubReader,
               "html": HtmlReader, "markdown": MarkdownReader, "txt": TextReader, "csv": CsvReader,
               "rtf": RtfReader}
    if fmt.key in readers:
        return readers[fmt.key]()
    if fmt.ocr:
        return ImageReader()
    raise UnsupportedFormat(f"No reader for {fmt.label}.")


class DocumentReader:
    """The parser for any supported format: detects each file and delegates to its reader."""

    multi_format = True

    def __init__(self, pdf_parser=None, *, use_ocr: bool = True):
        self._pdf_parser = pdf_parser
        self._use_ocr = use_ocr
        self._readers: dict[str, object] = {}
        self.notes: list[str] = []

    @property
    def name(self) -> str:
        return "document-reader"

    def _reader(self, path: Path):
        fmt = detect_format(Path(path))
        if fmt.key not in self._readers:
            self._readers[fmt.key] = reader_for(fmt, self._pdf_parser, use_ocr=self._use_ocr)
        return self._readers[fmt.key]

    def open(self, path: Path) -> ParsedDocument:
        try:
            return self._reader(path).open(Path(path))
        except DocumentReadError:
            raise
        except (ET.ParseError, zipfile.BadZipFile, KeyError, IndexError, UnicodeError) as exc:
            raise DocumentReadError(f"The document could not be read: {type(exc).__name__}: {exc}") from exc

    def pages(self, path: Path) -> Iterator[ParsedPage]:
        reader = self._reader(path)
        try:
            yield from reader.pages(Path(path))
        finally:
            notes = getattr(reader, "notes", None)
            if notes:
                self.notes.extend(n for n in notes if n not in self.notes)


__all__ = [
    "CsvReader", "DocumentReadError", "DocumentReader", "DocxReader", "EpubReader", "HtmlReader", "ImageReader",
    "MarkdownReader", "OcrPdfReader", "PptxReader", "RtfReader", "TextReader", "XlsxReader", "html_blocks",
    "markdown_blocks", "parse_xml", "reader_for", "rtf_text", "FORMATS",
]
