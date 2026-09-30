"""Generated documents in every supported format, written with the standard library.

Each builder writes the parts a real file of that format holds (content types, package
relationships, the main part, styles or metadata), with the same XML namespaces Office and
EPUB readers use. The texts are written by hand for the tests.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

CORE = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">'
        '<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator></cp:coreProperties>')


def _zip(path: Path, parts: dict[str, str | bytes]) -> Path:
    path = Path(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return path


# ------------------------------------------------------------------ Word


def w_paragraph(text: str, style: str | None = None, *, numbered: bool = False) -> str:
    props = ""
    if style or numbered:
        props = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + (
            '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if numbered else "") + "</w:pPr>"
    return f"<w:p>{props}<w:r><w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r></w:p>"


def w_equation(omml: str) -> str:
    """A display equation: a paragraph holding one m:oMathPara."""
    return f"<w:p><m:oMathPara><m:oMath>{omml}</m:oMath></m:oMathPara></w:p>"


def w_table(rows: list[list[str]]) -> str:
    cells = "".join("<w:tr>" + "".join(f"<w:tc>{w_paragraph(c)}</w:tc>" for c in row) + "</w:tr>" for row in rows)
    return f"<w:tbl>{cells}</w:tbl>"


def m_run(text: str) -> str:
    return f"<m:r><m:t>{escape(text)}</m:t></m:r>"


def m_frac(num: str, den: str) -> str:
    return f"<m:f><m:num>{num}</m:num><m:den>{den}</m:den></m:f>"


def m_sup(base: str, sup: str) -> str:
    return f"<m:sSup><m:e>{base}</m:e><m:sup>{sup}</m:sup></m:sSup>"


def make_docx(path: Path, body: list[str], *, title: str = "Circuits", author: str = "A. Author") -> Path:
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document xmlns:w="{W}" xmlns:m="{M}" xmlns:r="{R}"><w:body>{"".join(body)}</w:body></w:document>')
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{W}">'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/></w:style>'
              '</w:styles>')
    return _zip(path, {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                               '<Default Extension="xml" ContentType="application/xml"/></Types>',
        "_rels/.rels": f'<?xml version="1.0"?><Relationships xmlns="{PKG}"><Relationship Id="rId1" '
                       f'Type="{DOC_REL}/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": document,
        "word/styles.xml": styles,
        "docProps/core.xml": CORE.format(title=escape(title), author=escape(author)),
    })


# ------------------------------------------------------------------ PowerPoint


def make_pptx(path: Path, slides: list[tuple[str, list[str]]], *, notes: dict[int, str] | None = None) -> Path:
    parts: dict[str, str] = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
    }
    ids = "".join(f'<p:sldId id="{256 + n}" r:id="rId{n}"/>' for n in range(1, len(slides) + 1))
    parts["ppt/presentation.xml"] = (f'<?xml version="1.0"?><p:presentation xmlns:p="{P}" xmlns:r="{R}">'
                                     f"<p:sldIdLst>{ids}</p:sldIdLst></p:presentation>")
    rels = "".join(f'<Relationship Id="rId{n}" Type="{DOC_REL}/slide" Target="slides/slide{n}.xml"/>'
                   for n in range(1, len(slides) + 1))
    parts["ppt/_rels/presentation.xml.rels"] = f'<?xml version="1.0"?><Relationships xmlns="{PKG}">{rels}</Relationships>'
    for n, (title, lines) in enumerate(slides, start=1):
        body = "".join(f"<a:p><a:r><a:t>{escape(line)}</a:t></a:r></a:p>" for line in lines)
        parts[f"ppt/slides/slide{n}.xml"] = (
            f'<?xml version="1.0"?><p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree>'
            f'<p:sp><p:nvSpPr><p:cNvPr id="1" name="Title"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
            f"<p:txBody><a:p><a:r><a:t>{escape(title)}</a:t></a:r></a:p></p:txBody></p:sp>"
            f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="Body"/><p:cNvSpPr/><p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr>'
            f"<p:txBody>{body}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>")
        if notes and n in notes:
            parts[f"ppt/slides/_rels/slide{n}.xml.rels"] = (
                f'<?xml version="1.0"?><Relationships xmlns="{PKG}"><Relationship Id="rId9" '
                f'Type="{DOC_REL}/notesSlide" Target="../notesSlides/notesSlide{n}.xml"/></Relationships>')
            parts[f"ppt/notesSlides/notesSlide{n}.xml"] = (
                f'<?xml version="1.0"?><p:notes xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree><p:sp><p:nvSpPr>'
                f'<p:cNvPr id="2" name="Notes"/><p:cNvSpPr/><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>'
                f"<p:txBody><a:p><a:r><a:t>{escape(notes[n])}</a:t></a:r></a:p></p:txBody></p:sp>"
                f"</p:spTree></p:cSld></p:notes>")
    return _zip(path, parts)


# ------------------------------------------------------------------ Excel


def make_xlsx(path: Path, sheets: dict[str, list[list[str]]]) -> Path:
    strings: list[str] = []

    def index(text: str) -> int:
        if text not in strings:
            strings.append(text)
        return strings.index(text)

    parts: dict[str, str] = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
    }
    entries = []
    rels = []
    for n, (name, rows) in enumerate(sheets.items(), start=1):
        entries.append(f'<sheet name="{escape(name)}" sheetId="{n}" r:id="rId{n}"/>')
        rels.append(f'<Relationship Id="rId{n}" Type="{DOC_REL}/worksheet" Target="worksheets/sheet{n}.xml"/>')
        xml_rows = []
        for r, row in enumerate(rows, start=1):
            cells = []
            for c, value in enumerate(row):
                ref = f"{chr(65 + c)}{r}"
                if value.replace(".", "", 1).isdigit():
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                else:
                    cells.append(f'<c r="{ref}" t="s"><v>{index(value)}</v></c>')
            xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
        parts[f"xl/worksheets/sheet{n}.xml"] = (f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData>'
                                                f'{"".join(xml_rows)}</sheetData></worksheet>')
    parts["xl/workbook.xml"] = (f'<?xml version="1.0"?><workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
                                f'{"".join(entries)}</sheets></workbook>')
    parts["xl/_rels/workbook.xml.rels"] = f'<?xml version="1.0"?><Relationships xmlns="{PKG}">{"".join(rels)}</Relationships>'
    parts["xl/sharedStrings.xml"] = (f'<?xml version="1.0"?><sst xmlns="{S}">'
                                     + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")
    return _zip(path, parts)


# ------------------------------------------------------------------ EPUB


def make_epub(path: Path, chapters: list[str], *, title: str = "Notes") -> Path:
    path = Path(path)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml",
                         '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                         '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                         "</rootfiles></container>")
        items = "".join(f'<item id="c{n}" href="c{n}.xhtml" media-type="application/xhtml+xml"/>'
                        for n in range(1, len(chapters) + 1))
        spine = "".join(f'<itemref idref="c{n}"/>' for n in range(1, len(chapters) + 1))
        archive.writestr("OEBPS/content.opf",
                         '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                         f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{escape(title)}</dc:title>'
                         f"<dc:language>en</dc:language></metadata><manifest>{items}</manifest><spine>{spine}</spine></package>")
        for n, body in enumerate(chapters, start=1):
            archive.writestr(f"OEBPS/c{n}.xhtml",
                             '<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
                             f"<head><title>Chapter {n}</title></head><body>{body}</body></html>")
    return path
