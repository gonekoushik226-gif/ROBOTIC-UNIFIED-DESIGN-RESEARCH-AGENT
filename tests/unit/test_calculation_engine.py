"""Phase 11 Batch B: formula resolution, methods, cycles, missing inputs, assumptions and
the returned trace, on structured requests that admit no stored equation (ADR 0042
P11-16 ... P11-24).

Every expected value is worked by hand in the test (section 167), never computed by the
code under test. No request here admits a stored equation, so no database exists or is
opened (ADR 0043 P11-29): the engine is built without a repository.
"""

from __future__ import annotations

import json

import pytest

from app.calculation import (
    AnswerStatus,
    CalculationAssumption,
    CalculationEngine,
    CalculationInput,
    CalculationRequest,
    MethodState,
    Origin,
    SymbolState,
    to_json,
)
from app.calculation.engine import ENGINE_NAME, ENGINE_VERSION
from app.core.errors import InvalidInputError


def _calc(target, formulas=(), inputs=(), assumptions=()):
    request = CalculationRequest(
        target,
        formulas=tuple(formulas),
        inputs=tuple(CalculationInput(s, v) for s, v in inputs),
        assumptions=tuple(CalculationAssumption(s, v) for s, v in assumptions),
    )
    return CalculationEngine().calculate(request)


def _symbol(result, name):
    return next(s for s in result.symbols if s.symbol == name)


SECTION_203 = dict(
    formulas=("Rtotal = R1 + R2", "I = V / Rtotal"),
    inputs=(("R1", "10 Ω"), ("R2", "20 Ω"), ("V", "10 V")),
)


# ----------------------------------------------------------------- section 203


def test_section_203_is_calculated_with_the_expected_values():
    result = _calc("I", **SECTION_203)

    assert result.status is AnswerStatus.CALCULATED
    # Worked by hand: Rtotal = 10 Ω + 20 Ω = 30 Ω exactly; I = 10 V ÷ 30 Ω = 1/3 A.
    rtotal, current = _symbol(result, "Rtotal"), _symbol(result, "I")
    assert (rtotal.state, rtotal.value.exact, rtotal.value.relation, rtotal.value.text) == (
        SymbolState.DERIVED, "30", "=", "30 Ω")
    assert (current.state, current.value.exact, current.value.relation, current.value.text) == (
        SymbolState.DERIVED, "1/3", "≈", "0.333333 A")
    assert result.result == current.value
    assert result.message.startswith("I ≈ 0.333333 A - calculated (step 2); exact value 1/3")


def test_section_203_trace_holds_all_seven_required_fields():
    """Inputs, formula, intermediate result, final result, units, formula and input source."""
    result = _calc("I", **SECTION_203)

    # Inputs, with their input source.
    assert [(i.symbol, i.origin, i.text, i.value.text) for i in result.inputs] == [
        ("R1", Origin.USER_INPUT, "10 Ω", "10 Ω"),
        ("R2", Origin.USER_INPUT, "20 Ω", "20 Ω"),
        ("V", Origin.USER_INPUT, "10 V", "10 V"),
    ]
    # Formulas, each with its formula source: the request.
    assert [(f.number, f.text, f.target, f.origin, f.knowledge_id) for f in result.formulas] == [
        (1, "Rtotal = R1 + R2", "Rtotal", Origin.USER_INPUT, None),
        (2, "I = V / Rtotal", "I", Origin.USER_INPUT, None),
    ]
    first, second = result.steps
    # The intermediate result, with its substitution (section 93) and unit.
    assert (first.number, first.symbol, first.text, first.substitution) == (
        1, "Rtotal", "Rtotal = R1 + R2", "Rtotal = 10 Ω + 20 Ω")
    assert (first.result.relation, first.result.text) == ("=", "30 Ω")
    assert [(i.symbol, i.origin, i.value.text) for i in first.inputs] == [
        ("R1", Origin.USER_INPUT, "10 Ω"), ("R2", Origin.USER_INPUT, "20 Ω")]
    # The final result, whose input Rtotal comes from step 1.
    assert (second.symbol, second.substitution, second.result.text) == ("I", "I = 10 V ÷ 30 Ω", "0.333333 A")
    assert [(i.symbol, i.origin, i.from_step) for i in second.inputs] == [
        ("V", Origin.USER_INPUT, None), ("Rtotal", Origin.DERIVED, 1)]
    assert second.formula_origin is Origin.USER_INPUT and second.knowledge_id is None
    assert first.dimension_check.startswith("PASSED") and second.dimension_check.startswith("PASSED")
    # Units: every value carries its coherent SI unit.
    assert {s.symbol: s.value.unit for s in result.symbols} == {
        "I": "A", "V": "V", "Rtotal": "Ω", "R1": "Ω", "R2": "Ω"}


