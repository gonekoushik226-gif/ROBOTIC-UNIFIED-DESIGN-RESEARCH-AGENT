"""A calculation set out as a worked solution (the presentation of `solve` and `calculate`).

Defect found in manual testing: a calculation's steps and result were printed as linear
text ("I = 12 V ÷ 30 Ω = 0.4 A") and a stored equation as raw LaTeX ("f = \\frac{1}{T}"),
which is hard to read. The window now typesets them. These tests pin what is prepared for
the typesetter (nothing is recomputed: every number, unit and symbol is the command's own)
and what the window draws.
"""

from __future__ import annotations

import json
import tkinter as tk

import pytest

from app.ui.gui import answerview, mathrender, theme, worked
from app.ui.gui.window import RudraWindow


def value(displayed: str, unit: str = "", relation: str = "=", exact: str = "") -> dict:
    return {"displayed": displayed, "unit": unit, "relation": relation, "exact": exact or displayed,
            "dimension": [0] * 7}


SOLVE_TWO_STEPS = {
    "command": ["solve", "current", "--input", "V=12 V"],
    "detail": {
        "status": "CALCULATED", "verification": "VERIFIED",
        "target": {"symbol": "I", "asked": "current"},
        "quantities": [["I", "current"], ["V", "voltage"], ["R1", None]],
        "givens": [{"symbol": "V", "value": "12 V", "used": True},
                   {"symbol": "R1", "value": "10 Ω", "used": True},
                   {"symbol": "R2", "value": "20 Ω", "used": True},
                   {"symbol": "X", "value": "1 A", "used": False}],
        "result": value("0.4", "A", exact="2/5"),
        "steps": [
            {"number": 1, "symbol": "Rtotal", "formula": "Rtotal = R1 + R2", "rearranged": False,
             "stored_text": "Rtotal = R1 + R2", "substitution": "Rtotal = 10 Ω + 20 Ω",
             "result": value("30", "Ω")},
            {"number": 2, "symbol": "I", "formula": "I = V / Rtotal", "rearranged": False,
             "stored_text": "I = V / Rtotal", "substitution": "I = 12 V ÷ 30 Ω", "result": value("0.4", "A")},
        ],
    },
}


def test_a_solve_answer_becomes_a_worked_solution():
    result = worked.from_part(SOLVE_TWO_STEPS)
    assert result is not None
    assert (result.result.symbol, result.result.value, result.result.unit, result.result.name) == (
        "I", "0.4", "A", "current")
    assert [(g.symbol, g.value, g.name) for g in result.givens] == [
        ("V", "12 V", "voltage"), ("R1", "10 Ω", ""), ("R2", "20 Ω", "")]  # the unused one is not "given"
    assert [step.number for step in result.steps] == [1, 2]
    assert result.steps[0].equation == "Rtotal = R1 + R2"
    assert result.steps[0].working == r"Rtotal = 10\,\mathrm{Ω} + 20\,\mathrm{Ω} = 30\,\mathrm{Ω}"
    assert result.steps[1].working == r"I = \frac{12\,\mathrm{V}}{30\,\mathrm{Ω}} = 0.4\,\mathrm{A}"
    assert result.check_ok and result.check.startswith("Checked:")
    assert result.result.markup() == r"I = 0.4\,\mathrm{A}"


def test_a_rearranged_step_keeps_the_equation_the_document_states():
    part = json.loads(json.dumps(SOLVE_TWO_STEPS))
    part["detail"]["steps"] = [{"number": 1, "symbol": "T", "formula": "T = 1 / f", "rearranged": True,
                                "stored_text": r"f = \frac{1}{T}", "substitution": "T = 1 ÷ 50 Hz",
                                "result": value("0.02", "s")}]
    step = worked.from_part(part).steps[0]
    assert (step.equation, step.stored) == ("T = 1 / f", r"f = \frac{1}{T}")
    assert step.working == r"T = \frac{1}{50\,\mathrm{Hz}} = 0.02\,\mathrm{s}"


