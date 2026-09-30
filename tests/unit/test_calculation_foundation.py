"""Phase 11 Batch A: the calculation foundation (ADRs 0041-0043).

Exact numbers and their display (ADR 0042 P11-13, P11-14), SI units and dimensions
(P11-15, P11-16), the formula grammar (P11-12; N1), evaluation of one expression, and
the structured request's checks (ADR 0043 P11-28; N2).

**Every expected value is worked by hand** in the comments beside it, never computed by
the code under test (section 167). No database is involved: nothing here opens,
reads or writes one.
"""

from __future__ import annotations

import ast
from fractions import Fraction
from pathlib import Path

import pytest

from app.calculation import (
    CalculationAssumption,
    CalculationInput,
    CalculationRequest,
    CalculationScope,
    ConversionError,
    EvaluationError,
    EvaluationProblem,
    FormulaError,
    FormulaProblem,
    NumberError,
    UnitError,
    UnitProblem,
    coherent_unit,
    display,
    evaluate,
    parse_formula,
    parse_number,
    parse_quantity,
    parse_unit,
    quantity,
    to_unit,
)
from app.calculation.formulas import MAX_DEPTH, MAX_EXPONENT, MAX_FORMULA_LENGTH, Negate, Power, Product, Sum, Symbol
from app.core.errors import FailureCategory, InvalidInputError
from tests.conftest import PROJECT_ROOT
from tests.unit.test_import_boundaries import ALLOWED

OHM = "Ω"  # the canonical ohm symbol


def _value(text: str, **bindings: str) -> Fraction:
    """Evaluate `y = text` with each binding given as value text, e.g. R="10 Ω"."""
    quantities = {name: parse_quantity(value) for name, value in bindings.items()}
    return evaluate(parse_formula(f"y = {text}").expression, quantities).value


# ------------------------------------------------------------------ exact numbers


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.1", Fraction(1, 10)),  # exactly one tenth, not the float 0.1000000000000000055...
        ("1e-3", Fraction(1, 1000)),
        ("2.5E3", Fraction(2500)),
        ("-4", Fraction(-4)),
        ("−4", Fraction(-4)),  # U+2212 MINUS SIGN
        ("007", Fraction(7)),
    ],
)
def test_a_decimal_literal_is_read_exactly(text, expected):
    assert parse_number(text) == expected


@pytest.mark.parametrize("text", ["", "1.", ".5", "1e", "0x10", "1_000", "nan", "inf", "1/3", "1,5", " 1", "٣"])
def test_anything_but_a_decimal_literal_is_refused(text):
    with pytest.raises(NumberError):
        parse_number(text)


def test_exact_arithmetic_has_no_floating_point_error():
    # 0.1 + 0.2 = 1/10 + 2/10 = 3/10 exactly (the float sum would be 0.30000000000000004).
    assert _value("0.1 + 0.2") == Fraction(3, 10)


# ------------------------------------------------------------------------ display


@pytest.mark.parametrize(
    ("value", "text", "exact"),
    [
        (Fraction(30), "30", True),  # section 203: Rtotal = 30 Ω
        (Fraction(1, 3), "0.333333", False),  # section 203: I ≈ 0.333333 A
        (Fraction(2, 3), "0.666667", False),  # 0.6666666... rounds up at the sixth figure
        (Fraction(-1, 3), "-0.333333", False),
        (Fraction(123456), "123456", True),  # six significant digits: still exact
        (Fraction(1234567), "1.23457e6", False),  # seven: 1234567 -> 1.23457e6 (7th digit 7 rounds up)
        (Fraction(1, 10**4), "0.0001", True),  # positional down to 1e-4
        (Fraction(1, 10**5), "1e-5", True),  # scientific below 1e-4
        (Fraction(1, 10**12), "1e-12", True),  # 1 pF in farads
        (Fraction(10**6), "1e6", True),  # scientific from 1e6
        (Fraction(1000, 3), "333.333", False),  # 1/3 A in mA
        (Fraction(0), "0", True),
    ],
)
def test_values_are_displayed_exactly_or_to_six_significant_figures(value, text, exact):
    shown = display(value)
    assert (shown.text, shown.exact, shown.relation) == (text, exact, "=" if exact else "≈")