def test_every_result_is_pending_labelled_calculated_and_versioned():
    result = _calc("I", **SECTION_203)

    assert result.verification_status == "PENDING"
    assert _symbol(result, "I").origins == (Origin.DERIVED,)
    versions = result.versions
    assert (versions.engine, versions.engine_version) == (ENGINE_NAME, ENGINE_VERSION)
    assert (versions.grammar_version, versions.unit_table_version) == ("1", "1")
    assert "round-half-even" in versions.display_rule
    assert result.database_opened is False and result.read_only_connection is None
    assert any("Nothing was written" in note for note in result.notes)
    assert any("not independently verified" in note for note in result.notes)
    assert any("knowledge.db was not opened" in note for note in result.notes)


# -------------------------------------------------------- missing and blocked


def test_a_missing_input_is_named_with_the_formula_that_needs_it():
    """Sections 89, 163, 228, 236: section 203 without R2."""
    result = _calc("I", SECTION_203["formulas"], (("R1", "10 Ω"), ("V", "10 V")))

    assert result.status is AnswerStatus.CANNOT_DETERMINE and result.result is None
    assert [(m.symbol, m.required_by) for m in result.missing] == [("R2", ("Rtotal = R1 + R2",))]
    assert _symbol(result, "R2").state is SymbolState.MISSING
    assert _symbol(result, "Rtotal").state is SymbolState.BLOCKED
    assert _symbol(result, "I").state is SymbolState.BLOCKED
    (method,) = _symbol(result, "Rtotal").methods
    assert method.state is MethodState.MISSING_INPUT and "R2" in method.reason
    (blocked,) = _symbol(result, "I").methods
    assert blocked.state is MethodState.BLOCKED and "Rtotal (BLOCKED)" in blocked.reason
    assert "Missing: R2 — required by `Rtotal = R1 + R2`" in result.message
    assert result.steps == ()  # nothing was evaluated, nothing invented


def test_a_target_nothing_defines_is_missing_itself():
    result = _calc("Q", ("P = V * I",), (("V", "1 V"), ("I", "1 A")))

    assert result.status is AnswerStatus.CANNOT_DETERMINE
    assert [(m.symbol, m.required_by) for m in result.missing] == [("Q", ())]
    assert "Missing: Q — required by the target itself" in result.message
    assert [s.symbol for s in result.symbols] == ["Q"]  # P is never reached


# ---------------------------------------------------------- dimensional checks


def test_voltage_plus_resistance_is_dimensionally_inconsistent_with_no_number():
    """Sections 92 and 166: X = V + R, V = 10 V, R = 20 Ω."""
    result = _calc("X", ("X = V + R",), (("V", "10 V"), ("R", "20 Ω")))

    assert result.status is AnswerStatus.CANNOT_DETERMINE
    (method,) = _symbol(result, "X").methods
    assert method.state is MethodState.DIMENSIONALLY_INCONSISTENT
    assert "V + Ω" in method.reason and method.value is None
    assert result.steps == ()


def test_inputs_in_prefixed_units_are_converted_exactly():
    """Section 202: V = I × R with I = 2 mA and R = 4.7 kΩ is 0.002 A × 4700 Ω = 9.4 V."""
    result = _calc("V", ("V = I * R",), (("I", "2 mA"), ("R", "4.7 kΩ")))

    value = _symbol(result, "V").value
    assert (value.exact, value.relation, value.text) == ("47/5", "=", "9.4 V")
    assert result.steps[0].substitution == "V = 0.002 A × 4700 Ω"


def test_rounding_to_six_significant_figures_is_half_even_on_a_tie():
    """1.234565 V has seven digits; its sixth is 6 (even), so the tie stays 1.23456.
    1.234575 V: the sixth digit 7 is odd, so the tie rounds to 1.23458."""
    even = _calc("X", ("X = V * 1",), (("V", "1.234565 V"),))
    odd = _calc("X", ("X = V * 1",), (("V", "1.234575 V"),))

    assert (even.result.relation, even.result.displayed, even.result.exact) == ("≈", "1.23456", "246913/200000")
    assert (odd.result.relation, odd.result.displayed) == ("≈", "1.23458")