def test_a_rounded_result_says_so_and_carries_its_exact_value():
    part = json.loads(json.dumps(SOLVE_TWO_STEPS))
    part["detail"]["result"] = value("0.333333", "A", relation="≈", exact="1/3")
    result = worked.from_part(part).result
    assert (result.relation, result.value, result.exact) == ("≈", "0.333333", "1/3")
    assert result.markup() == r"I ≈ 0.333333\,\mathrm{A}"
    assert worked.from_part(SOLVE_TWO_STEPS).result.exact == ""  # an exact value needs no note


def test_a_failed_independent_check_is_shown_as_failed():
    part = json.loads(json.dumps(SOLVE_TWO_STEPS))
    part["detail"]["verification"] = "FAILED"
    result = worked.from_part(part)
    assert not result.check_ok and "FAILED" in result.check


def test_a_calculate_answer_becomes_a_worked_solution_too():
    part = {"command": ["calculate", "I"], "detail": {
        "status": "CALCULATED", "target": "I", "verification_status": "PENDING",
        "inputs": [{"symbol": "V", "text": "12 V", "origin": "USER_INPUT"},
                   {"symbol": "R", "text": "30 Ω", "origin": "USER_INPUT"}],
        "result": value("0.4", "A"),
        "steps": [{"number": 1, "symbol": "I", "text": "I = V / R", "substitution": "I = 12 V ÷ 30 Ω",
                   "result": value("0.4", "A")}]}}
    result = worked.from_part(part)
    assert [g.symbol for g in result.givens] == ["V", "R"]
    assert result.steps[0].equation == "I = V / R"
    assert "not independently verified" in result.check and result.check_ok


@pytest.mark.parametrize("raw", [
    {"command": ["query", "--name", "x"], "detail": SOLVE_TWO_STEPS["detail"]},
    {"command": ["solve", "I"], "detail": {"status": "CANNOT_DETERMINE"}},
    {"command": ["solve", "I"], "detail": None},
    {"command": ["solve", "I"], "detail": {"status": "CALCULATED", "result": {}, "target": {}}},
    {"command": [], "detail": {}},
])
def test_only_a_calculated_answer_has_a_worked_solution(raw):
    assert worked.from_part(raw) is None


@pytest.mark.parametrize("substitution, result, markup", [
    ("Rtotal = 10 Ω + 20 Ω", "30 Ω", r"Rtotal = 10\,\mathrm{Ω} + 20\,\mathrm{Ω} = 30\,\mathrm{Ω}"),
    ("E = 0.5 × 2 kg × (3 m)^2", "9 J",
     r"E = 0.5 \times 2\,\mathrm{kg} \times \left(3\,\mathrm{m}\right)^{2} = 9\,\mathrm{J}"),
    ("P = (12 V − 2 V) × 3 A", "30 W",
     r"P = \left(12\,\mathrm{V} − 2\,\mathrm{V}\right) \times 3\,\mathrm{A} = 30\,\mathrm{W}"),
    ("v = (10 m + 5 m) ÷ (2 s × 3)", "2.5 m",
     r"v = \frac{10\,\mathrm{m} + 5\,\mathrm{m}}{2\,\mathrm{s} \times 3} = 2.5\,\mathrm{m}"),
    ("x = 1.5e7 Hz ÷ 2", "7.5e6 Hz",
     r"x = \frac{1.5 \times 10^{7}\,\mathrm{Hz}}{2} = 7.5 \times 10^{6}\,\mathrm{Hz}"),
    ("Vout = 12 V × 20000 Ω ÷ (10000 Ω + 20000 Ω)", "8 V",
     r"Vout = \frac{12\,\mathrm{V} \times 20\,000\,\mathrm{Ω}}{10\,000\,\mathrm{Ω} + 20\,000\,\mathrm{Ω}} = 8\,\mathrm{V}"),
    ("y = -5 V × 2", "-10 V", r"y = -5\,\mathrm{V} \times 2 = -10\,\mathrm{V}"),
    ("z = (1 ÷ 4)^2", "0.0625", r"z = \left(\frac{1}{4}\right)^{2} = 0.0625"),
    ("t = 3 s^-1", "3 s", r"t = 3\,\mathrm{s}^{-1} = 3\,\mathrm{s}"),
])
def test_a_substitution_is_set_as_the_textbook_writes_it(substitution, result, markup):
    assert worked.substitution_markup(substitution, result) == markup


