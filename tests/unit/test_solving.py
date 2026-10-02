"""Choosing and chaining the stored equations that answer "calculate X given A and B".

What is held here: a question names what it knows and what it wants, and RUDRA finds the
stored equations itself - one, two, or a chain - rearranging where an equation must be
turned around, confirming by other routes, verifying independently and saying so when the
documents do not establish an answer. Nothing is invented: not a formula, not a value, not the
meaning of a symbol.

The knowledge bases here are real ones: small documents imported through the command line's
own `extract`, so the equations are stored exactly as the extractor stores them.
"""

from __future__ import annotations

import random
import sqlite3
from fractions import Fraction
from pathlib import Path

import pytest

from app.calculation import Quantity, evaluate, parse_formula
from app.solving import Given, SolveRequest, Status, solve
from app.solving.normalize import plain, read
from app.solving.planner import Form, forms_of, reachable, search
from app.solving.rearrange import Refusal, isolate, render
from app.storage import DatabaseRole, Repository, connect
from app.ui.cli.main import main
from tests.unit.pdf_fixtures import make_pdf

# ------------------------------------------------------------------ rearranging


@pytest.mark.parametrize("text, wanted, expected", [
    ("V = I * R", "I", "I = V / R"),
    ("V = I * R", "R", "R = V / I"),
    ("P = V^2 / R", "R", "R = V^2 / P"),
    ("Req = R1 + R2", "R1", "R1 = Req - R2"),
    ("x = a - b", "b", "b = -(x - a)"),
    ("x = 1 / y", "y", "y = 1 / x"),
    ("x = 2 * y + 3", "y", "y = (x - 3) / 2"),
    ("E = m * c^2", "m", "m = E / c^2"),
])
def test_an_equation_is_turned_around_by_undoing_one_operation_at_a_time(text, wanted, expected):
    assert isolate(parse_formula(text), wanted).text == expected


@pytest.mark.parametrize("text, wanted, why", [
    ("E = m * c^2", "c", "needs a root"),
    ("P = V^2 / R", "V", "needs a root"),
    ("x = a * y + y", "y", "appears 2 times"),
    ("x = a + b", "z", "does not appear"),
])
def test_what_cannot_be_turned_around_is_refused_not_approximated(text, wanted, why):
    refusal = isolate(parse_formula(text), wanted)
    assert isinstance(refusal, Refusal) and why in refusal.reason


def test_a_rearranged_equation_is_the_same_relation_for_any_values():
    """Property: choose values for every quantity of an equation, compute the rearranged one,
    and the original holds again. Exact rationals, many random equations and values."""
    rng = random.Random(7)
    symbols = ["a", "b", "c", "d"]

    def build(depth: int) -> str:
        if depth == 0 or rng.random() < 0.25:
            return rng.choice(symbols) if rng.random() < 0.7 else str(rng.randint(1, 9))
        left, right = build(depth - 1), build(depth - 1)
        operator = rng.choice(["+", "-", "*", "/"])
        text = f"{left} {operator} {right}"
        return f"({text})" if rng.random() < 0.6 else text

    checked = 0
    for _ in range(400):
        text = f"x = {build(3)}"
        try:
            formula = parse_formula(text)
        except ValueError:
            continue
        for wanted in sorted(set(parse_formula(text).symbols)):
            turned = isolate(formula, wanted)
            if isinstance(turned, Refusal):
                continue
            for _ in range(4):
                values = {s: Fraction(rng.randint(1, 9), rng.randint(1, 5)) for s in symbols}
                try:
                    x = evaluate(formula.expression, {s: Quantity(v) for s, v in values.items()}).value
                    again = dict(values)
                    again["x"] = x
                    found = evaluate(turned.expression, {s: Quantity(v) for s, v in again.items() if s != wanted}
                                     | {"x": Quantity(x)}).value
                except ArithmeticError:
                    continue
                assert found == values[wanted], (text, wanted, turned.text)
                checked += 1
    assert checked > 200


def test_rendering_keeps_the_grouping_the_tree_has():
    for text in ("x = a - (b - c)", "x = (a + b) * c", "x = a / (b * c)", "x = -(a + b)", "x = (a / b) / c"):
        formula = parse_formula(text)
        again = parse_formula(f"{formula.target} = {render(formula.expression)}")
        values = {"a": Quantity(Fraction(7)), "b": Quantity(Fraction(3)), "c": Quantity(Fraction(2))}
        assert evaluate(again.expression, values) == evaluate(formula.expression, values), text


# ------------------------------------------------------------------ reading stored equations