def test_rounding_is_half_even_at_the_sixth_significant_figure():
    # 2.500005 lies exactly between 2.50000 and 2.50001: half-even keeps the even 0.
    assert display(Fraction(2500005, 10**6)).text == "2.50000"
    # 2.500015 lies exactly between 2.50001 and 2.50002: half-even takes the even 2.
    assert display(Fraction(2500015, 10**6)).text == "2.50002"
    # 9999995 lies between 9999990 and 10000000 at six figures: the kept 9 is odd, so it
    # rounds up to 1.00000e7, keeping all six significant digits.
    assert display(Fraction(9999995)).text == "1.00000e7"


# -------------------------------------------------------------------------- units


@pytest.mark.parametrize(
    ("unit", "factor", "coherent"),
    [
        (f"k{OHM}", Fraction(1000), OHM),
        ("Ω", Fraction(1), OHM),  # the OHM SIGN is the same unit, written canonically
        ("mA", Fraction(1, 1000), "A"),
        ("µF", Fraction(1, 10**6), "F"),  # MICRO SIGN
        ("μF", Fraction(1, 10**6), "F"),  # GREEK SMALL LETTER MU
        ("uF", Fraction(1, 10**6), "F"),
        ("nH", Fraction(1, 10**9), "H"),
        ("pF", Fraction(1, 10**12), "F"),
        ("GHz", Fraction(10**9), "Hz"),
        ("MW", Fraction(10**6), "W"),
        ("kg", Fraction(1), "kg"),  # k + g: 1000 × 1/1000 kg
        ("mg", Fraction(1, 10**6), "kg"),  # 1/1000 × 1/1000 kg
        ("mm", Fraction(1, 1000), "m"),
        ("ms", Fraction(1, 1000), "s"),
        ("mS", Fraction(1, 1000), "S"),
        ("mol", Fraction(1), "mol"),
        ("cd", Fraction(1), "cd"),
        ("T", Fraction(1), "T"),  # the tesla; there is no tera prefix
        ("Wb", Fraction(1), "Wb"),
        ("J", Fraction(1), "J"),
        ("C", Fraction(1), "C"),
        ("K", Fraction(1), "K"),
    ],
)
def test_a_unit_is_one_table_symbol_with_at_most_one_prefix(unit, factor, coherent):
    parsed = parse_unit(unit)
    assert (parsed.factor, coherent_unit(parsed.dimension)) == (factor, coherent)


def test_symbols_and_prefixes_are_case_sensitive():
    # S is the siemens and s the second: different dimensions entirely.
    assert parse_unit("S").dimension != parse_unit("s").dimension
    # m is milli and M mega: mA is 1/1000 A, MA is 1000000 A.
    assert (parse_unit("mA").factor, parse_unit("MA").factor) == (Fraction(1, 1000), 10**6)
    # No case folding: v is not the volt, hz not the hertz, K is the kelvin (not kilo).
    for text in ("v", "hz", "KV", "kv"):
        with pytest.raises(UnitError) as caught:
            parse_unit(text)
        assert caught.value.problem is UnitProblem.UNKNOWN


@pytest.mark.parametrize("unit", ["dB", "Np", "°C", "℃", "degC", "°F", "°", "deg", "rad", "sr", "mrad", "kdB"])
def test_logarithmic_affine_and_angular_units_are_refused_as_unsupported(unit):
    with pytest.raises(UnitError) as caught:
        parse_unit(unit)
    assert caught.value.problem is UnitProblem.UNSUPPORTED


