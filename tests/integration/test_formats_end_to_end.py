"""Every supported format, imported and asked about through the real command line.

One scratch project receives a document of each format, each defining a different
component; then each is asked about in plain language. The answer must carry the
document's own sentence, from the right source type. Images and scanned PDF pages go
through Windows' own OCR (skipped where the machine has no OCR language), and what
OCR produced is answered as uncertain. The live database is never opened.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import subprocess
from pathlib import Path

import pytest

from app.documents import ocr
from app.ui.cli.main import main
from tests.unit.format_fixtures import make_docx, make_epub, make_pptx, make_xlsx, w_paragraph
from tests.unit.layout_pdf_fixtures import Text, write_pdf
from tests.unit.pdf_fixtures import make_pdf

TERMS = {
    "docx": "capacitor", "pptx": "inductor", "xlsx": "resistor", "epub": "transformer", "html": "diode",
    "md": "transistor", "txt": "thermistor", "csv": "varistor", "rtf": "fuse", "pdf": "relay",
}


def sentence(term: str) -> str:
    return f"A {term} is a component that controls the flow of electric current."


def _write(folder: Path, kind: str) -> tuple[Path, str]:
    term = TERMS[kind]
    text = sentence(term)
    path = folder / f"{term}.{kind}"
    if kind == "docx":
        make_docx(path, [w_paragraph(term.title(), "Heading1"), w_paragraph(text)])
    elif kind == "pptx":
        make_pptx(path, [(term.title(), [text])])
    elif kind == "xlsx":
        make_xlsx(path, {"Glossary": [["Term", "Meaning"], [term.title(), text]]})
    elif kind == "epub":
        make_epub(path, [f"<h1>{term.title()}</h1><p>{text}</p>"])
    elif kind == "html":
        path.write_text(f"<html><body><h1>{term.title()}</h1><p>{text}</p></body></html>", encoding="utf-8")
    elif kind == "md":
        path.write_text(f"# {term.title()}\n\n{text}\n", encoding="utf-8")
    elif kind == "txt":
        path.write_text(f"{term.title()}\n\n{text}\n", encoding="utf-8")
    elif kind == "csv":
        path.write_text(f'Term,Meaning\n{term.title()},"{text}"\n', encoding="utf-8")
    elif kind == "rtf":
        path.write_text(r"{\rtf1\ansi{\fonttbl\f0 Times;}\f0 " + text + r"\par}", encoding="ascii")
    else:
        path.write_bytes(make_pdf([text]))
    return path, text


def _cli(root: Path, capsys, *args) -> tuple[int, str]:
    capsys.readouterr()
    code = main([*args, "--project-root", str(root)])
    captured = capsys.readouterr()
    return code, captured.out if code == 0 else captured.out + captured.err


def _ask(root: Path, capsys, question: str) -> dict:
    code, out = _cli(root, capsys, "ask", question, "--json")
    assert code == 0, out
    return json.loads(out)["parts"][0]


def test_every_format_is_imported_and_answered_from_its_own_sentence(tmp_path, capsys):
    root = tmp_path / "project"
    files = tmp_path / "files"
    files.mkdir()
    source_types = {"docx": "DOCX", "pptx": "PPTX", "xlsx": "XLSX", "epub": "EPUB", "html": "HTML", "md": "MARKDOWN",
                    "txt": "TEXT", "csv": "CSV", "rtf": "RTF", "pdf": "PDF"}
    for kind in TERMS:
        path, _text = _write(files, kind)
        code, out = _cli(root, capsys, "extract", str(path))
        assert code == 0, (kind, out)
        assert f", {source_types[kind]}," in out, (kind, out)
    database = sqlite3.connect(root / "data" / "database" / "knowledge.db")
    try:
        stored = {row[0] for row in database.execute("SELECT source_type FROM document")}
    finally:
        database.close()
    assert stored == set(source_types.values())
    for kind, term in TERMS.items():
        part = _ask(root, capsys, f"What is a {term}?")
        assert sentence(term) in part["answer"], (kind, part["answer"])
        assert part["status"] == "ANSWERED", (kind, part["status"])


def test_an_unsupported_file_is_refused_and_nothing_is_stored(tmp_path, capsys):
    root = tmp_path / "project"
    legacy = tmp_path / "old.doc"
    legacy.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
    code, out = _cli(root, capsys, "extract", str(legacy))
    assert code != 0 and ".docx" in out
    documents = root / "data" / "documents"
    assert not documents.exists() or not any(documents.iterdir())


# ------------------------------------------------------------------------ OCR

needs_ocr = pytest.mark.skipif(not ocr.engine_status().available,
                               reason="Windows OCR is not available on this machine")


@pytest.fixture(autouse=True)
def _ocr_switched_on(request, monkeypatch):
    """The suite switches OCR off (tests/conftest.py); the OCR tests here switch it on."""
    if request.node.get_closest_marker("skipif") is not None or "ocr" in request.node.name:
        monkeypatch.delenv(ocr.SWITCH, raising=False)
OCR_SENTENCE = "A solenoid is a coil that produces a magnetic field."


def _rendered(tmp_path: Path) -> Path:
    """A PNG of a page carrying one sentence in large type, drawn by Windows' own PDF renderer."""
    pdf = write_pdf(tmp_path / "source.pdf", [[Text(40, 600, OCR_SENTENCE, 20, "R")]])
    return ocr.render_pdf_page(pdf, 1, tmp_path / "scan.png", scale=2.0)