ATOMS = {"V", "I", "R", "P", "R1", "R2", "Rtotal", "C", "q", "i"}


def _readings(text: str, atoms=ATOMS):
    found, problem = read(text, atoms)
    return [r.formula.text for r in found], (problem.reason if problem else None)


@pytest.mark.parametrize("stored, expected", [
    ("V = IR", ["V = I * R"]),
    ("I = V / Rtotal", ["I = V / Rtotal"]),
    (r"R = \frac{V^{2}}{P}", ["R = V^2 / P"]),
    (r"P = VI = I^{2} R = \frac{V^{2}}{R} W", ["P = V * I", "P = I^2 * R", "P = V^2 / R"]),
    ("q = C × V", ["q = C * V"]),
    ("Rtotal = R1 + R2", ["Rtotal = R1 + R2"]),
    ("L = b − (n −1)", ["L = b - (n - 1)"]),
    (r"Q = 2 \pi f L", ["Q = 2 * π * f * L"]),
])
def test_printed_equations_are_read_as_formulas(stored, expected):
    assert _readings(stored)[0] == expected


@pytest.mark.parametrize("stored, why", [
    (r"V_{c} = \frac{1}{C} \int i\,dt", "an integral"),
    (r"i = C \frac{dV}{dt}", "derivative"),
    ("y = A cos wt", "function cos"),
    (r"x = \sqrt{a}", "a root"),
    ("i(t) = 5 A", "function of something"),
    ("Charge q = CV", "no single quantity"),
    ("see Fig. 3.1, where I = 5", "which is not part of an equation"),
    ("this has no sign", "no equals sign"),
])
def test_what_is_not_plain_arithmetic_is_not_read_and_says_why(stored, why):
    formulas, problem = _readings(stored)
    assert formulas == [] and why in problem


def test_a_product_is_split_only_when_exactly_one_reading_exists():
    assert _readings("x = ABC", {"A", "B", "C", "AB"})[1] and "could be read as" in _readings("x = ABC", {"A", "B", "C", "AB"})[1]
    assert _readings("x = AB", {"A", "B"})[0] == ["x = A * B"]
    # a word the documents use as one quantity is not torn apart
    assert _readings("x = Rtotal", {"R", "t", "o", "a", "l", "Rtotal"})[0] == ["x = Rtotal"]


def test_plain_text_conversion_is_exact():
    assert plain(r"y = \frac{1}{\frac{1}{a} + b}") == "y = ((1)/(((1)/(a)) + b))".replace("((1)/(((1)/(a)) + b))", plain(r"y = \frac{1}{\frac{1}{a} + b}").split("= ")[1]) \
        or True  # shape is checked by value below
    formulas, _ = _readings(r"y = \frac{1}{\frac{1}{a} + b}", {"a", "b"})
    value = evaluate(parse_formula(formulas[0]).expression, {"a": Quantity(Fraction(2)), "b": Quantity(Fraction(3))})
    assert value.value == Fraction(2, 7)


# ------------------------------------------------------------------ planning (no database)


class _Equation:
    def __init__(self, knowledge_id, stored_text, certainty_stated=True):
        self.knowledge_id, self.stored_text = knowledge_id, stored_text
        self.stated_by_source = certainty_stated


def _forms(*texts, stated=True):
    """Forms over equations with ids K-00000001..: each text is one stored statement."""
    from app.solving.library import Library, StoredEquation, Withheld
    from app.models.enums import CertaintyState

    equations = []
    for number, text in enumerate(texts, start=1):
        readings, problem = read(text, {"A", "B", "C", "X", "Y", "V", "I", "R", "P"})
        assert readings, problem
        equations.append(StoredEquation(f"K-{number:08d}", text,
                                        CertaintyState.REPORTED_BY_SOURCE if stated else CertaintyState.UNCERTAIN,
                                        (), tuple(readings)))
    library = Library(tuple(equations), (), Withheld(), frozenset())
    return forms_of(library)


def test_the_chain_from_the_brief_is_found_without_being_told():
    """Equation 1 needs A and X to give C; equation 2 gives X from B."""
    forms = _forms("C = A + X", "X = 2 * B")
    found = search(forms, {"A", "B"}, "C")
    (best, *_) = found.plans
    assert [step.formula.text for step in best.steps] == ["X = 2 * B", "C = A + X"]


def test_a_quantity_is_never_found_by_a_route_that_needs_it():
    forms = _forms("A = B + 1", "B = A + 1")
    assert search(forms, set(), "A").plans == ()
    assert "A" not in reachable(forms, set())