@pytest.mark.parametrize("unit", ["furlong", "ohm", "volt", "mkg", f"T{OHM}", "k", "min", "N", "Pa", " "])
def test_anything_outside_the_table_is_an_unknown_unit_never_guessed(unit):
    with pytest.raises(UnitError) as caught:
        parse_unit(unit)
    assert caught.value.problem is UnitProblem.UNKNOWN


def test_the_derived_units_have_the_right_dimensions():
    # V/Ω = A; V×A = W; W×s = J; A×s = C; V×s = Wb; 1/Ω = S; C/V = F; Wb/A = H; Wb/m² = T.
    def dim(unit):
        return parse_unit(unit).dimension

    assert dim("V").over(dim(OHM)) == dim("A")
    assert dim("V").times(dim("A")) == dim("W")
    assert dim("W").times(dim("s")) == dim("J")
    assert dim("A").times(dim("s")) == dim("C")
    assert dim("V").times(dim("s")) == dim("Wb")
    assert dim("A").over(dim("V")) == dim("S")
    assert dim("C").over(dim("V")) == dim("F")
    assert dim("Wb").over(dim("A")) == dim("H")
    assert dim("Wb").over(dim("m").power(2)) == dim("T")
    assert dim("s").power(-1) == dim("Hz")


def test_a_dimension_without_a_named_unit_is_a_product_of_base_units():
    velocity = parse_unit("m").dimension.over(parse_unit("s").dimension)
    assert coherent_unit(velocity) == "m·s^-1"
    assert coherent_unit(parse_unit("m").dimension.power(2)) == "m^2"
    assert coherent_unit(parse_unit("V").dimension.over(parse_unit("V").dimension)) == ""


@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        (f"10 {OHM}", Fraction(10), OHM),
        (f"10{OHM}", Fraction(10), OHM),
        (f"4.7 k{OHM}", Fraction(4700), OHM),  # 4.7 × 1000
        ("0.5 mA", Fraction(1, 2000), "A"),  # 0.5 × 1/1000
        ("-5 V", Fraction(-5), "V"),
        ("−5 V", Fraction(-5), "V"),
        ("2", Fraction(2), ""),
        ("1e-3 s", Fraction(1, 1000), "s"),
        ("100 nF", Fraction(1, 10**7), "F"),  # 100 × 1e-9
    ],
)
def test_a_value_is_a_number_with_at_most_one_unit_in_coherent_si(text, value, unit):
    parsed = parse_quantity(text)
    assert (parsed.value, parsed.unit) == (value, unit)


@pytest.mark.parametrize("text", ["", "ten", "10 k Ω", "10 V A", "V", "1.2.3 V"])
def test_a_malformed_value_is_refused(text):
    with pytest.raises(NumberError):
        parse_quantity(text)


def test_conversion_is_exact_and_needs_the_same_dimension():
    third_of_an_amp = quantity(Fraction(1, 3), "A")
    assert to_unit(third_of_an_amp, "mA") == Fraction(1000, 3)  # 1/3 ÷ 1/1000
    assert display(to_unit(third_of_an_amp, "mA")).text == "333.333"  # same six figures
    assert to_unit(quantity(4700, OHM), f"k{OHM}") == Fraction(47, 10)
    with pytest.raises(ConversionError):
        to_unit(third_of_an_amp, "V")


# ------------------------------------------------------------------------ formulas


def test_section_203_formulas_parse_as_written():
    total = parse_formula("Rtotal = R1 + R2")
    assert total.target == "Rtotal" and total.symbols == ("R1", "R2")
    assert total.expression == Sum(Symbol("R1"), (("+", Symbol("R2")),))
    current = parse_formula("I = V / Rtotal")
    assert current.target == "I" and current.symbols == ("V", "Rtotal")
    assert current.expression == Product(Symbol("V"), (("/", Symbol("Rtotal")),))


