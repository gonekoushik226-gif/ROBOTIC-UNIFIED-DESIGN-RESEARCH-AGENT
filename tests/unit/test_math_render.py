"""Textbook display of formulas: the parsed structure, the layout's proportions, and the window.

The parse is checked structurally (a fraction is a Frac, a power a Script, ...). The
layout is checked for what makes it textbook mathematics rather than a raw string: the
numerator stands above the denominator, a superscript is raised and smaller, a radical
covers its body, an integral sign is taller than the text. The last tests draw into a
real (hidden) Tk canvas: no raw notation such as ``\\frac`` or ``^`` is drawn.
"""

from __future__ import annotations

import tkinter as tk

import pytest

from app.ui.gui import mathrender as m
from app.ui.gui.mathrender import Atom, BigOp, Fenced, Frac, Radical, Row, Script


def only(text: str):
    tree = m.parse(text)
    assert isinstance(tree, Row)
    return tree.items


def texts(box: m.Box) -> list[str]:
    return [item[3] for item in box.items if item[0] == "text"]


def layout(text: str, size: float = 20.0) -> m.Box:
    return m.layout(m.parse(text), size, m.ApproximateMetrics())


RAW_NOTATION = ("\\", "^", "_", "{", "}", "sqrt", "frac", "int ")


# ---------------------------------------------------------------- parsing


@pytest.mark.parametrize("text", ["dy/dx", r"\frac{dy}{dx}", r"\dfrac{dy}{dx}"])
def test_fractions(text):
    (fraction,) = only(text)
    assert fraction == Frac(Atom("dy", "var"), Atom("dx", "var"))


def test_a_linear_fraction_takes_the_operands_that_touch_it():
    items = only("I = V / Rtotal")
    assert items[1] == Atom("=", "rel") and items[2] == Frac(Atom("V", "var"), Atom("Rtotal", "var"))
    half, *rest = only("1/2 m v^2")
    assert half == Frac(Atom("1", "num"), Atom("2", "num")) and rest[0] == Atom("m", "var")
    grouped = only("(a + b)/(c + d)")[0]
    assert isinstance(grouped, Frac) and isinstance(grouped.numerator, Row) and len(grouped.numerator.items) == 3


@pytest.mark.parametrize(("text", "power"), [("x^2", "2"), ("x²", "2"), ("x^{2}", "2"), ("x**2", "2")])
def test_powers(text, power):
    (script,) = only(text)
    assert isinstance(script, Script) and script.base == Atom("x", "var")
    assert m.rendered_text(script.sup) == power


def test_compound_and_negative_exponents():
    (script,) = only("x^(n+1)")
    assert m.rendered_text(script.sup) == "n+1"
    (script,) = only("e^{-x}")
    assert m.rendered_text(script.sup) == "−x"


@pytest.mark.parametrize(("text", "sub"), [("x_1", "1"), ("x₁", "1"), ("V_{out}", "out"), ("R1", "1")])
def test_subscripts(text, sub):
    (script,) = only(text)
    assert isinstance(script, Script) and m.rendered_text(script.sub) == sub


def test_a_word_with_digits_is_not_split():
    assert only("Rtotal") == (Atom("Rtotal", "var"),)


@pytest.mark.parametrize(("text", "index"), [("sqrt(x)", None), ("√x", None), (r"\sqrt{x}", None),
                                             (r"\sqrt[3]{x}", "3"), ("∛x", "3")])
def test_roots(text, index):
    (radical,) = only(text)
    assert isinstance(radical, Radical) and m.rendered_text(radical.body) == "x"
    assert (m.rendered_text(radical.index) if radical.index is not None else None) == index


@pytest.mark.parametrize("text", [r"\int x dx", "∫ x dx", r"\int_0^1 x dx", "∫_a^b f(x) dx"])
def test_integrals(text):
    first = only(text)[0]
    assert isinstance(first, BigOp) and first.symbol == "∫"


def test_integral_limits():
    (integral, *_) = only(r"\int_0^\pi \sin\theta \, d\theta")
    assert m.rendered_text(integral.lower) == "0" and m.rendered_text(integral.upper) == "π"


def test_sums_and_limits():
    (total, *_) = only(r"\sum_{k=1}^{n} k")
    assert total.symbol == "∑" and m.rendered_text(total.lower) == "k=1"
    lim = only(r"\lim_{x \to 0} f(x)")[0]
    assert isinstance(lim, Script) and lim.base == Atom("lim", "func")