def test_digits_are_grouped_only_in_the_picture_and_never_changed():
    assert worked.value_markup("1500 Ω") == r"1500\,\mathrm{Ω}"          # four digits stay together
    assert worked.value_markup("20000 Ω") == r"20\,000\,\mathrm{Ω}"
    assert worked.value_markup("1234567.25 W") == r"1\,234\,567.25\,\mathrm{W}"
    assert worked.value_markup("0.0001 F") == r"0.0001\,\mathrm{F}"


@pytest.mark.parametrize("text", ["garbage ???", "no equals sign", "a = b +", "a = (1 + 2", "a = 1 ÷"])
def test_what_is_not_in_the_engines_grammar_is_shown_as_written(text):
    assert worked.substitution_markup(text, "") == text
    assert worked.substitution_markup(text, "5 V") == text + " = 5 V"


def test_the_markup_reads_back_to_the_same_numbers_and_units():
    """Typesetting changes how a value looks, never what it is."""
    markup = worked.substitution_markup("Vout = 12 V × 20000 Ω ÷ (10000 Ω + 20000 Ω)", "8 V")
    shown = mathrender.rendered_text(mathrender.parse(markup))
    for needle in ("12", "V", "20", "000", "Ω", "×", "8"):
        assert needle in shown
    assert "\\" not in shown and "{" not in shown


# ---------------------------------------------------------------- the answer view and window


def raw_part(**fields) -> dict:
    base = {"number": 1, "status": "ANSWERED", "intent": "CALCULATE", "answer": "I = 0.4 A",
            "command": SOLVE_TWO_STEPS["command"], "detail": SOLVE_TWO_STEPS["detail"],
            "calculation": ["Step 1: Rtotal = R1 + R2  →  Rtotal = 10 Ω + 20 Ω = 30 Ω",
                            "Step 2: I = V / Rtotal  →  I = 12 V ÷ 30 Ω = 0.4 A", "Checked: yes"],
            "reasoning": ["Asked for I (current) from V (voltage) = 12 V.", "Chose 2 equation(s)."]}
    base.update(fields)
    return base


def test_the_plain_lines_stay_for_copying_and_the_window_leaves_out_what_the_solution_shows():
    part = answerview._part(raw_part())
    assert part.worked is not None
    assert part.lines == ("I = 0.4 A",)
    assert [title for title, _ in part.extras] == ["Steps", "Reasoning"]  # still there to copy
    assert part.shown_extras() == (("Reasoning", ("Chose 2 equation(s).",)),)  # the window does not repeat them
    text = answerview.plain_text(answerview.AnswerDocument("q", (part,)))
    assert "Step 2: I = V / Rtotal" in text and "I = 0.4 A" in text


def test_an_answer_that_is_not_a_calculation_is_untouched():
    part = answerview._part({"number": 1, "status": "ANSWERED", "answer": "Definition: X is Y.", "command": ["query"]})
    assert part.worked is None and part.shown_extras() == part.extras == ()


@pytest.fixture
def window(tmp_path, capsys):
    try:
        with capsys.disabled():
            root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - a machine without a display
        pytest.skip(f"Tk cannot open a window here: {exc}")
    root.withdraw()
    built = RudraWindow(root, tmp_path, full=True, autostart=False)
    yield built
    root.destroy()