def test_unicode_operators_mean_the_ascii_ones():
    assert parse_formula("y = a × b ÷ c − d · e").expression == parse_formula("y = a * b / c - d * e").expression


def test_unary_minus_binds_looser_than_a_power():
    # -x^2 is -(x^2): with x = 3 that is -9, not 9.
    assert parse_formula("y = -x^2").expression == Negate(Power(Symbol("x"), 2))
    assert _value("-x^2", x="3") == -9


def test_multi_letter_symbols_are_real():
    # Section 203 itself uses Rtotal; a run of letters and digits is one symbol token.
    assert parse_formula("y = R_1 + Rtotal2").symbols == ("R_1", "Rtotal2")
    section_203 = {"I", "V", "R1", "R2", "Rtotal"}
    assert parse_formula("I = V / Rtotal", section_203).symbols == ("V", "Rtotal")
    assert parse_formula("Rtotal = R1 + R2", section_203).symbols == ("R1", "R2")


@pytest.mark.parametrize(
    ("text", "known", "reading"),
    [
        ("V = IR", {"V", "I", "R"}, "I × R"),  # P11-12's own example
        ("V = IR", {"V", "I", "R", "IR"}, "I × R"),  # two readings even when IR is known
        ("P = R1R2", {"P", "R1", "R2"}, "R1 × R2"),
        ("y = abc", {"a", "b", "c", "bc"}, "a × bc"),  # the fewest parts first
    ],
)
def test_a_symbol_the_calculations_own_symbols_spell_is_implicit_multiplication(text, known, reading):
    """ADR 0042 P11-12: `IR` could be one symbol or two; never guessed (section 97)."""
    with pytest.raises(FormulaError) as caught:
        parse_formula(text, known)
    assert caught.value.problem is FormulaProblem.IMPLICIT_MULTIPLICATION
    assert reading in str(caught.value)


def test_a_symbol_its_known_parts_do_not_spell_stays_one_symbol():
    # Only I is known: IR cannot be I × R, so it is one symbol. It has no value unless the
    # request supplies one, so no number can ever come from a guessed reading.
    assert parse_formula("V = IR", {"V", "I"}).symbols == ("IR",)
    assert parse_formula("V = IR", {"V", "IR"}).symbols == ("IR",)
    # Without any known symbols the text alone cannot be judged.
    assert parse_formula("V = IR").symbols == ("IR",)


