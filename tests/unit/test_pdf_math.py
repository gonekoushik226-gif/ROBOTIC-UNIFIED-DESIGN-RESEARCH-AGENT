"""Display equations rebuilt from where a PDF page places its glyphs (app/documents/pdfmath.py).

Every fixture is a real PDF written from explicit glyph positions and rules, read back
through the pypdf adapter - the same path an imported document takes. What is tested:
structure recovered from geometry (fractions, nesting, scripts, limits, radicals,
matrices, cases, aligned derivations, numbers), nothing invented (every glyph appears
once, prose is left alone), doubt turned into uncertainty, the page text changed only for
certain structured equations, and the extraction stage storing each accordingly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.documents import pdfmath
from app.documents.pdfmath import Glyph, Rule
from app.documents.pypdf_adapter import PypdfParser
from tests.unit.layout_pdf_fixtures import Bar, Text as T, fraction_line, prose, write_pdf


def _read(tmp_path: Path, items: list, *, form: bool = False, name: str = "page.pdf"):
    path = write_pdf(tmp_path / name, [items], form=form)
    page = next(PypdfParser().pages(path))
    assert page.layout_note is None
    return page, pdfmath.place(page.text, list(page.equations))


def _one(tmp_path: Path, items: list, **options) -> pdfmath.Placement:
    _page, (_text, placements) = _read(tmp_path, items, **options)
    assert len(placements) == 1, [p.equation.linear for p in placements]
    return placements[0]


NESTED = [T(200, 600, "Z", 12, "I"), T(212, 600, "=", 12, "S"), T(232, 610, "1", 12, "R"), Bar(226, 250, 604),
          T(228, 594, "1", 10, "R"), Bar(227, 236, 590.5), T(229, 584, "a", 10, "I"),
          T(238, 588, "+", 10, "S"), T(245, 588, "b", 10, "I")]
SCRIPTS = [T(200, 600, "E", 12, "I"), T(212, 600, "=", 12, "S"), T(224, 600, "mc", 12, "I"),
           T(238, 605, "2", 8, "R"), T(250, 600, "+", 12, "S"), T(262, 600, "x", 12, "I"), T(268, 596, "i", 8, "I")]
INTEGRAL = [T(200, 600, "W", 12, "I"), T(214, 600, "=", 12, "S"), T(228, 596, "∫", 20, "S"),
            T(240, 592, "0", 8, "R"), T(240, 612, "T", 8, "I"), T(248, 600, "p", 12, "I"), T(256, 600, "dt", 12, "I")]
SUM = [T(200, 600, "S", 12, "I"), T(212, 600, "=", 12, "S"), T(226, 596, "∑", 20, "S"),
       T(226, 586, "i=1", 8, "I"), T(229, 616, "n", 8, "I"), T(246, 600, "x", 12, "I"), T(252, 596, "i", 8, "I")]
RADICAL = [T(200, 600, "V", 12, "I"), T(210, 600, "=", 12, "S"), T(222, 600, "√", 12, "S"), Bar(229, 262, 611),
           T(231, 600, "2", 12, "R"), T(238, 600, "gh", 12, "I")]
MATRIX = [T(150, 600, "A", 12, "I"), T(162, 600, "=", 12, "S"), T(174, 594, "(", 30, "S"),
          T(186, 608, "a", 12, "I"), T(206, 608, "b", 12, "I"), T(186, 592, "c", 12, "I"), T(206, 592, "d", 12, "I"),
          T(218, 594, ")", 30, "S")]
CASES = [T(150, 600, "f(x)", 12, "I"), T(175, 600, "=", 12, "S"), T(188, 594, "{", 30, "S"),
         T(200, 608, "x,", 12, "I"), T(240, 608, "x", 12, "I"), T(248, 608, "≥", 12, "S"), T(258, 608, "0", 12, "R"),
         T(200, 592, "−", 12, "S"), T(207, 592, "x,", 12, "I"), T(240, 592, "x", 12, "I"), T(248, 592, "<", 12, "S"),
         T(258, 592, "0", 12, "R")]
ALIGNED = [T(200, 620, "y", 12, "I"), T(212, 620, "=", 12, "S"), T(224, 620, "(a+b)", 12, "I"), T(254, 625, "2", 8, "R"),
           T(212, 602, "=", 12, "S"), T(224, 602, "a", 12, "I"), T(230, 607, "2", 8, "R"), T(236, 602, "+", 12, "S"),
           T(246, 602, "2ab", 12, "I"), T(266, 602, "+", 12, "S"), T(276, 602, "b", 12, "I"), T(282, 607, "2", 8, "R")]
DERIVATIVE = [T(200, 600, "i", 12, "I"), T(208, 600, "=", 12, "S"), T(220, 600, "C", 12, "I"),
              T(232, 608, "dv", 12, "I"), Bar(231, 245, 604), T(232, 590, "dt", 12, "I")]
PARTIAL = [T(200, 600, "u", 12, "I"), T(210, 600, "=", 12, "S"), T(224, 608, "∂", 12, "S"), T(231, 608, "f", 12, "I"),
           Bar(222, 238, 604), T(224, 590, "∂", 12, "S"), T(231, 590, "x", 12, "I")]


@pytest.mark.parametrize("items, expected", [
    (fraction_line(), r"BW = \frac{R}{L}"),
    (NESTED, r"Z = \frac{1}{\frac{1}{a} + b}"),
    (SCRIPTS, r"E = mc^{2} + x_{i}"),
    (INTEGRAL, r"W = \int_{0}^{T} p dt"),
    (SUM, r"S = \sum_{i=1}^{n} x_{i}"),
    (RADICAL, r"V = \sqrt{2gh}"),
    (MATRIX, r"A = \begin{pmatrix} a & b \\ c & d \end{pmatrix}"),
    (CASES, r"f(x) = \begin{cases} x, & x \ge 0 \\ - x, & x < 0 \end{cases}"),
    (ALIGNED, r"\begin{aligned} y & = {(a+b)}^{2} \\ & = a^{2} + 2ab + b^{2} \end{aligned}"),
    (DERIVATIVE, r"i = C \frac{dv}{dt}"),
    (PARTIAL, r"u = \frac{\partial f}{\partial x}"),
    ([*fraction_line(), T(480, 600, "(2.13)", 12, "R")], r"BW = \frac{R}{L} \tag{2.13}"),
])
def test_structure_is_rebuilt_from_geometry_and_replaces_the_flat_text(tmp_path, items, expected):
    placement = _one(tmp_path, items)
    assert placement.equation.linear == expected
    assert placement.certain and placement.substituted and placement.equation.evidence
    _page, (text, _p) = _read(tmp_path, items, name="again.pdf")
    assert expected in text


def test_a_page_drawn_inside_a_form_xobject_is_read_the_same(tmp_path):
    placement = _one(tmp_path, NESTED, form=True)
    assert placement.equation.linear == r"Z = \frac{1}{\frac{1}{a} + b}" and placement.certain


def test_every_rebuilt_expression_can_be_drawn_by_the_formula_renderer(tmp_path):
    from app.ui.gui import mathrender

    for index, items in enumerate((NESTED, SCRIPTS, INTEGRAL, SUM, RADICAL, MATRIX, CASES, ALIGNED, PARTIAL)):
        linear = _one(tmp_path, items, name=f"r{index}.pdf").equation.linear
        assert mathrender.rendered_text(mathrender.parse(linear)).strip()


def _symbols_of(text: str) -> list[str]:
    """The letters and digits of a notation, without command names: what the page printed."""
    return sorted(c for c in re.sub(r"\\(?:(?:begin|end)\{[A-Za-z]+\}|[A-Za-z]+)", "", text) if c.isalnum())


@pytest.mark.parametrize("items", [fraction_line(), NESTED, SCRIPTS, INTEGRAL, SUM, RADICAL, MATRIX, CASES, ALIGNED])
def test_nothing_is_invented_or_dropped(tmp_path, items):
    equation = _one(tmp_path, items).equation
    assert _symbols_of(equation.linear) == _symbols_of(equation.flat)


def test_a_long_equation_keeps_every_term_in_order(tmp_path):
    x = 90.0
    items = [T(x, 600, "y", 12, "I"), T(x + 10, 600, "=", 12, "S")]
    x += 22
    for n in range(1, 13):
        items += [T(x, 600, "a", 12, "I"), T(x + 6, 596, str(n), 8, "R"), T(x + 16, 600, "+", 12, "S")]
        x += 30
    items.append(T(x, 600, "c", 12, "I"))
    linear = _one(tmp_path, items).equation.linear
    assert linear == "y = " + " + ".join(f"a_{{{n}}}" for n in range(1, 13)) + " + c"


# ------------------------------------------------------------------ nothing invented


def test_mathematics_inside_a_sentence_is_left_as_the_text_layer_gives_it(tmp_path):
    page, (text, placements) = _read(tmp_path, [prose(600, "The law V = IR holds for every ohmic resistor.")])
    assert placements == [] and text == page.text


def test_a_condition_line_is_not_taken_for_an_equation(tmp_path):
    _page, (_text, placements) = _read(tmp_path, [prose(600, "If x = 0 then")])
    assert placements == []


def test_lines_with_their_own_left_hand_sides_stay_separate_equations(tmp_path):
    items = [T(200, 620, "A", 12, "I"), T(212, 620, "=", 12, "S"), T(224, 620, "2", 12, "R"),
             T(200, 602, "B", 12, "I"), T(212, 602, "=", 12, "S"), T(224, 602, "3", 12, "R")]
    _page, (_text, placements) = _read(tmp_path, items)
    assert [p.equation.linear for p in placements] == ["A = 2", "B = 3"]


def test_an_equation_without_structure_is_certain_and_its_text_is_untouched(tmp_path):
    items = [prose(640, "Ohm's law:"), T(250, 600, "V", 12, "I"), T(262, 600, "=", 12, "S"), T(274, 600, "IR", 12, "I")]
    page, (text, placements) = _read(tmp_path, items)
    (placement,) = placements
    assert placement.certain and not placement.substituted and text == page.text
    assert page.text[placement.span[0]:placement.span[1]] == "V = IR"


# ------------------------------------------------------------------ doubt is uncertainty


@pytest.mark.parametrize("items, reason", [
    ([T(200, 600, "a", 12, "I"), T(210, 600, "=", 12, "S"), T(222, 600, "b", 12, "I"), T(223, 612, "c", 12, "I")],
     "belong to none of its structures"),
    ([T(200, 600, "a", 12, "I"), T(210, 600, "=", 12, "S"), T(224, 608, "b", 12, "I"), Bar(222, 234, 604)],
     "glyphs on one side only"),
    ([T(200, 600, "V", 12, "I"), T(210, 600, "=", 12, "S"), T(222, 600, "√", 12, "S"), T(231, 600, "2gh", 12, "I")],
     "radical sign has no bar"),
    ([T(200, 600, "a", 12, "I"), T(210, 600, "=", 12, "S"), T(222, 600, "b", 12, "I"), T(223, 600, "c", 12, "I")],
     "printed over one another"),
    ([*MATRIX[:5], T(186, 592, "c", 12, "I"), T(218, 594, ")", 30, "S")], "same number of cells"),
])
def test_an_ambiguous_layout_is_uncertain_and_the_page_text_is_kept(tmp_path, items, reason):
    page, (text, placements) = _read(tmp_path, items)
    (placement,) = placements
    assert not placement.certain and not placement.substituted
    assert any(reason in r for r in placement.reasons), placement.reasons
    assert text == page.text  # the original extracted text is preserved
    assert placement.original == page.text[placement.span[0]:placement.span[1]]


def test_an_undecodable_glyph_makes_the_equation_uncertain():
    glyphs = [Glyph("a", 200, 206, 600, 12, 0), Glyph("=", 210, 217, 600, 12, 1), Glyph("�", 222, 228, 600, 12, 2)]
    (equation,) = pdfmath.equations(glyphs, [])
    assert not equation.certain and any("decoded" in r for r in equation.reasons)


def test_an_equation_the_plain_text_cannot_locate_is_not_substituted():
    glyphs = [Glyph("x", 200, 206, 600, 12, 0), Glyph("=", 210, 217, 600, 12, 1), Glyph("y", 222, 228, 606, 8, 2)]
    (equation,) = pdfmath.equations(glyphs, [])
    text, (placement,) = pdfmath.place("something else entirely", [equation])
    assert text == "something else entirely" and placement.span is None and not placement.certain


def test_an_equation_sharing_its_text_line_with_other_text_is_not_substituted():
    glyphs = [Glyph("x", 200, 206, 600, 12, 0), Glyph("=", 210, 217, 600, 12, 1), Glyph("y", 222, 228, 600, 12, 2),
              Glyph("2", 229, 233, 605, 8, 3)]
    (equation,) = pdfmath.equations(glyphs, [])
    assert equation.certain
    text, (placement,) = pdfmath.place("see x = y2 below", [equation])
    assert text == "see x = y2 below" and not placement.certain
    assert any("other characters" in r for r in placement.reasons)


def test_a_superscript_goes_to_the_base_it_is_nearest():
    # Two tightly spaced equation lines; the raised '+' belongs to the lower line's '0'.
    glyphs = [Glyph("t", 200, 204, 612, 12, 0), Glyph("=", 208, 215, 612, 12, 1), Glyph("1", 219, 225, 612, 12, 2),
              Glyph("s", 200, 205, 600, 12, 3), Glyph("=", 208, 215, 600, 12, 4), Glyph("0", 219, 225, 600, 12, 5),
              Glyph("+", 225.5, 230, 604, 8, 6)]
    found = pdfmath.equations(glyphs, [])
    assert [e.linear for e in found] == ["t = 1", "s = 0^{+}"]


# ------------------------------------------------------------------ into the knowledge base


@pytest.fixture
def pipelines(tmp_path):
    from app.documents.pipeline import IngestionPipeline
    from app.documents.readers import DocumentReader
    from app.extraction.pipeline import ExtractionPipeline
    from app.storage import Repository, connect, migrate

    db = tmp_path / "data" / "database" / "knowledge.db"
    db.parent.mkdir(parents=True)
    connection = connect(db)
    migrate(connection, database_path=db)
    repository = Repository(connection)
    documents = tmp_path / "data" / "documents"

    def run(items, *, layout: bool = True):
        pdf = write_pdf(tmp_path / "circuits.pdf", [items])
        report = IngestionPipeline(repository, DocumentReader(PypdfParser(), use_ocr=False), documents).ingest(pdf)
        connection.commit()
        pipeline = ExtractionPipeline(repository, layout_dir=documents.parent / "extracted" if layout else None)
        run_ = pipeline.start(report.document.id)
        connection.commit()
        pipeline.execute(run_)
        connection.commit()
        rows = connection.execute(
            "SELECT k.statement, k.certainty, e.evidence_text, e.extraction_method, e.segment_id, e.char_start, "
            "e.char_end FROM knowledge_object k JOIN evidence e ON e.subject_id = k.id "
            "WHERE k.knowledge_type = 'EQUATION' ORDER BY e.char_start").fetchall()
        return report, rows

    yield {"run": run, "connection": connection, "documents": documents}
    connection.close()


PAGE = [prose(720, "The bandwidth of a series circuit is"), *[
    T(250, 680, "BW", 12, "I"), T(272, 680, "=", 12, "S"), T(290, 688, "R", 12, "I"), Bar(287, 301, 684),
    T(290, 670, "L", 12, "I")],
    prose(640, "where R is the resistance."), prose(610, "Ohm's law gives"),
    T(250, 580, "V", 12, "I"), T(262, 580, "=", 12, "S"), T(274, 580, "IR", 12, "I"),
    prose(550, "and for the stored charge"),
    T(250, 520, "Q", 12, "I"), T(262, 520, "=", 12, "S"), T(274, 520, "CV", 12, "I"), T(275, 532, "k", 12, "I")]


def test_certain_equations_are_stored_as_the_source_states_them_and_doubtful_ones_as_uncertain(pipelines):
    report, rows = pipelines["run"](PAGE)
    by_statement = {row[0]: row for row in rows}
    fraction = by_statement[r"BW = \frac{R}{L}"]
    assert fraction[1] == "REPORTED_BY_SOURCE" and fraction[3].endswith("+layout")
    assert by_statement["V = IR"][1] == "REPORTED_BY_SOURCE"
    doubtful = by_statement["Q = CV"]
    assert doubtful[1] == "UNCERTAIN" and doubtful[3].endswith("+layout?")
    assert any("display equation(s) were rebuilt" in note for note in report.notes)
    # Each quote is exactly where its evidence says, in the stored page text.
    connection = pipelines["connection"]
    for statement, _certainty, quote, _method, segment_id, start, end in rows:
        stored = connection.execute("SELECT text FROM document_segment WHERE id = ?", (segment_id,)).fetchone()[0]
        assert stored[start:end] == quote


def test_the_layout_record_keeps_the_original_text_evidence_and_doubts(pipelines):
    report, _rows = pipelines["run"](PAGE)
    folder = pipelines["documents"].parent / "extracted" / report.document.file_hash
    record = json.loads((folder / "page-1.math.json").read_text(encoding="utf-8"))
    assert record["method"] == pdfmath.METHOD and record["page"] == 1
    fraction = next(e for e in record["equations"] if e["linear"].startswith("BW"))
    assert fraction["certain"] and fraction["substituted"] and fraction["page_text"] == "BW = R\nL"
    assert any(line.startswith("fraction:") for line in fraction["evidence"])
    doubtful = next(e for e in record["equations"] if e["linear"].startswith("Q"))
    assert not doubtful["certain"] and doubtful["reasons"]


def test_without_the_layout_record_pdf_equations_stay_uncertain(pipelines):
    _report, rows = pipelines["run"](PAGE, layout=False)
    assert rows and all(row[1] == "UNCERTAIN" for row in rows)


def test_an_uncertain_rebuilt_equation_explains_itself_in_answers():
    from app.orchestration.answers import _why_uncertain

    reason = _why_uncertain({"certainty": "UNCERTAIN"},
                            [{"extraction_method": "deterministic/EquationCandidate@v6+layout?"}])
    assert "PDF page" in reason and "compare it with the source" in reason