@pytest.mark.parametrize(
    ("formula", "inputs", "problem"),
    [
        ("I = V / R", (("V", "5 V"), ("R", "0 Ω")), "DIVISION_BY_ZERO"),
        ("Y = R^-1", (("R", "0 Ω"),), "ZERO_TO_NEGATIVE_POWER"),
    ],
)
def test_evaluation_errors_are_method_outcomes_never_values(formula, inputs, problem):
    target = formula.split(" = ")[0]
    result = _calc(target, (formula,), inputs)

    assert result.status is AnswerStatus.CANNOT_DETERMINE
    (method,) = _symbol(result, target).methods
    assert method.state is MethodState.EVALUATION_ERROR and problem in method.reason


# -------------------------------------------------------------- several methods


def test_two_methods_that_agree_give_one_value_and_both_are_reported():
    """P = V × I = 10 V × 2 A = 20 W; P = I² × R = 4 A² × 5 Ω = 20 W."""
    result = _calc("P", ("P = V * I", "P = I^2 * R"), (("V", "10 V"), ("I", "2 A"), ("R", "5 Ω")))

    power = _symbol(result, "P")
    assert result.status is AnswerStatus.CALCULATED and power.state is SymbolState.DERIVED
    assert [(m.formula, m.state, m.value.text) for m in power.methods] == [
        (1, MethodState.COMPLETE, "20 W"), (2, MethodState.COMPLETE, "20 W")]
    assert len(result.steps) == 2


def test_two_methods_that_disagree_are_conflicting_and_none_is_selected():
    """With R = 6 Ω the second method gives 4 A² × 6 Ω = 24 W, not 20 W."""
    result = _calc("P", ("P = V * I", "P = I^2 * R", "Q = P * 2"),
                   (("V", "10 V"), ("I", "2 A"), ("R", "6 Ω")))

    assert result.status is AnswerStatus.CONFLICTING and result.result is None
    power = _symbol(result, "P")
    assert power.state is SymbolState.CONFLICTING and power.value is None
    assert [v.text for v in power.conflicting_values] == ["20 W", "24 W"]
    assert "Resolution: not automatically selected" in result.message

    downstream = _calc("Q", ("P = V * I", "P = I^2 * R", "Q = P * 2"),
                       (("V", "10 V"), ("I", "2 A"), ("R", "6 Ω")))
    (method,) = _symbol(downstream, "Q").methods
    assert _symbol(downstream, "Q").state is SymbolState.BLOCKED
    assert method.state is MethodState.BLOCKED and "P (CONFLICTING)" in method.reason


def test_a_supplied_target_is_the_direct_answer_and_its_formula_is_still_evaluated():
    """Section 166. V = 9.4 V supplied; V = I × R = 2 mA × 4.7 kΩ = 9.4 V agrees."""
    agree = _calc("V", ("V = I * R",), (("V", "9.4 V"), ("I", "2 mA"), ("R", "4.7 kΩ")))
    assert agree.status is AnswerStatus.CALCULATED
    assert _symbol(agree, "V").state is SymbolState.AVAILABLE
    assert _symbol(agree, "V").origins == (Origin.USER_INPUT,)
    assert _symbol(agree, "V").methods[0].state is MethodState.COMPLETE
    assert "direct answer" in agree.message

    # 9 V supplied disagrees with the formula's 9.4 V: CONFLICTING, never hidden.
    disagree = _calc("V", ("V = I * R",), (("V", "9 V"), ("I", "2 mA"), ("R", "4.7 kΩ")))
    assert disagree.status is AnswerStatus.CONFLICTING
    assert [v.text for v in _symbol(disagree, "V").conflicting_values] == ["9 V", "9.4 V"]


# --------------------------------------------------------------------- cycles


def test_a_formula_cycle_is_reported_and_never_looped():
    result = _calc("A", ("A = B + 1", "B = A - 1"))

    assert result.status is AnswerStatus.CANNOT_DETERMINE
    assert result.cycles == (("A", "B"),)
    for name in "AB":
        record = _symbol(result, name)
        assert record.state is SymbolState.BLOCKED and record.cycle == ("A", "B")
        assert [m.state for m in record.methods] == [MethodState.ON_CYCLE]
    assert result.steps == ()


