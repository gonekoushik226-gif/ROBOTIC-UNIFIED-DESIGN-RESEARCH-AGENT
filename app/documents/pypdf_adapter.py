"""The `pypdf` adapter (decision D-03, ADR 0014).

**This is the only module in RUDRA permitted to import `pypdf`.** A test enforces
that. The point is not tidiness: ADR 0014 records that `pymupdf` may replace this
if extraction quality on a real textbook proves inadequate, and that swap stays a
one-file change only while nothing else reaches for the library.

The adapter's job is to be boring - translate `pypdf` into the port's value types,
and turn its failures into RUDRA failures. It makes no decisions about what to do
with a page.

Besides the text, it reads where each page places its glyph runs and draws its thin
rules (`page_geometry`), through `pypdf`'s own text-state machinery, and hands that
geometry to `app.documents.pdfmath`, which rebuilds display equations. The geometry
itself is not kept: only the equations found, each with its evidence. A page whose
geometry cannot be read keeps its text exactly as before, with the reason recorded.
"""

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pypdf.generic import ContentStream

from app.core.errors import FailureCategory, RudraError
from app.documents import pdfmath
from app.documents.ports import ParsedDocument, ParsedPage, PdfMetadata

#: Pages with more glyph runs than this are not searched for equations (a dense
#: table or index page), to keep ingestion time bounded.
MAX_LAYOUT_RUNS = 20_000
_PAINT_FILL = frozenset({b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"})
_PAINT_STROKE = frozenset({b"S", b"s", b"B", b"B*", b"b", b"b*"})


class PypdfParser:
    """`PdfParser` implemented with `pypdf`."""

    __slots__ = ()

    @property
    def name(self) -> str:
        return "pypdf"

    def open(self, path: Path) -> ParsedDocument:
        """Page count and metadata, without reading any page text."""
        reader = self._reader(path)
        if reader.is_encrypted:
            # Not an error: an encrypted PDF is a real thing a user may hold. The
            # pipeline decides what to do; the adapter only reports.
            return ParsedDocument(page_count=0, metadata=PdfMetadata(), encrypted=True)
        try:
            count = len(reader.pages)
        except (PdfReadError, ValueError, OSError) as exc:
            raise self._unreadable(path, exc) from exc
        return ParsedDocument(
            page_count=count,
            metadata=self._metadata(reader, count),
            encrypted=False,
        )

    def pages(self, path: Path) -> Iterator[ParsedPage]:
        """Yield each page's text in document order, one page at a time.

        A page that cannot be read yields a `failed` page rather than aborting the
        document: Part 2 section 61 says partial failure must not discard the whole
        source silently, and the caller needs to know *which* pages were affected.
        """
        reader = self._reader(path)
        if reader.is_encrypted:
            raise self._encrypted(path)
        try:
            total = len(reader.pages)
        except (PdfReadError, ValueError, OSError) as exc:
            raise self._unreadable(path, exc) from exc

        for index in range(total):
            number = index + 1
            try:
                with _quiet_pypdf():
                    text = reader.pages[index].extract_text() or ""
            except Exception as exc:  # noqa: BLE001 - see below
                # Deliberately broad. pypdf raises a wide and undocumented range on
                # malformed page content, and the alternative to catching it is
                # losing every later page of an otherwise readable document, which
                # Part 2 section 61 forbids. The reason is recorded, never swallowed.
                yield ParsedPage(
                    page_number=number,
                    text="",
                    extraction_method=self.name,
                    failed=True,
                    failure_reason=f"{type(exc).__name__}: {exc}",
                )
                continue
            equations: tuple = ()
            note = None
            try:
                with _quiet_pypdf():
                    glyphs, rules = page_geometry(reader.pages[index])
                if len(glyphs) <= MAX_LAYOUT_RUNS:
                    equations = tuple(pdfmath.equations(glyphs, rules))
            except Exception as exc:  # noqa: BLE001 - same reason as above
                # The text is already read; only the equation layout is lost, and says why.
                note = f"the page's glyph layout could not be read ({type(exc).__name__}: {exc})"
            yield ParsedPage(
                page_number=number, text=text, extraction_method=self.name,
                equations=equations, layout_note=note,
            )

    # ------------------------------------------------------------------ internals

    def _reader(self, path: Path) -> PdfReader:
        try:
            return PdfReader(str(path))
        except (PdfReadError, ValueError, OSError) as exc:
            raise self._unreadable(path, exc) from exc

    def _metadata(self, reader: PdfReader, page_count: int) -> PdfMetadata:
        """Read what the file states, and nothing else (Part 2 section 29).

        Blank strings are normalised to `None`: a PDF with an empty `/Title` knows
        no title, and recording `""` would be recording a fact that does not exist.
        """
        raw = reader.metadata or {}

        def value(key: str) -> str | None:
            item = raw.get(key)
            if item is None:
                return None
            text = str(item).strip()
            return text or None

        return PdfMetadata(
            title=value("/Title"),
            author=value("/Author"),
            publisher=value("/Producer"),
            publication_date=value("/CreationDate"),
            # pypdf exposes no reliable language field; leaving it None is the
            # honest answer rather than guessing from the text.
            language=None,
            page_count=page_count,
        )

    def _unreadable(self, path: Path, exc: Exception) -> RudraError:
        return RudraError.of(
            "RUDRA could not read that PDF.",
            f"{type(exc).__name__}: {exc}",
            category=FailureCategory.PARSER_FAILURE,
            stage="documents.pypdf.read",
            data_changed=False,
            retry_safe=False,
            cause=repr(exc),
            detail=str(path),
            next_options=(
                "Check the file is a valid, uncorrupted PDF.",
                "Open it in a PDF viewer to confirm it is readable.",
            ),
        )

    def _encrypted(self, path: Path) -> RudraError:
        return RudraError.of(
            "That PDF is encrypted and RUDRA cannot read its pages.",
            "The file requires a password. RUDRA does not handle credentials for "
            "user files.",
            category=FailureCategory.PARSER_FAILURE,
            stage="documents.pypdf.read",
            data_changed=False,
            retry_safe=False,
            detail=str(path),
            next_options=(
                "Provide a decrypted copy of the document.",
                "The original file has not been modified.",
            ),
        )


# ------------------------------------------------------------------ page geometry


@contextmanager
def _quiet_pypdf():
    """Silence pypdf's own per-font logging during text and layout extraction.

    pypdf warns, once per affected font dictionary, that the optional `fontTools`
    package is not installed and so it cannot parse a CFF Type1 font's internal
    encoding. RUDRA does not bundle `fontTools` (ADR: tested against the project's
    reference PDF, where every such font already names a standard `/Encoding` and
    the warning made no difference to the extracted text; see docs/FORMATS.md). The
    warning is not a RUDRA diagnostic - it is never recorded as an `extraction_issue`
    - so silencing it loses no information a technical user could otherwise see.
    """
    logger = logging.getLogger("pypdf")
    level = logger.level
    logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        logger.setLevel(level)


def _point(matrix: list[float], x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix[:6]
    return a * x + c * y + e, b * x + d * y + f


def page_geometry(page) -> tuple[list[pdfmath.Glyph], list[pdfmath.Rule]]:
    """Where a page places its glyph runs, and the thin horizontal rules it draws.

    Text positions come from `pypdf`'s layout text-state machinery (the effective
    transform, font size, rise and advance of every string shown); rules from the
    rectangles and line segments the page fills or strokes. Form XObjects are entered
    with their own matrix and fonts (many producers draw a whole page inside one), to a
    bounded depth. Page coordinates, y upwards; a page drawn upside down is turned the
    right way up, and one that mixes both orientations yields nothing.
    """
    # pypdf's layout-mode internals: pinned with the dependency, and confined to this module.
    from pypdf._font import Font
    from pypdf._text_extraction._layout_mode._fixed_width_page import resolve_font
    from pypdf._text_extraction._layout_mode._text_state_manager import TextStateManager

    contents = page.get("/Contents")
    if contents is None:
        return [], []
    manager = TextStateManager()
    glyphs: list[pdfmath.Glyph] = []
    flips: set[bool] = set()
    rules: list[pdfmath.Rule] = []
    widths = [1.0]
    path: list[tuple[str, list[float]]] = []
    entered: set[int] = set()

    def show(value) -> None:
        params = manager.text_state_params(value)
        if params.text:
            flips.add(params.flip_vertical)
            glyphs.append(pdfmath.Glyph(
                params.text, float(params.tx), float(params.displaced_tx), float(params.ty),
                abs(float(params.font_height)), len(glyphs), params.font.name,
                bool(params.font.interpretable) and not params.rotated))
        manager.add_trm(params.displacement_matrix())

    def paint(op: bytes) -> None:
        matrix = [float(v) for v in list(manager.effective_transform)[:6]]
        scale = (abs(matrix[1]) + abs(matrix[3])) or 1.0
        start = None
        for kind, operands in path:
            if kind == "re" and op in _PAINT_FILL | _PAINT_STROKE:
                x, y, w, h = operands
                (x0, y0), (x1, y1) = _point(matrix, x, y), _point(matrix, x + w, y + h)
                thick = abs(y1 - y0) if op in _PAINT_FILL else max(abs(y1 - y0), widths[-1] * scale)
                if op not in _PAINT_FILL and abs(y1 - y0) > 0.5:
                    continue
                if 0 < thick <= 2.5 and abs(x1 - x0) >= max(2.0, 3 * thick):
                    rules.append(pdfmath.Rule(min(x0, x1), max(x0, x1), (y0 + y1) / 2, thick))
            elif kind == "m":
                start = _point(matrix, *operands)
            elif kind == "l" and start is not None:
                end = _point(matrix, *operands)
                thick = widths[-1] * scale
                if op in _PAINT_STROKE and abs(end[1] - start[1]) <= 0.3 and abs(end[0] - start[0]) >= 2.0 \
                        and 0 < thick <= 2.5:
                    rules.append(pdfmath.Rule(min(start[0], end[0]), max(start[0], end[0]),
                                              (start[1] + end[1]) / 2, thick))
                start = end

    def fonts_of(resources, inherited: dict) -> dict:
        fonts = dict(inherited)
        font_dict = resources.get("/Font") if resources is not None else None
        if font_dict is not None:
            font_dict = font_dict.get_object()
            for name in font_dict:
                fonts[name] = Font.from_font_resource(font_dict[name].get_object())
        return fonts

    def enter(name, resources, fonts: dict, depth: int) -> None:
        """Draw a Form XObject in place: its matrix, its resources, then its content."""
        xobjects = resources.get("/XObject") if resources is not None else None
        target = xobjects.get_object().get(name) if xobjects is not None else None
        form = target.get_object() if target is not None else None
        if form is None or form.get("/Subtype") != "/Form" or id(form) in entered or depth >= 6:
            return
        entered.add(id(form))
        manager.add_q()
        widths.append(widths[-1])
        if "/Matrix" in form:
            manager.add_cm(*[float(v) for v in form["/Matrix"]])
        inner = form.get("/Resources")
        inner = inner.get_object() if inner is not None else resources
        walk(form, inner, fonts_of(inner, fonts), depth + 1)
        manager.remove_q()
        if len(widths) > 1:
            widths.pop()
        entered.discard(id(form))

    def walk(stream, resources, fonts: dict, depth: int) -> None:
        for operands, op in ContentStream(stream, page.pdf, "bytes").operations:
            if op == b"q":
                manager.add_q()
                widths.append(widths[-1])
            elif op == b"Q":
                manager.remove_q()
                if len(widths) > 1:
                    widths.pop()
            elif op == b"cm":
                manager.add_cm(*operands)
            elif op == b"ET":
                manager.reset_tm()
            elif op == b"Tf":
                manager.set_font(resolve_font(fonts, operands[0]), operands[1])
            elif op == b"Tj":
                show(operands[0])
            elif op == b"TJ":
                for item in operands[0]:
                    if isinstance(item, (bytes, str)):
                        show(item)
                    else:
                        manager.add_trm(manager.text_state_params("").displacement_matrix(
                            word="", td_offset=float(item)))
            elif op in (b"'", b'"'):
                manager.reset_trm()
                if op == b'"':
                    manager.set_state_param(b"Tw", operands[0])
                    manager.set_state_param(b"Tc", operands[1])
                manager.add_tm([0, -manager.TL])
                show(operands[-1])
            elif op in (b"Td", b"Tm", b"TD", b"T*"):
                manager.reset_trm()
                if op == b"Tm":
                    manager.reset_tm()
                elif op == b"TD":
                    manager.set_state_param(b"TL", -operands[1])
                elif op == b"T*":
                    operands = [0, -manager.TL]
                manager.add_tm(operands)
            elif op == b"w":
                widths[-1] = float(operands[0])
            elif op == b"re":
                path.append(("re", [float(v) for v in operands]))
            elif op in (b"m", b"l"):
                path.append((op.decode(), [float(v) for v in operands]))
            elif op in _PAINT_FILL or op in _PAINT_STROKE:
                paint(op)
                path.clear()
            elif op == b"n":
                path.clear()
            elif op == b"Do":
                enter(operands[0], resources, fonts, depth)
            else:
                manager.set_state_param(op, operands)

    resources = page.get("/Resources")
    resources = resources.get_object() if resources is not None else None
    walk(contents.get_object(), resources, page._layout_mode_fonts(), 0)
    if len(flips) > 1:
        return [], []
    if flips == {True}:
        glyphs = [pdfmath.Glyph(g.text, g.x0, g.x1, -g.y, g.size, g.order, g.font, g.readable) for g in glyphs]
        rules = [pdfmath.Rule(r.x0, r.x1, -r.y, r.thickness) for r in rules]
    return glyphs, rules