def _convert(png: Path, target: Path, kind: str) -> Path:
    script = (f"Add-Type -AssemblyName System.Drawing; $i = [System.Drawing.Image]::FromFile('{png}'); "
              f"$i.Save('{target}', [System.Drawing.Imaging.ImageFormat]::{kind}); $i.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True,
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return target


def _jpeg_size(data: bytes) -> tuple[int, int]:
    at = 2
    while at < len(data):
        marker, length = data[at + 1], struct.unpack(">H", data[at + 2:at + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[at + 5:at + 9])
            return width, height
        at += 2 + length
    raise ValueError("no frame header")


def _scanned_pdf(path: Path, jpeg: bytes, *, text_page: str | None = None) -> Path:
    """A PDF whose (last) page is only a picture of text - no text layer - as a scanner makes."""
    width, height = _jpeg_size(jpeg)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = []
    body = []
    if text_page is not None:
        stream = b"BT /F1 12 Tf 72 700 Td (" + text_page.encode("latin-1") + b") Tj ET"
        body += [b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                 b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)]
    image_stream = b"q 612 0 0 792 0 0 cm /Im0 Do Q"
    body += [b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8 "
             b"/Filter /DCTDecode /Length %d >>\nstream\n" % (width, height, len(jpeg)) + jpeg + b"\nendstream",
             b"<< /Length %d >>\nstream\n%s\nendstream" % (len(image_stream), image_stream)]
    first = 3
    numbers = {name: first + i for i, name in enumerate(["font", "text", "image", "draw"][-len(body):])}
    if text_page is not None:
        kids.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 %d 0 R >> >> "
                    b"/Contents %d 0 R >>" % (numbers["font"], numbers["text"]))
    kids.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /XObject << /Im0 %d 0 R >> >> "
                b"/Contents %d 0 R >>" % (numbers["image"], numbers["draw"]))
    page_numbers = [first + len(body) + i for i in range(len(kids))]
    objects.append(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % n for n in page_numbers),
                                                                  len(kids)))
    objects += body + kids
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(data))
        data += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    data += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    data += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(data))
    return path


@needs_ocr
@pytest.mark.parametrize("kind", ["png", "jpeg", "tiff"])
def test_an_image_is_read_by_ocr_and_answered_as_uncertain(tmp_path, capsys, kind):
    png = _rendered(tmp_path)
    image = png if kind == "png" else _convert(png, tmp_path / f"scan.{ {'jpeg': 'jpg', 'tiff': 'tif'}[kind]}",
                                               {"jpeg": "Jpeg", "tiff": "Tiff"}[kind])
    root = tmp_path / "project"
    code, out = _cli(root, capsys, "extract", str(image))
    assert code == 0 and "IMAGE" in out, out
    part = _ask(root, capsys, "What is a solenoid?")
    assert "coil" in part["answer"] and "magnetic field" in part["answer"]
    assert part["uncertain"] and "OCR" in " ".join(part["uncertain"])
    database = sqlite3.connect(root / "data" / "database" / "knowledge.db")
    try:
        origins = {row[0] for row in database.execute("SELECT text_origin FROM document_segment")}
    finally:
        database.close()
    assert origins == {"OCR"}
    assert list((root / "data" / "extracted").rglob("page-1.ocr.json"))  # the recognized layout is kept


@needs_ocr
def test_a_scanned_pdf_page_falls_back_to_ocr_and_a_text_page_keeps_its_own_text(tmp_path, capsys):
    jpeg = _convert(_rendered(tmp_path), tmp_path / "scan.jpg", "Jpeg").read_bytes()
    pdf = _scanned_pdf(tmp_path / "scanned.pdf", jpeg, text_page="An ammeter is an instrument that measures current.")
    root = tmp_path / "project"
    code, out = _cli(root, capsys, "extract", str(pdf))
    assert code == 0, out
    database = sqlite3.connect(root / "data" / "database" / "knowledge.db")
    try:
        origins = dict(database.execute("SELECT page_number, text_origin FROM document_segment"))
    finally:
        database.close()
    assert origins == {1: "NATIVE_TEXT", 2: "OCR"}  # native text first; OCR only where there is none
    native = _ask(root, capsys, "What is an ammeter?")
    assert "measures current" in native["answer"] and not native["uncertain"]
    recognized = _ask(root, capsys, "What is a solenoid?")
    assert "magnetic field" in recognized["answer"] and recognized["uncertain"]