def test_a_supplied_symbol_on_a_cycle_stays_available_and_the_rest_is_blocked():
    """The ADR 0039 Amendment 1 rule, applied to formulas (P11-17)."""
    result = _calc("A", ("A = B + 1", "B = A - 1"), (("A", "5"),))

    assert result.status is AnswerStatus.CALCULATED and result.result.exact == "5"
    assert _symbol(result, "A").state is SymbolState.AVAILABLE
    assert _symbol(result, "B").state is SymbolState.BLOCKED
    assert result.steps == ()  # the cycle is not broken by the supplied symbol

    blocked = _calc("B", ("A = B + 1", "B = A - 1", "C = D * 2"), (("A", "5"),))
    assert blocked.status is AnswerStatus.CANNOT_DETERMINE and "formula cycle" in blocked.message


def test_a_long_chain_resolves_iteratively():
    """X1 = X0 + 1, ..., X600 = X599 + 1 with X0 = 0 gives X600 = 600."""
    formulas = [f"X{i} = X{i - 1} + 1" for i in range(1, 601)]
    result = _calc("X600", formulas, (("X0", "0"),))

    assert result.status is AnswerStatus.CALCULATED and result.result.exact == "600"
    assert len(result.steps) == 600


# ---------------------------------------------------------------- assumptions


def test_an_assumption_is_labelled_its_effect_shown_and_its_results_conditional():
    """I = V / R with R = 2.2 kΩ and V assumed 5 V: 5 ÷ 2200 = 1/440 A ≈ 0.00227273 A."""
    result = _calc("P", ("I = V / R", "P = V * I"), (("R", "2.2 kΩ"),), (("V", "5 V"),))

    assert [(a.symbol, a.origin, a.value.text) for a in result.assumptions] == [
        ("V", Origin.ASSUMPTION, "5 V")]
    assert _symbol(result, "V").origins == (Origin.ASSUMPTION,)
    current = _symbol(result, "I")
    assert (current.value.exact, current.value.displayed) == ("1/440", "0.00227273")
    assert current.conditional_on == ("V",)
    # P = 5 V × 1/440 A = 1/88 W ≈ 0.0113636 W, conditional on V too.
    assert _symbol(result, "P").value.exact == "1/88"
    assert _symbol(result, "P").conditional_on == ("V",)
    (effect,) = result.effects
    assert (effect.symbol, effect.symbols, effect.formulas) == ("V", ("P", "I"), (1, 2))
    assert "conditional on the assumption(s) V" in result.message
    assert all(step.conditional_on == ("V",) for step in result.steps)


# ------------------------------------------------------------ the substitution


def test_the_substitution_keeps_grouping_and_shows_negative_values_in_parentheses():
    """P = (V − 2 × W)² ÷ −R: (5 − 2 × (−1))² ÷ −(−4) = 49 ÷ 4 = 12.25 W."""
    result = _calc("P", ("P = (V - 2 * W)^2 / -R",), (("R", "-4 Ω"), ("V", "5 V"), ("W", "-1 V")))

    assert result.result.exact == "49/4" and result.result.text == "12.25 W"
    assert result.steps[0].substitution == "P = (5 V − 2 × (-1 V))^2 ÷ -(-4 Ω)"


# ------------------------------------------------------------ determinism


def test_output_is_byte_identical_on_repeat_and_is_plain_json():
    first = to_json(_calc("I", **SECTION_203))
    assert to_json(_calc("I", **SECTION_203)) == first
    data = json.loads(first)
    assert data["status"] == "CALCULATED" and data["result"]["exact"] == "1/3"
    assert data["verification_status"] == "PENDING"


def test_an_invalid_request_is_refused_before_anything_else():
    with pytest.raises(InvalidInputError):
        _calc("V", ("V = I R",), (("I", "1 A"), ("R", "1 Ω")))


def test_the_engine_needs_no_database_without_an_admission(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _calc("I", **SECTION_203)
    assert result.status is AnswerStatus.CALCULATED
    assert list(tmp_path.iterdir()) == []  # nothing created anywhere


def test_a_missing_symbol_needed_only_by_an_unevaluated_method_is_still_attributed():
    """A method on a cycle is not evaluated, yet the MISSING symbol it needs is named with it."""
    result = _calc("A", ("A = B + C", "B = A - 1"))

    assert [(m.symbol, m.required_by) for m in result.missing] == [("C", ("A = B + C",))]
    assert "Missing: C — required by `A = B + C`" in result.message