def canvases(view):
    return [widget for widget in view.embedded if isinstance(widget, tk.Canvas)]


def drawn(canvas: tk.Canvas) -> str:
    return "".join(canvas.itemcget(item, "text") for item in canvas.find_all() if canvas.type(item) == "text")


def test_the_window_shows_a_calculation_as_typeset_mathematics(window):
    view = window.full.output
    view.show_answer(answerview.AnswerDocument("Find the current", (answerview._part(raw_part()),)))
    sources = [canvas.formula_source for canvas in canvases(view)]
    assert sources[0] == r"I = 0.4\,\mathrm{A}"                      # the result, first and on its own card
    assert r"I = \frac{12\,\mathrm{V}}{30\,\mathrm{Ω}} = 0.4\,\mathrm{A}" in sources
    assert "I = V / Rtotal" in sources and "Rtotal = R1 + R2" in sources
    assert len(sources) == 1 + 3 + 4                                  # result, three givens, two steps of two lines
    shown = view.text_content()
    for raw_notation in ("\\", "÷", "Step 1: ", "→"):
        assert raw_notation not in shown
    for heading in ("Result", "Given", "Working"):
        assert theme.spaced(heading) in shown
    assert "Step 1" in shown and "Step 2" in shown
    assert "Checked: an independent evaluation" in shown
    assert "Asked for" not in shown                                   # the givens already say it
    assert "Chose 2 equation(s)." in shown
    # every picture is made of real glyphs and bars, never of notation
    for canvas in canvases(view):
        text = drawn(canvas)
        assert canvas.find_all() and "\\" not in text and "{" not in text and "÷" not in text
    result_card = canvases(view)[0]
    assert int(result_card.cget("highlightthickness")) == 1            # the result stands out as a card


def test_a_stored_equation_in_the_sources_is_typeset(window):
    view = window.full.output
    part = answerview._part(raw_part(
        sources=['K-00000005: the stored equation "f = \\frac{1}{T}"',
                 'DOC-00000001 p.1: "f = \\frac{1}{T}"', 'DOC-00000001 p.2: "V = I R"'],
        basis=["K-00000005"], calculation=[]))
    view.show_answer(answerview.AnswerDocument("q", (part,)))
    before = len(canvases(view))
    view.toggle_sources(1)
    after = [canvas.formula_source for canvas in canvases(view)][before:]
    assert after.count(r"f = \frac{1}{T}") == 2     # the stored equation, and the typeset form of the LaTeX quotation
    shown = view.text_content()
    assert 'DOC-00000001 p.1: "f = \\frac{1}{T}"' in shown   # the quotation itself stays exactly as the document has it
    assert 'the stored equation "' not in shown              # the stored equation is shown typeset, not as notation
    assert 'DOC-00000001 p.2: "V = I R"' in shown and after.count("V = I R") == 0


def test_a_sentence_with_fenced_latex_is_typeset_in_place(window):
    view = window.full.output
    part = answerview._part({"number": 1, "status": "ANSWERED", "command": ["query"],
                             "answer": "Definition: The speed is \\(v = \\frac{d}{t}\\) in metres per second."})
    view.show_answer(answerview.AnswerDocument("q", (part,)))
    assert [canvas.formula_source for canvas in canvases(view)] == [r"v = \frac{d}{t}"]
    shown = view.text_content()
    assert "Definition: The speed is" in shown and "in metres per second." in shown
    assert "\\(" not in shown and "\\frac" not in shown


def test_the_missing_formula_of_an_undetermined_calculation_is_typeset(window):
    view = window.full.output
    part = answerview._part({"number": 1, "status": "CANNOT_DETERMINE", "command": ["solve", "P"],
                             "answer": "I cannot determine this.", "missing": ["P = V * I would give it, and needs I"]})
    view.show_answer(answerview.AnswerDocument("q", (part,)))
    assert [canvas.formula_source for canvas in canvases(view)] == ["P = V * I"]
    assert "would give it, and needs I" in view.text_content()


