"""Deterministic PDF pages drawn from explicit glyph positions, for layout tests.

Each page is described as text placements - (x, y, size, font, text) - and horizontal
rules (fraction bars, radical bars), exactly as a typesetter writes them into a content
stream. The fonts are PDF standard fonts (Times, Symbol), so the file needs nothing
embedded and reads the same everywhere. No external tool is involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Symbol-font codes for the mathematical characters the fixtures print.
SYMBOL = {"∫": 0xF2, "∑": 0xE5, "∏": 0xD5, "√": 0xD6, "−": 0x2D, "∂": 0xB6, "∞": 0xA5, "≤": 0xA3, "≥": 0xB3,
          "≠": 0xB9, "×": 0xB4, "→": 0xAE, "α": 0x61, "β": 0x62, "ω": 0x77, "π": 0x70, "θ": 0x71, "Δ": 0x44,
          "(": 0x28, ")": 0x29, "[": 0x5B, "]": 0x5D, "{": 0x7B, "}": 0x7D, "=": 0x3D, "+": 0x2B, "·": 0xD7,
          "<": 0x3C, ">": 0x3E, ",": 0x2C, "|": 0x7C, "≈": 0xBB}

FONTS = {"R": "Times-Roman", "I": "Times-Italic", "S": "Symbol"}


@dataclass(frozen=True)
class Text:
    x: float
    y: float
    text: str
    size: float = 12.0
    font: str = "I"


@dataclass(frozen=True)
class Bar:
    x0: float
    x1: float
    y: float
    thickness: float = 0.5
    #: "fill" draws a thin rectangle (TeX); "stroke" draws a line (word processors).
    style: str = "fill"


def _string(item: Text) -> bytes:
    if item.font == "S":
        data = bytes(SYMBOL[c] for c in item.text)
    else:
        data = item.text.encode("latin-1")
    escaped = data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    return b"(" + escaped + b")"


def content(items: list) -> bytes:
    out = []
    for item in items:
        if isinstance(item, Text):
            out.append(b"BT /F%s %.2f Tf %.2f %.2f Td %s Tj ET" % (
                item.font.encode(), item.size, item.x, item.y, _string(item)))
        elif item.style == "fill":
            out.append(b"%.2f %.2f %.2f %.2f re f" % (item.x0, item.y - item.thickness / 2, item.x1 - item.x0,
                                                      item.thickness))
        else:
            out.append(b"%.2f w %.2f %.2f m %.2f %.2f l S" % (item.thickness, item.x0, item.y, item.x1, item.y))
    return b"\n".join(out)


def write_pdf(path: Path, pages: list[list], *, form: bool = False) -> Path:
    """A PDF with one page per list of placements (US Letter, origin bottom-left).

    With `form`, each page draws its content inside a Form XObject (with its own fonts)
    and the page itself holds only the call - as many producers write their pages.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_ids = {key: add(b"<< /Type /Font /Subtype /Type1 /BaseFont /%s >>" % name.encode())
                for key, name in FONTS.items()}
    fonts = b" ".join(b"/F%s %d 0 R" % (key.encode(), number) for key, number in font_ids.items())
    pages_id = len(objects) + (3 if form else 2) * len(pages) + 1
    kids = []
    for items in pages:
        stream = content(items)
        if form:
            form_id = add(b"<< /Type /XObject /Subtype /Form /BBox [0 0 612 792] /Resources << /Font << %s >> >> "
                          b"/Length %d >>\nstream\n%s\nendstream" % (fonts, len(stream), stream))
            call = b"q 1 0 0 1 0 0 cm /X0 Do Q"
            stream_id = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(call), call))
            resources = b"<< /XObject << /X0 %d 0 R >> >>" % form_id
        else:
            stream_id = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
            resources = b"<< /Font << %s >> >>" % fonts
        kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Resources %s "
                        b"/Contents %d 0 R >>" % (pages_id, resources, stream_id)))
    assert add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids), len(kids))) == pages_id
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(data))
        data += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(data)
    data += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    data += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    data += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, xref)
    path.write_bytes(bytes(data))
    return path


# ------------------------------------------------------------ representative equations

def prose(y: float, text: str) -> Text:
    return Text(72, y, text, 11, "R")


def fraction_line(y: float = 600) -> list:
    """BW = R / L, displayed: numerator above a bar, denominator below."""
    return [Text(250, y, "BW", 12, "I"), Text(272, y, "=", 12, "S"),
            Text(290, y + 8, "R", 12, "I"), Bar(287, 301, y + 4), Text(290, y - 10, "L", 12, "I")]