def test_no_constant_has_a_built_in_value():
    # π is an ordinary symbol with no value of its own (P11-12: constants are not supported).
    with pytest.raises(EvaluationError) as caught:
        _value("π * r^2", r="2 m")
    assert caught.value.problem is EvaluationProblem.UNBOUND_SYMBOL


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("", FormulaProblem.MALFORMED),
        ("y", FormulaProblem.MALFORMED),
        ("y =", FormulaProblem.MALFORMED),
        ("= x", FormulaProblem.MALFORMED),
        ("y = (x", FormulaProblem.MALFORMED),
        ("y = x)", FormulaProblem.MALFORMED),
        ("y = ()", FormulaProblem.MALFORMED),
        ("y = x +", FormulaProblem.MALFORMED),
        ("y = 2 V", FormulaProblem.IMPLICIT_MULTIPLICATION),
        ("y = 2V", FormulaProblem.IMPLICIT_MULTIPLICATION),
        ("V = I R", FormulaProblem.IMPLICIT_MULTIPLICATION),
        ("y = 2(a + b)", FormulaProblem.IMPLICIT_MULTIPLICATION),
        ("y = (a)(b)", FormulaProblem.IMPLICIT_MULTIPLICATION),
        ("y = x^2 z", FormulaProblem.IMPLICIT_MULTIPLICATION),
        (f"R = 5{OHM}", FormulaProblem.IMPLICIT_MULTIPLICATION),  # no units inside a formula
        ("y = sqrt(x)", FormulaProblem.FUNCTION_CALL),
        ("Z = R(1 + x)", FormulaProblem.FUNCTION_CALL),
        ("V / I = R", FormulaProblem.UNSUPPORTED_FORM),  # would need solving (N1)
        ("x = 2 * x + 1", FormulaProblem.UNSUPPORTED_FORM),  # target not isolated
        ("y ≈ x", FormulaProblem.UNSUPPORTED_FORM),
        ("y ∝ x", FormulaProblem.UNSUPPORTED_FORM),
        ("y < x", FormulaProblem.UNSUPPORTED_FORM),
        ("a = b = c", FormulaProblem.UNSUPPORTED_FORM),
        ("y = x^2^3", FormulaProblem.UNSUPPORTED_FORM),
        ("y = x**2", FormulaProblem.UNSUPPORTED_OPERATOR),
        ("y = +x", FormulaProblem.UNSUPPORTED_OPERATOR),
        ("y = x % 2", FormulaProblem.UNSUPPORTED_CHARACTER),
        ("y = x!", FormulaProblem.UNSUPPORTED_CHARACTER),
        ("y = √x", FormulaProblem.UNSUPPORTED_CHARACTER),
        ("y = x^0.5", FormulaProblem.NON_INTEGER_EXPONENT),
        ("y = x^n", FormulaProblem.NON_INTEGER_EXPONENT),
        ("y = x^(2)", FormulaProblem.NON_INTEGER_EXPONENT),
        (f"y = x^{MAX_EXPONENT + 1}", FormulaProblem.TOO_COMPLEX),
        ("y = " + "(" * (MAX_DEPTH + 1) + "x" + ")" * (MAX_DEPTH + 1), FormulaProblem.TOO_COMPLEX),
        ("y = " + "x + " * MAX_FORMULA_LENGTH + "x", FormulaProblem.TOO_COMPLEX),
    ],
)
def test_text_outside_the_grammar_is_refused_with_its_problem(text, problem):
    with pytest.raises(FormulaError) as caught:
        parse_formula(text)
    assert caught.value.problem is problem


def test_the_guards_admit_formulas_up_to_their_limits():
    nested = "(" * MAX_DEPTH + "x" + ")" * MAX_DEPTH
    assert parse_formula(f"y = {nested}").symbols == ("x",)
    assert parse_formula("y = " + "-" * MAX_DEPTH + "x").symbols == ("x",)
    assert parse_formula(f"y = x^{MAX_EXPONENT}").expression == Power(Symbol("x"), MAX_EXPONENT)
    with pytest.raises(FormulaError) as caught:
        parse_formula("y = " + "-" * (MAX_DEPTH + 1) + "x")
    assert caught.value.problem is FormulaProblem.TOO_COMPLEX


def test_the_parser_executes_nothing():
    """No eval, exec, compile or Python ast: the text is parsed by a whitelist only."""
    for path in sorted((PROJECT_ROOT / "app" / "calculation").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        assert "ast" not in imported, path.name
        called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assert not called & {"eval", "exec", "compile", "__import__"}, path.name


# ---------------------------------------------------------------------- evaluation


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2 + 3 * 4", 14),  # multiplication first
        ("(2 + 3) * 4", 20),
        ("2 - 3 - 4", -5),  # left to right: (2 - 3) - 4
        ("12 / 3 / 2", 2),  # left to right: (12 / 3) / 2
        ("2^3", 8),
        ("2^-2", Fraction(1, 4)),
        ("--3", 3),
        ("1 / 3 + 1 / 6", Fraction(1, 2)),  # 2/6 + 1/6 = 3/6
    ],
)
def test_arithmetic_is_exact_with_the_usual_precedence(text, expected):
    assert _value(text) == expected