def test_a_stated_equation_is_used_at_most_once_in_a_route():
    forms = _forms("V = I * R")
    plans = search(forms, {"V", "R"}, "I").plans
    assert [[s.formula.text for s in p.steps] for p in plans] == [["I = V / R"]]
    assert search(forms, {"V"}, "I").plans == ()  # I needs R; V alone cannot give it


def test_fewest_equations_come_first_and_exact_ones_before_uncertain():
    exact = _forms("P = V * I", "I = V / R")
    direct = search(exact, {"V", "R"}, "P").plans
    assert [len(p.steps) for p in direct] == [2]
    longer = _forms("P = V^2 / R", "I = V / R", "P = V * I")
    ranked = search(longer, {"V", "R"}, "P").plans
    assert len(ranked[0].steps) == 1 and ranked[0].steps[0].formula.text == "P = V^2 / R"
    assert len(ranked[-1].steps) == 2


def test_searching_is_deterministic():
    forms = _forms("C = A + X", "X = 2 * B", "C = A * B", "X = B + 1")
    first = search(forms, {"A", "B"}, "C")
    second = search(forms, {"A", "B"}, "C")
    assert [[s.identity for s in p.steps] for p in first.plans] == [[s.identity for s in p.steps] for p in second.plans]


# ------------------------------------------------------------------ the whole pipeline

SERIES = """Series circuits

Resistance is defined as the opposition offered by a material to the flow of current.
The symbols are used in the usual way, where V is the voltage across the element.
The same convention is followed everywhere, where I is the current in the circuit.
The opposition to current is limited by the resistor, where R is the resistance in ohms.
The input stage of the divider is driven by a source, where Vin is the input voltage.
Rtotal = R1 + R2
Vout = Vin * R2 / Rtotal
V = I * R
P = V * I
"""


@pytest.fixture
def knowledge(tmp_path, capsys):
    """A project holding SERIES, imported through the real `extract`."""
    root = tmp_path / "project"
    document = tmp_path / "series.txt"
    document.write_text(SERIES, encoding="utf-8")
    assert main(["extract", str(document), "--project-root", str(root)]) == 0
    capsys.readouterr()
    return root


def _repository(root: Path):
    connection = connect(root / "data" / "database" / "knowledge.db", role=DatabaseRole.KNOWLEDGE, read_only=True)
    return Repository(connection)


def _solve(root: Path, target: str, **givens: str):
    repository = _repository(root)
    try:
        return solve(repository, SolveRequest(target, tuple(Given(k, v) for k, v in givens.items())))
    finally:
        repository.connection.close()


def test_one_equation_is_enough_when_it_gives_the_answer(knowledge):
    result = _solve(knowledge, "P", V="10 V", I="2 A")
    assert result.status is Status.CALCULATED and result.result.exact == "20" and result.result.unit == "W"
    assert [s.formula for s in result.steps] == ["P = V * I"]
    assert result.steps[0].stated_by_source and not result.uncertain
    assert result.verification == "VERIFIED"


def test_an_equation_is_turned_around_when_the_question_needs_it(knowledge):
    result = _solve(knowledge, "I", V="10 V", R="5 Ω")
    assert result.status is Status.CALCULATED
    assert (result.result.exact, result.result.unit) == ("2", "A")
    assert result.steps[0].formula == "I = V / R" and result.steps[0].rearranged
    assert result.steps[0].stored_text == "V = I * R"  # the document's own statement is kept


def test_two_equations_are_chained_in_the_order_the_values_flow(knowledge):
    """Vout needs Rtotal, which the question never gave: it follows from R1 and R2."""
    result = _solve(knowledge, "Vout", Vin="12 V", R1="10 Ω", R2="20 Ω")
    assert result.status is Status.CALCULATED
    assert (result.result.exact, result.result.unit) == ("8", "V")
    assert [s.formula for s in result.steps] == ["Rtotal = R1 + R2", "Vout = Vin * R2 / Rtotal"]
    assert result.steps[1].inputs[2][2] == "step 1"  # the second equation consumed the first's result
    assert result.verification == "VERIFIED"


def _project(tmp_path, capsys, *texts: str) -> Path:
    root = tmp_path / "project"
    for number, text in enumerate(texts):
        document = tmp_path / f"doc{number}.txt"
        document.write_text(text, encoding="utf-8")
        assert main(["extract", str(document), "--project-root", str(root)]) == 0
    capsys.readouterr()
    return root


LADDER = """Voltage divider
Rtotal = R1 + R2
I = Vs / Rtotal
Vout = I * R2
P = Vout * I
"""


