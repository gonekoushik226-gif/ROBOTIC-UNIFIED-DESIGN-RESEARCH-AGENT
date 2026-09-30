"""Synthetic PDFs, built byte by byte, for Phase 4 tests.

**These are fixtures, not a substitute for the prototype document.** No real
technical PDF has been supplied (Part 5 section 249 and Part 6 section 53 both want
the first vertical slice driven by one), so Phase 4's acceptance criteria are
**not** claimed against a real book. What these fixtures do prove is that every
stage behaves correctly on input whose exact content is known - which is the part
that can be established without the real document.

The PDFs are written here rather than produced by a library because `pypdf` cannot
author text content, and adding a PDF-writing dependency to test the PDF-reading
dependency would be a poor trade against Part 1 section 15.
"""

from __future__ import annotations


def make_pdf(pages: list[str]) -> bytes:
    """A minimal but genuine PDF carrying `pages` of text, one string per page.

    Real structure: catalog, page tree, one page object and one content stream per
    page, a shared Type1 font, and a correct cross-reference table. `pypdf` reads
    it the same way it reads any other PDF.
    """
    page_count = len(pages)
    font_object = 3 + 2 * page_count
    kids = " ".join(f"{3 + 2 * index} 0 R" for index in range(page_count))

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode(),
    ]
    for index, body in enumerate(pages):
        content_object = 4 + 2 * index
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_object} 0 R >> >> "
                f"/Contents {content_object} 0 R >>"
            ).encode()
        )
        operators = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
        for line in body.split("\n"):
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            operators.append(f"({escaped}) Tj T*")
        operators.append("ET")
        stream = "\n".join(operators).encode()
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    return bytes(out)


#: A textbook-shaped document: decimal numbering, the scheme Part 2 section 35
#: gives as its first example.
TEXTBOOK_PAGES = [
    "Chapter 5  Sequential Logic",
    "5.1  Flip-Flops\nA flip-flop is a bistable circuit that stores one bit of data.",
    "5.2  Counters\nA counter is a sequential circuit that counts input pulses.\n"
    "5.2.1  Ripple Counters\nRipple counters propagate their carry asynchronously.",
    "Definition 5.1  A latch is a level-sensitive storage element used widely.",
]

#: Part 2 section 35's *second* scheme, which a decimal-only detector would miss.
#: Its presence here is the point: section 35 requires adaptivity.
UNIT_MODULE_PAGES = [
    "Unit III  Analog Electronics",
    "Module 2  Amplifier Configurations\n"
    "A common-emitter amplifier provides both voltage and current gain.",
    "Topic A  Biasing\nBiasing sets the quiescent operating point of the device.",
]

#: Section 35's third case - "another may have no explicit hierarchy".
NO_HIERARCHY_PAGES = [
    "Some introductory prose about semiconductors and their behaviour in circuits.",
    "More prose continuing the discussion without any heading whatsoever here.",
]


def textbook_pdf() -> bytes:
    return make_pdf(TEXTBOOK_PAGES)


def unit_module_pdf() -> bytes:
    return make_pdf(UNIT_MODULE_PAGES)


def flat_pdf() -> bytes:
    return make_pdf(NO_HIERARCHY_PAGES)


def image_only_pdf() -> bytes:
    """A page with no extractable text - what a scanned page looks like to a parser.

    Used to prove Part 2 section 33's detection step works and reports the page as
    needing OCR, rather than silently producing an empty document.
    """
    return make_pdf([" "])