def test_the_compact_window_fits_a_calculation_to_its_width(window, monkeypatch):
    window.show(full=False)
    view = window.compact.output
    monkeypatch.setattr(view.text, "winfo_width", lambda: 440)  # the assistant's width; the test window is hidden
    long_working = {"number": 1, "symbol": "Vout", "formula": "Vout = Vin * R2 / (R1 + R2)", "rearranged": False,
                    "stored_text": "x", "result": value("8", "V"),
                    "substitution": "Vout = 12 V × 20000 Ω ÷ (10000 Ω + 20000 Ω)"}
    detail = json.loads(json.dumps(SOLVE_TWO_STEPS["detail"]))
    detail["steps"] = [long_working]
    view.show_answer(answerview.AnswerDocument("q", (answerview._part(raw_part(detail=detail)),)))
    assert canvases(view)
    for canvas in canvases(view):
        assert int(canvas.cget("width")) <= 440, (canvas.formula_source, canvas.cget("width"))


# ---------------------------------------------------------------- stored equations that disagree


CONFLICTING = {
    "command": ["solve", "V", "--input", "I=2 A"],
    "detail": {
        "status": "CONFLICTING", "target": {"symbol": "V", "asked": "V"}, "quantities": [["V", "voltage"]],
        "routes": [
            {"agrees": False, "equations": ["V = I * R (K-00000002)"], "problem": "", "value": value("10", "V")},
            {"agrees": False, "equations": ["V = 2 * I * R (K-00000003)"], "problem": "", "value": value("20", "V")},
        ],
    },
}


def test_equations_that_give_different_values_are_set_side_by_side_and_none_is_chosen():
    conflict = worked.conflict_from_part(CONFLICTING)
    assert conflict is not None and conflict.symbol == "V"
    assert [(route.letter, route.equations, route.result.value) for route in conflict.routes] == [
        ("A", ("V = I * R",), "10"), ("B", ("V = 2 * I * R",), "20")]  # the identifiers are provenance, not maths
    assert conflict.route_markup(conflict.routes[0]) == r"V = I * R \quad \Rightarrow \quad V = 10\,\mathrm{V}"
    assert worked.from_part(CONFLICTING) is None   # a conflict is not a worked solution


@pytest.mark.parametrize("raw", [
    {"command": ["solve", "V"], "detail": {"status": "CALCULATED"}},
    {"command": ["calculate", "V"], "detail": CONFLICTING["detail"]},
    {"command": ["solve", "V"], "detail": {**CONFLICTING["detail"], "routes": CONFLICTING["detail"]["routes"][:1]}},
    {"command": ["solve", "V"], "detail": {**CONFLICTING["detail"], "routes": [{"equations": [], "value": {}}] * 2}},
])
def test_only_a_solve_answer_with_two_routes_is_a_conflict(raw):
    assert worked.conflict_from_part(raw) is None


def test_the_window_typesets_the_disagreeing_routes(window):
    view = window.full.output
    part = answerview._part({"number": 1, "status": "CANNOT_DETERMINE", "intent": "CALCULATE",
                             "answer": "I cannot determine this. No value is asserted and none was chosen.",
                             "command": CONFLICTING["command"], "detail": CONFLICTING["detail"],
                             "conflicts": ["Conflict detected: stored equations give different values for V."]})
    view.show_answer(answerview.AnswerDocument("q", (part,)))
    sources = [canvas.formula_source for canvas in canvases(view)]
    assert sources == [r"V = I * R \quad \Rightarrow \quad V = 10\,\mathrm{V}",
                       r"V = 2 * I * R \quad \Rightarrow \quad V = 20\,\mathrm{V}"]
    shown = view.text_content()
    assert theme.spaced("Conflict") in shown and "Route A" in shown and "Route B" in shown
    assert "No value was chosen." in shown