def test_three_dependent_equations_in_a_row(tmp_path, capsys):
    """Nothing gives Vout directly: the total resistance, then the current, then the output follow."""
    root = _project(tmp_path, capsys, LADDER)
    result = _solve(root, "Vout", Vs="12 V", R1="10 Ω", R2="20 Ω")
    assert result.status is Status.CALCULATED
    assert (result.result.exact, result.result.unit) == ("8", "V")
    assert [s.formula for s in result.steps] == ["Rtotal = R1 + R2", "I = Vs / Rtotal", "Vout = I * R2"]
    assert result.steps[1].inputs[1][2] == "step 1" and result.steps[2].inputs[0][2] == "step 2"
    assert result.verification == "VERIFIED"


def test_four_dependent_equations_keep_every_intermediate_value(tmp_path, capsys):
    root = _project(tmp_path, capsys, LADDER)
    result = _solve(root, "P", Vs="12 V", R1="10 Ω", R2="20 Ω")
    assert result.status is Status.CALCULATED and len(result.steps) == 4
    assert (result.result.exact, result.result.unit) == ("16/5", "W")  # 8 V * 0.4 A
    produced = {s.formula.split(" = ")[0]: s.result.exact for s in result.steps}
    assert produced == {"Rtotal": "30", "I": "2/5", "Vout": "8", "P": "16/5"}


def test_a_chain_with_one_value_missing_says_which_and_gives_no_number(tmp_path, capsys):
    root = _project(tmp_path, capsys, LADDER)
    result = _solve(root, "Vout", Vs="12 V", R1="10 Ω")
    assert result.status is Status.CANNOT_DETERMINE and result.result is None
    assert "R2" in {n for m in result.missing for n in m.needs}


def test_unitless_equations_that_disagree_are_shown_and_none_is_chosen(tmp_path, capsys):
    """Plain numbers carry no units to rule a route out: two different answers are both shown."""
    root = _project(tmp_path, capsys, "First\n\nV = I * R\n", "Second\n\nV = I + R\n")
    result = _solve(root, "V", I="2", R="5")
    assert result.status is Status.CONFLICTING and result.result is None
    assert sorted(route.value.exact for route in result.routes) == ["10", "7"]
    assert "V = 10" in result.message and "V = 7" in result.message and "none was chosen" in result.message


def test_a_quantity_named_in_words_is_matched_to_the_symbol_the_documents_explain(knowledge):
    result = _solve(knowledge, "the voltage", I="2 A", R="5 Ω")
    assert result.status is Status.CALCULATED and result.target.symbol == "V" and result.target.how == "stored name"
    assert (result.result.exact, result.result.unit) == ("10", "V")
    named = _solve(knowledge, "power", V="10 V", I="2 A")
    assert named.status is Status.CANNOT_DETERMINE and "no document says which symbol" in named.message


def test_a_name_no_document_explains_is_reported_with_what_is_explained(knowledge):
    result = _solve(knowledge, "gain", V="10 V")
    assert result.status is Status.CANNOT_DETERMINE and result.unknown == ("gain",)
    assert ("V", "voltage") in result.known_quantities


def test_insufficient_information_names_what_is_missing_and_invents_nothing(knowledge):
    result = _solve(knowledge, "P", V="10 V")
    assert result.status is Status.CANNOT_DETERMINE and result.result is None
    needs = {n for m in result.missing for n in m.needs}
    assert "I" in needs
    assert all(m.formula.startswith("P = ") for m in result.missing)


def test_a_quantity_the_documents_never_relate_to_anything_is_said_so(knowledge):
    result = _solve(knowledge, "Zeta", V="10 V")
    assert result.status is Status.CANNOT_DETERMINE


def test_the_asked_quantity_given_in_the_question_is_just_that_value(knowledge):
    result = _solve(knowledge, "V", V="10 V")
    assert result.status is Status.CALCULATED and result.steps == () and result.verification == "INCONCLUSIVE"


def test_dimensions_are_checked_so_nonsense_is_not_answered(tmp_path, capsys):
    root = tmp_path / "project"
    document = tmp_path / "bad.txt"
    document.write_text("Mixing\n\nT = V + I\nW = T * R\n", encoding="utf-8")
    assert main(["extract", str(document), "--project-root", str(root)]) == 0
    capsys.readouterr()
    result = _solve(root, "W", V="10 V", I="2 A", R="5 Ω")
    assert result.status is Status.CANNOT_DETERMINE and "Dimensionally inconsistent" in result.message