def test_section_203_evaluates_exactly_with_units():
    bindings = {
        "R1": parse_quantity(f"10 {OHM}"),
        "R2": parse_quantity(f"20 {OHM}"),
        "V": parse_quantity("10 V"),
    }
    total = evaluate(parse_formula("Rtotal = R1 + R2").expression, bindings)
    assert (total.value, total.unit) == (30, OHM)  # 10 Ω + 20 Ω = 30 Ω
    current = evaluate(parse_formula("I = V / Rtotal").expression, {**bindings, "Rtotal": total})
    assert (current.value, current.unit) == (Fraction(1, 3), "A")  # 10 V ÷ 30 Ω = 1/3 A
    assert display(total.value).text == "30" and display(current.value).text == "0.333333"


def test_units_combine_and_convert_exactly():
    # P = I^2 × R with I = 2 mA and R = 4.7 kΩ: (1/500)^2 × 4700 = 4700/250000 = 47/2500 W.
    power = _value("I^2 * R", I="2 mA", R=f"4.7 k{OHM}")
    assert power == Fraction(47, 2500)  # 0.0188 W
    watts = evaluate(parse_formula("P = I^2 * R").expression,
                     {"I": parse_quantity("2 mA"), "R": parse_quantity(f"4.7 k{OHM}")})
    assert watts.unit == "W"


def test_adding_a_voltage_to_a_resistance_is_refused_with_no_number():
    """Section 92: "Voltage + resistance" must not silently produce a result."""
    with pytest.raises(EvaluationError) as caught:
        _value("V + R", V="10 V", R=f"20 {OHM}")
    assert caught.value.problem is EvaluationProblem.DIMENSION_MISMATCH
    assert f"V + {OHM}" in str(caught.value)


def test_a_dimensionless_number_cannot_be_added_to_a_quantity():
    with pytest.raises(EvaluationError) as caught:
        _value("V + 1", V="10 V")
    assert caught.value.problem is EvaluationProblem.DIMENSION_MISMATCH


@pytest.mark.parametrize(
    ("text", "bindings", "problem"),
    [
        ("V / R", {"V": "10 V", "R": f"0 {OHM}"}, EvaluationProblem.DIVISION_BY_ZERO),
        ("1 / (a - a)", {"a": "3"}, EvaluationProblem.DIVISION_BY_ZERO),
        ("x^-1", {"x": "0"}, EvaluationProblem.ZERO_TO_NEGATIVE_POWER),
        ("V / R", {"V": "10 V"}, EvaluationProblem.UNBOUND_SYMBOL),
        ("x^100 * x^100 * x^100", {"x": "1e10"}, EvaluationProblem.TOO_LARGE),
    ],
)
def test_expressions_without_a_value_raise_their_problem_never_a_number(text, bindings, problem):
    with pytest.raises(EvaluationError) as caught:
        _value(text, **bindings)
    assert caught.value.problem is problem


# ------------------------------------------------------------------------ requests


def _section_203_request(**changes) -> CalculationRequest:
    fields = dict(
        target="I",
        formulas=("Rtotal = R1 + R2", "I = V / Rtotal"),
        inputs=(CalculationInput("R1", f"10 {OHM}"), CalculationInput("R2", f"20 {OHM}"),
                CalculationInput("V", "10 V")),
    )
    fields.update(changes)
    return CalculationRequest(**fields)


def test_a_well_formed_request_is_checked_and_parsed_in_order():
    checked = _section_203_request(
        assumptions=(CalculationAssumption("T0", "300 K"),), admissions=("K-00000001",),
        scope=CalculationScope.AUTHORIZED,
    ).check()
    assert [f.target for f in checked.formulas] == ["Rtotal", "I"]
    assert checked.inputs == (
        ("R1", parse_quantity(f"10 {OHM}")), ("R2", parse_quantity(f"20 {OHM}")),
        ("V", parse_quantity("10 V")),
    )
    assert checked.assumptions == (("T0", parse_quantity("300 K")),)
    assert checked.admission == "K-00000001"


def test_a_request_needs_no_formula_and_no_admission():
    checked = CalculationRequest("V", inputs=(CalculationInput("V", "10 V"),)).check()
    assert checked.formulas == () and checked.admission is None


