"""Reading the supported document formats (app/documents/formats.py, readers.py).

Every format is identified by its bytes, not trusted by its name; each reader keeps the
document's own structure (headings, lists, tables, slides, sheets, equations) and says
where each piece came from; formats RUDRA does not read are refused with the reason and
what to do instead; and container formats are opened with limits and without DTDs.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.documents import formats, readers
from app.documents.formats import UnsupportedFormat, detect_format
from app.documents.readers import DocumentReader, DocumentReadError
from tests.unit.format_fixtures import (
    m_frac, m_run, m_sup, make_docx, make_epub, make_pptx, make_xlsx, w_equation, w_paragraph, w_table,
)
from tests.unit.pdf_fixtures import make_pdf

DEFINITION = "A capacitor is a device that stores electric charge."


def _pages(path: Path):
    reader = DocumentReader(use_ocr=False)
    return reader, list(reader.pages(path))


def _text(pages) -> str:
    return "\n".join(p.text for p in pages)


# ------------------------------------------------------------------ identification


@pytest.fixture
def samples(tmp_path):
    made = {
        "pdf": tmp_path / "a.pdf", "docx": tmp_path / "a.docx", "pptx": tmp_path / "a.pptx",
        "xlsx": tmp_path / "a.xlsx", "epub": tmp_path / "a.epub", "html": tmp_path / "a.html",
        "markdown": tmp_path / "a.md", "txt": tmp_path / "a.txt", "csv": tmp_path / "a.csv", "rtf": tmp_path / "a.rtf",
        "png": tmp_path / "a.png", "jpeg": tmp_path / "a.jpg", "tiff": tmp_path / "a.tif",
    }
    made["pdf"].write_bytes(make_pdf([DEFINITION]))
    make_docx(made["docx"], [w_paragraph(DEFINITION)])
    make_pptx(made["pptx"], [("Capacitors", [DEFINITION])])
    make_xlsx(made["xlsx"], {"Terms": [["Term", "Meaning"], ["Capacitor", DEFINITION]]})
    make_epub(made["epub"], [f"<h1>Capacitors</h1><p>{DEFINITION}</p>"])
    made["html"].write_text(f"<!doctype html><html><body><h1>Capacitors</h1><p>{DEFINITION}</p></body></html>",
                            encoding="utf-8")
    made["markdown"].write_text(f"# Capacitors\n\n{DEFINITION}\n", encoding="utf-8")
    made["txt"].write_text(DEFINITION, encoding="utf-8")
    made["csv"].write_text(f"Term,Meaning\nCapacitor,\"{DEFINITION}\"\n", encoding="utf-8")
    made["rtf"].write_text(r"{\rtf1\ansi{\fonttbl\f0 Times;}\f0 " + DEFINITION + r"\par}", encoding="ascii")
    made["png"].write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    made["jpeg"].write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    made["tiff"].write_bytes(b"II*\x00" + b"\x00" * 64)
    return made


def test_every_supported_format_is_identified_by_its_bytes(samples):
    for key, path in samples.items():
        assert detect_format(path).key == key, path.name


def test_a_misnamed_file_is_identified_by_what_it_is(samples, tmp_path):
    disguised = tmp_path / "notes.txt"
    disguised.write_bytes(samples["docx"].read_bytes())
    assert detect_format(disguised).key == "docx"


def test_the_backup_format_accepts_every_stored_document_suffix():
    from app.storage.archive import DOCUMENT_NAME

    for suffix in formats.STORED_SUFFIXES:
        assert DOCUMENT_NAME.match("a" * 64 + suffix), suffix


@pytest.mark.parametrize("name, head, word", [
    ("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".docx"),
    ("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".xlsx"),
    ("old.ppt", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".pptx"),
    ("data.json", b'{"a": 1}', "JSON"),
    ("data.xml", b"<?xml version='1.0'?><a/>", "XML"),
    ("tool.exe", b"MZ\x90\x00\x03\x00\x00\x00", "Supported:"),
])
def test_unsupported_formats_are_refused_with_the_reason(tmp_path, name, head, word):
    path = tmp_path / name
    path.write_bytes(head + b"\x00" * 32)
    with pytest.raises(UnsupportedFormat) as refused:
        detect_format(path)
    assert word in str(refused.value)


def test_a_zip_that_is_no_office_document_is_refused(tmp_path):
    path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "hello")
    with pytest.raises(UnsupportedFormat):
        detect_format(path)


# ------------------------------------------------------------------ structure and location


def test_word_keeps_headings_lists_tables_and_office_math(tmp_path):
    path = make_docx(tmp_path / "notes.docx", [
        w_paragraph("Capacitance", "Heading1"), w_paragraph(DEFINITION),
        w_paragraph("It stores energy.", numbered=True),
        w_table([["Quantity", "Unit"], ["Capacitance", "farad"]]),
        w_equation(m_run("C = ") + m_frac(m_run("Q"), m_run("V"))), w_equation(m_run("E = ") + m_sup(m_run("V"), m_run("2"))),
    ])
    reader, pages = _pages(path)
    text = _text(pages)
    assert DEFINITION in text and "- It stores energy." in text
    assert r"C = \frac{Q}{V}" in text and "V^{2}" in text
    assert any((1, "Capacitance") in p.headings for p in pages)
    assert all(p.origin == "NATIVE_TEXT" and p.extraction_method == "docx" for p in pages)
    document = reader.open(path)
    assert document.metadata.title == "Circuits" and document.metadata.author == "A. Author"


def test_powerpoint_reads_slides_as_units_with_their_notes(tmp_path):
    path = make_pptx(tmp_path / "deck.pptx", [("Capacitors", [DEFINITION]), ("Inductors", ["An inductor stores energy."])],
                     notes={2: "Speaker note about inductors."})
    _reader, pages = _pages(path)
    assert [p.page_number for p in pages] == [1, 2]
    assert pages[0].location.startswith("Slide 1") and DEFINITION in pages[0].text
    assert "Speaker note about inductors." in pages[1].text


def test_excel_reads_each_sheet_with_its_rows(tmp_path):
    path = make_xlsx(tmp_path / "terms.xlsx", {"Terms": [["Term", "Meaning"], ["Capacitor", DEFINITION]],
                                               "Units": [["Quantity", "Unit"], ["Charge", "coulomb"]]})
    _reader, pages = _pages(path)
    assert {p.location.split(" ", 1)[0] for p in pages} == {"Sheet"}
    text = _text(pages)
    assert DEFINITION in text and "coulomb" in text


def test_epub_html_and_markdown_keep_headings_and_mathml(tmp_path):
    mathml = ('<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>C</mi><mo>=</mo>'
              '<mfrac><mi>Q</mi><mi>V</mi></mfrac></math>')
    epub = make_epub(tmp_path / "book.epub", [f"<h1>Capacitors</h1><p>{DEFINITION}</p><p>{mathml}</p>"])
    html = tmp_path / "page.html"
    html.write_text(f"<html><body><h2>Capacitors</h2><p>{DEFINITION}</p>{mathml}</body></html>", encoding="utf-8")
    md = tmp_path / "notes.md"
    md.write_text(f"# Capacitors\n\n{DEFINITION}\n\n$$C = \\frac{{Q}}{{V}}$$\n\n| a | b |\n|---|---|\n| 1 | 2 |\n",
                  encoding="utf-8")
    for path in (epub, html, md):
        _reader, pages = _pages(path)
        text = _text(pages)
        assert DEFINITION in text and r"\frac{Q}{V}" in text, path.name
        assert any(title == "Capacitors" for p in pages for _level, title in p.headings), path.name


def test_plain_text_csv_and_rtf_are_read(tmp_path, samples):
    for key in ("txt", "csv", "rtf"):
        _reader, pages = _pages(samples[key])
        assert DEFINITION in _text(pages), key


def test_a_dtd_in_a_document_part_is_refused_never_expanded():
    with pytest.raises(DocumentReadError):
        readers.parse_xml(b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "boom">]><a>&x;</a>')


def test_a_document_part_larger_than_the_limit_is_refused(tmp_path, monkeypatch):
    path = make_docx(tmp_path / "big.docx", [w_paragraph(DEFINITION * 50)])
    monkeypatch.setattr(readers, "MAX_XML_BYTES", 100)
    with pytest.raises(DocumentReadError):
        list(DocumentReader(use_ocr=False).pages(path))


def test_a_container_with_too_many_parts_is_refused(tmp_path, monkeypatch):
    path = make_docx(tmp_path / "many.docx", [w_paragraph(DEFINITION)])
    monkeypatch.setattr(readers, "MAX_ARCHIVE_MEMBERS", 1)
    with pytest.raises(DocumentReadError):
        list(DocumentReader(use_ocr=False).pages(path))