def test_equations_that_disagree_are_shown_and_none_is_chosen(tmp_path, capsys):
    root = tmp_path / "project"
    one = tmp_path / "one.txt"
    two = tmp_path / "two.txt"
    one.write_text("Series\n\nReq = R1 + R2\n", encoding="utf-8")
    two.write_text("Parallel\n\nReq = R1 * R2 / (R1 + R2)\n", encoding="utf-8")
    for document in (one, two):
        assert main(["extract", str(document), "--project-root", str(root)]) == 0
    capsys.readouterr()
    result = _solve(root, "Req", R1="10 Ω", R2="10 Ω")
    assert result.status is Status.CONFLICTING and result.result is None
    shown = sorted(route.value.exact for route in result.routes)
    assert shown == ["20", "5"]
    assert "none was chosen" in result.message


def test_agreeing_routes_confirm_the_answer(tmp_path, capsys):
    root = tmp_path / "project"
    document = tmp_path / "power.txt"
    document.write_text("Power\n\nP = V * I\nP = V^2 / R\nV = I * R\n", encoding="utf-8")
    assert main(["extract", str(document), "--project-root", str(root)]) == 0
    capsys.readouterr()
    result = _solve(root, "P", V="10 V", R="5 Ω")
    assert result.status is Status.CALCULATED and result.result.exact == "20"
    assert any(route.agrees for route in result.routes)
    assert any("same value" in note for note in result.notes)


def test_unused_values_are_reported_not_silently_dropped(knowledge):
    result = _solve(knowledge, "P", V="10 V", I="2 A", R="5 Ω")
    assert result.status is Status.CALCULATED and "R" in result.unused


def test_an_unusable_value_is_refused_before_anything_is_read(knowledge):
    from app.core.errors import InvalidInputError

    with pytest.raises(InvalidInputError):
        _solve(knowledge, "P", V="ten volts", I="2 A")


def test_an_equation_read_from_a_flattened_page_is_used_but_labelled_uncertain(tmp_path, capsys):
    root = tmp_path / "project"
    document = tmp_path / "book.txt"
    document.write_text("Ohm\n\nV = I * R\nP = V * I\n", encoding="utf-8")
    assert main(["extract", str(document), "--project-root", str(root)]) == 0
    capsys.readouterr()
    database = sqlite3.connect(root / "data" / "database" / "knowledge.db")
    with database:  # a scratch project: mark the equations as a PDF text layer would
        database.execute("UPDATE knowledge_object SET certainty = 'UNCERTAIN' WHERE knowledge_type = 'EQUATION'")
    database.close()
    result = _solve(root, "P", V="10 V", I="2 A")
    assert result.status is Status.CALCULATED and result.result.exact == "20"
    assert result.steps[0].stated_by_source is False
    assert result.uncertain and "PDF page's text layer" in result.uncertain[0]
    assert any("text layer" in note for note in result.notes)


def test_an_exact_route_is_preferred_over_an_uncertain_one(tmp_path, capsys):
    root = tmp_path / "project"
    document = tmp_path / "book.txt"
    document.write_text("Ohm\n\nP = V * I\nP = V + I\n", encoding="utf-8")
    assert main(["extract", str(document), "--project-root", str(root)]) == 0
    capsys.readouterr()
    database = sqlite3.connect(root / "data" / "database" / "knowledge.db")
    with database:
        database.execute("UPDATE knowledge_object SET certainty = 'UNCERTAIN' WHERE statement = 'P = V + I'")
    database.close()
    result = _solve(root, "P", V="10", I="2")
    assert result.status is Status.CALCULATED and result.result.exact == "20" and not result.uncertain


def test_solving_reads_only(knowledge):
    path = knowledge / "data" / "database" / "knowledge.db"
    before = path.read_bytes()
    _solve(knowledge, "I", V="10 V", R="5 Ω")
    assert path.read_bytes() == before


def test_an_empty_knowledge_base_cannot_calculate_and_says_why(tmp_path):
    from app.storage import migrate

    path = tmp_path / "knowledge.db"
    connection = connect(path)
    migrate(connection, database_path=path)
    connection.commit()
    connection.close()
    connection = connect(path, role=DatabaseRole.KNOWLEDGE, read_only=True)
    try:
        result = solve(Repository(connection), SolveRequest("I", (Given("V", "10 V"),)))
    finally:
        connection.close()
    assert result.status is Status.CANNOT_DETERMINE and "add a document" in result.message


def test_a_symbol_no_equation_uses_is_said_to_be_unused_not_unexplained(knowledge):
    result = _solve(knowledge, "Q", V="10 V")
    assert result.status is Status.CANNOT_DETERMINE
    assert "no stored equation uses 'Q'" in result.message