@pytest.mark.parametrize(("text", "letter"), [(r"\alpha", "α"), (r"\theta", "θ"), (r"\Omega", "Ω"), ("theta", "θ"),
                                              ("pi", "π"), ("ω", "ω"), ("Δ", "Δ")])
def test_greek_letters(text, letter):
    (atom,) = only(text)
    assert atom.text == letter and atom.kind == "greek"


@pytest.mark.parametrize("name", ["sin", "cos", "tan", "sec", "csc", "cot", "log", "ln", "exp"])
def test_functions_are_upright(name):
    items = only(f"{name} x")
    assert items[0] == Atom(name, "func") and items[1] == Atom("x", "var")
    assert only(f"\\{name} x")[0] == Atom(name, "func")


@pytest.mark.parametrize("text", ["sin^-1 x", "sin⁻¹ x", r"\sin^{-1} x"])
def test_inverse_trigonometric_notation(text):
    script = only(text)[0]
    assert script.base == Atom("sin", "func") and m.rendered_text(script.sup) == "−1"


@pytest.mark.parametrize("name", ["arcsin", "arccos", "arctan"])
def test_inverse_trigonometric_names(name):
    assert only(f"{name}(x)")[0] == Atom(name, "func")


def test_operators_become_mathematical_symbols():
    rendered = m.rendered_text(m.parse("a <= b >= c != d -> e +- f * g"))
    for symbol in "≤≥≠→±·":
        assert symbol in rendered
    assert m.rendered_text(m.parse(r"a \cdot b \times c \leq d \infty \partial")) == "a·b×c≤d∞∂"
    assert only("a - b")[1] == Atom("−", "op")  # a true minus sign, not a hyphen


def test_differentiation_as_in_a_textbook():
    fraction, fenced, equals, *_ = only("d/dx(x^n) = n x^(n-1)")
    assert fraction == Frac(Atom("d", "var"), Atom("dx", "var"))
    assert isinstance(fenced, Fenced) and equals == Atom("=", "rel")
    second = only("d^2y/dx^2")[0]
    assert isinstance(second, Frac) and isinstance(second.denominator, Script)


def test_integration_as_in_a_textbook():
    items = only(r"\int x^n dx = \frac{x^{n+1}}{n+1} + C")
    assert isinstance(items[0], BigOp) and isinstance(items[1], Script) and items[2] == Atom("dx", "var")
    assert any(isinstance(item, Frac) for item in items)
    items = only("∫ 1/(1 + x²) dx = tan⁻¹ x + C")
    assert isinstance(items[1], Frac) and isinstance(items[1].denominator, Row)


def test_what_cannot_be_read_is_kept_not_dropped():
    assert "@" in m.rendered_text(m.parse("a @ b"))
    assert m.rendered_text(m.parse(r"\unknowncommand x")) == "unknowncommandx"
    for broken in ("x^", "(a + b", "a/", "/b", r"\frac{a}", "{{{", "}}}", "", "   "):
        m.layout(m.parse(broken), 16, m.ApproximateMetrics())  # never raises


# ---------------------------------------------------------------- layout


def _y_of(box: m.Box, text: str) -> float:
    return next(item[2] for item in box.items if item[0] == "text" and item[3] == text)


def _size_of(box: m.Box, text: str) -> float:
    return next(item[4] for item in box.items if item[0] == "text" and item[3] == text)


def test_a_fraction_is_stacked_with_a_bar_between():
    box = layout("a/b")
    bar = next(item for item in box.items if item[0] == "line")
    assert _y_of(box, "a") < bar[2] < _y_of(box, "b")  # numerator above the bar, denominator below
    assert box.ascent > 20 * m.ASCENT and box.descent > 20 * m.DESCENT


def test_a_superscript_is_raised_and_smaller_and_a_subscript_lowered():
    box = layout("x^2")
    assert _y_of(box, "2") < _y_of(box, "x") and _size_of(box, "2") < _size_of(box, "x")
    box = layout("x_1")
    assert _y_of(box, "1") > _y_of(box, "x") and _size_of(box, "1") < _size_of(box, "x")