@pytest.mark.parametrize(
    "changes",
    [
        {"target": "2x"},
        {"target": ""},
        {"target": "I R"},
        {"formulas": ("V / I = R",)},  # outside the grammar: exit 2, not "not usable"
        {"formulas": ("I = V R",)},
        {"formulas": ("I = V / Rtotal", "I = V/Rtotal")},  # the same formula twice
        {"formulas": ["I = V / R"]},  # not a tuple
        {"inputs": (CalculationInput("R1", "10 dB"),)},  # unsupported unit
        {"inputs": (CalculationInput("R1", "10 ohms"),)},  # unknown unit
        {"inputs": (CalculationInput("R1", "ten ohm"),)},
        {"inputs": (CalculationInput("R 1", "10 V"),)},
        {"inputs": (CalculationInput("V", "10 V"), CalculationInput("V", "12 V"))},  # twice
        {"assumptions": (CalculationAssumption("V", "12 V"),)},  # V is already an input
        {"admissions": ("K-00000001", "K-00000002")},  # N2: at most one stored equation
        {"admissions": ("K-12",)},
        {"admissions": ("CPT-00000001",)},  # a concept, not a knowledge object
        {"admissions": ("equation",)},
        {"scope": "everything"},
    ],
)
def test_an_invalid_request_is_refused_as_invalid_input_before_any_database(changes):
    with pytest.raises(InvalidInputError) as caught:
        _section_203_request(**changes).check()
    report = caught.value.report
    assert report.category is FailureCategory.INVALID_INPUT and report.stage == "calculation.request"
    assert report.data_changed is False


def test_a_request_formula_its_own_symbols_make_ambiguous_is_refused():
    # V = IR with I and R supplied: implicit multiplication, an invalid request (exit 2).
    request = CalculationRequest(
        "V", formulas=("V = IR",),
        inputs=(CalculationInput("I", "2 A"), CalculationInput("R", f"5 {OHM}")),
    )
    with pytest.raises(InvalidInputError) as caught:
        request.check()
    assert "IMPLICIT_MULTIPLICATION" in caught.value.report.summary
    # Another formula's target counts as a known symbol too: P = VI with V defined and I supplied.
    request = CalculationRequest(
        "P", formulas=("V = I * R", "P = VI"),
        inputs=(CalculationInput("I", "2 A"), CalculationInput("R", f"5 {OHM}")),
    )
    with pytest.raises(InvalidInputError):
        request.check()
    # With IR itself supplied and neither I nor R, IR is one symbol: accepted.
    accepted = CalculationRequest(
        "V", formulas=("V = IR",), inputs=(CalculationInput("IR", "10 V"),)
    ).check()
    assert accepted.formulas[0].symbols == ("IR",)


def test_two_admissions_are_refused_by_the_one_equation_rule():
    with pytest.raises(InvalidInputError) as caught:
        _section_203_request(admissions=("K-00000001", "K-00000002")).check()
    assert "at most one stored equation" in caught.value.report.reason


# -------------------------------------------------------------- package boundaries


def test_calculation_may_read_storage_but_not_the_other_services():
    """ADR 0043 P11-26 (N3): app.storage only; never knowledge, reasoning, query, extraction."""
    assert ALLOWED["app.calculation"] == {"app.version", "app.models", "app.core", "app.storage"}
    assert "app.calculation" not in ALLOWED["app.reasoning"]  # no cycle through reasoning
    assert "app.calculation" in ALLOWED["app.ui"]


def test_the_calculation_package_uses_the_standard_library_only():
    import sys

    stdlib = set(sys.stdlib_module_names)
    for path in sorted(Path(PROJECT_ROOT / "app" / "calculation").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for name in names:
                root = name.split(".")[0]
                assert root == "app" or root in stdlib, (path.name, name)