def test_a_radical_sign_covers_its_body():
    box = layout("sqrt(x + 1)")
    (path,) = [item for item in box.items if item[0] == "path"]
    points = path[1]
    assert points[-1][0] > max(item[1] for item in box.items if item[0] == "text")  # the bar reaches past the body
    assert min(y for _, y in points) < -20 * m.ASCENT * 0.9  # and rises above it


def test_an_integral_sign_is_larger_than_the_text():
    box = layout(r"\int x dx")
    assert _size_of(box, "∫") > _size_of(box, "x") * 1.4


def test_tall_brackets_grow_with_their_content():
    box = layout(r"\left(\frac{a}{b}\right)")
    assert sum(1 for item in box.items if item[0] == "path") == 2  # drawn to the fraction's height


def test_no_raw_notation_is_drawn_for_the_reference_formulas():
    for text in (r"d/dx(x^n) = n x^(n-1)", r"\int x^n dx = \frac{x^{n+1}}{n+1} + C",
                 r"d/dx(sin^-1 x) = 1/sqrt(1 - x^2)", r"\int \frac{1}{1+x^2} dx = \tan^{-1} x + C",
                 r"x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}", r"\frac{dy}{dx}", "x^2", r"\int x dx"):
        drawn = "".join(texts(layout(text)))
        for raw in RAW_NOTATION:
            assert raw not in drawn, (text, raw, drawn)


def test_the_svg_keeps_text_as_text():
    svg = m.to_svg(layout(r"\frac{dy}{dx} = \alpha x^2"))
    assert svg.startswith("<svg") and "<text" in svg and "<line" in svg and "α" in svg and "\\frac" not in svg


# ---------------------------------------------------------------- which lines are formulas


def test_answer_lines_that_hold_formulas():
    assert m.formula_parts("Equation: P = dW/dt") == ("Equation: ", "P = dW/dt")
    assert m.formula_parts("Formula: I = V / R") == ("Formula: ", "I = V / R")
    assert m.formula_parts(r"The slope is \frac{dy}{dx}") == ("", r"The slope is \frac{dy}{dx}")
    assert m.formula_parts("Definition: Resistance is the opposition to current.") is None


# ---------------------------------------------------------------- in the window


@pytest.fixture
def root(capsys):
    try:
        with capsys.disabled():
            window = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - a machine without a display
        pytest.skip(f"Tk cannot open a window here: {exc}")
    window.withdraw()
    yield window
    window.destroy()


def test_formulas_are_drawn_on_a_canvas_with_real_fonts(root):
    metrics = m.TkMetrics(root)
    canvas = tk.Canvas(root, width=600, height=200)
    box = m.layout(m.parse(r"\int_0^1 \frac{x^2}{\sqrt{1 - x^2}} dx"), 24, metrics)
    ids = m.draw_on_canvas(canvas, box, metrics, 5, 5 + box.ascent, "#000000")
    kinds = {canvas.type(item) for item in ids}
    assert kinds == {"text", "line"}
    drawn = "".join(canvas.itemcget(item, "text") for item in ids if canvas.type(item) == "text")
    assert "∫" in drawn and "\\" not in drawn and "^" not in drawn and "sqrt" not in drawn
    assert box.width > 0 and box.ascent > 24 * m.ASCENT


def test_the_answer_view_typesets_equation_lines(root, tmp_path):
    from app.ui.gui import answerview
    from app.ui.gui.window import RudraWindow

    window = RudraWindow(root, tmp_path, full=True, autostart=False)
    document = answerview.AnswerDocument("Which equations are associated with power?", (
        answerview.AnswerPart(1, "ANSWERED", ("Equation: P = dW/dt", "Equation: x^2 + y^2 = r^2"), (), False,
                              answerview.SourceDetails(basis=("K-00000001",), sources=("DOC-00000001 p.3: \"P = dW/dt\"",))),
    ))
    view = window.full.output
    view.show_answer(document)
    canvases = [widget for widget in view.embedded if isinstance(widget, tk.Canvas)]
    assert [canvas.formula_source for canvas in canvases] == ["P = dW/dt", "x^2 + y^2 = r^2"]
    shown = view.text_content()
    assert "dW/dt" not in shown and "x^2" not in shown  # the text view does not show the raw strings
    for canvas in canvases:
        assert canvas.find_all() and int(canvas.cget("width")) > 20
