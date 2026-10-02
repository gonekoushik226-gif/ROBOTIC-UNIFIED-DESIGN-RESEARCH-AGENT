"""Phase 12: verifying calculation and reasoning answers (ADR 0044 P12-9 ... P12-12).

What every test holds verification to: an answer file is untrusted input, rebuilt into
its request and compared, never believed; a calculation answer is reproduced, every step
re-evaluated by a separate evaluator, and its chain checked; a reasoning answer is
reproduced and every step re-checked against stored relationships; the outcome is
VERIFIED, FAILED or INCONCLUSIVE, and INCONCLUSIVE is never success; an answer that used
no stored source is "Provenance unavailable." with no citation. Expected values are
worked by hand. Temporary databases only; the live database is never opened.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from app.calculation import (
    CalculationAssumption,
    CalculationEngine,
    CalculationInput,
    CalculationRequest,
)
from app.calculation import to_json as calculation_json
from app.core.errors import InvalidInputError
from app.models import Equation, KnowledgeType, LifecycleStatus
from app.provenance import UNAVAILABLE_TEXT, CheckStatus, ProvenanceStatus
from app.reasoning import ReasoningEngine, ReasoningRequest, UserInput
from app.reasoning import to_json as reasoning_json
from app.verification import AnswerKind, read_answer, verify
from app.verification.independent import IndependentError, Operand, agrees, evaluate, exact_to_decimal
from tests.unit.query_rows import QueryRows
from tests.unit.reasoning_rows import ReasoningRows, section_201

SECTION_203 = CalculationRequest(
    "I", formulas=("Rtotal = R1 + R2", "I = V / Rtotal"),
    inputs=(CalculationInput("R1", "10 Ω"), CalculationInput("R2", "20 Ω"), CalculationInput("V", "10 V")),
)
VOLT = (2, 1, -3, -1, 0, 0, 0)
DIMENSIONLESS = (0,) * 7


def _file(tmp_path, text: str, name: str = "answer.json"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _calc_answer(tmp_path, request=SECTION_203, repo=None, edit=None):
    text = calculation_json(CalculationEngine(repo).calculate(request))
    if edit is not None:
        data = json.loads(text)
        edit(data)
        text = json.dumps(data, ensure_ascii=False)
    return read_answer(_file(tmp_path, text))


def _checks(report) -> dict[str, CheckStatus]:
    return {f"{c.kind} {c.subject_id}": c.status for c in report.checks}


# ----------------------------------------------------------- the independent evaluator


@pytest.mark.parametrize(
    ("formula", "values", "expected"),
    [
        ("y = a + b * c", {"a": "1", "b": "2", "c": "3"}, "7"),          # 1 + 6
        ("y = (a + b) * c", {"a": "1", "b": "2", "c": "3"}, "9"),        # 3 * 3
        ("y = -a^2", {"a": "3"}, "-9"),                                  # -(3^2)
        ("y = a - -b", {"a": "5", "b": "2"}, "7"),                       # 5 + 2
        ("y = a^-1 * b", {"a": "4", "b": "2"}, "0.5"),                   # (1/4) * 2
        ("y = a / b / c", {"a": "12", "b": "2", "c": "3"}, "2"),         # (12/2)/3
        ("y = a − b × c ÷ d", {"a": "10", "b": "4", "c": "3", "d": "6"}, "8"),  # 10 - 2
        ("y = 2.5e1 * a", {"a": "1/5"}, "5"),                            # 25 / 5
    ],
)
def test_the_independent_evaluator_follows_the_grammar(formula, values, expected):
    inputs = {s: Operand(exact_to_decimal(v), DIMENSIONLESS) for s, v in values.items()}
    assert evaluate(formula, inputs).value == Decimal(expected)


def test_the_independent_evaluator_propagates_dimensions():
    ohm = (2, 1, -3, -2, 0, 0, 0)
    found = evaluate("I = V / R", {"V": Operand(Decimal(10), VOLT), "R": Operand(Decimal(30), ohm)})
    assert found.dimension == (0, 0, 0, 1, 0, 0, 0)  # the ampere
    assert agrees(found, "1/3", (0, 0, 0, 1, 0, 0, 0))[0]
    assert not agrees(found, "1/4", (0, 0, 0, 1, 0, 0, 0))[0]
    assert not agrees(found, "1/3", VOLT)[0]


@pytest.mark.parametrize("formula", ["y = a + v", "y = a / z", "y = a b", "y = (a", "y = sqrt(a)", "y = q"])
def test_the_independent_evaluator_never_passes_what_it_cannot_compute(formula):
    inputs = {"a": Operand(Decimal(1), DIMENSIONLESS), "v": Operand(Decimal(1), VOLT),
              "z": Operand(Decimal(0), DIMENSIONLESS)}
    with pytest.raises(IndependentError):
        evaluate(formula, inputs)


# ---------------------------------------------------------------- answer files


@pytest.mark.parametrize(
    "text",
    ["not json", "[]", '{"status": "CALCULATED"}', '{"rule": "OTHER"}',
     '{"versions": {"engine": "RUDRA calculation engine"}, "request": {"target": 3}}'],
)
def test_a_file_that_is_not_a_rudra_answer_is_an_invalid_request(tmp_path, text):
    with pytest.raises(InvalidInputError) as caught:
        read_answer(_file(tmp_path, text))
    assert caught.value.report.data_changed is False


def test_a_recorded_request_outside_the_grammar_is_invalid(tmp_path):
    data = json.loads(calculation_json(CalculationEngine().calculate(SECTION_203)))
    data["request"]["formulas"] = ["I = V R"]
    with pytest.raises(InvalidInputError):
        read_answer(_file(tmp_path, json.dumps(data)))


# ------------------------------------------------------------ calculation answers


def test_a_request_only_answer_verifies_and_its_provenance_is_unavailable(tmp_path):
    answer = _calc_answer(tmp_path)
    assert answer.kind is AnswerKind.CALCULATION

    report = verify(answer, None)

    assert report.status is CheckStatus.VERIFIED and report.database_opened is False
    assert _checks(report) == {
        "REPRODUCTION answer": CheckStatus.VERIFIED, "INDEPENDENT step 1": CheckStatus.VERIFIED,
        "INDEPENDENT step 2": CheckStatus.VERIFIED, "CHAIN answer": CheckStatus.VERIFIED,
    }
    exposure = report.exposure
    assert exposure.source_status is ProvenanceStatus.UNAVAILABLE
    assert exposure.source_message.startswith(UNAVAILABLE_TEXT) and exposure.sources == ()
    assert exposure.pages == () and exposure.relevant_knowledge == ()
    assert exposure.calculation[1].startswith("Step 2: I = 10 V ÷ 30 Ω -> I ≈ 0.333333 A")
    assert exposure.verification_status is CheckStatus.VERIFIED


def test_a_tampered_result_fails_reproduction_and_the_independent_check(tmp_path):
    def edit(data):
        data["result"]["exact"] = "1/4"
        data["steps"][1]["result"]["exact"] = "1/4"
        data["symbols"][0]["value"]["exact"] = "1/4"

    report = verify(_calc_answer(tmp_path, edit=edit), None)

    assert report.status is CheckStatus.FAILED
    checks = _checks(report)
    assert checks["REPRODUCTION answer"] is CheckStatus.FAILED
    assert checks["INDEPENDENT step 2"] is CheckStatus.FAILED
    assert checks["INDEPENDENT step 1"] is CheckStatus.VERIFIED


def test_a_broken_chain_fails(tmp_path):
    def edit(data):
        data["steps"][1]["inputs"][1]["value"]["exact"] = "40"  # Rtotal claimed 40, step 1 said 30

    report = verify(_calc_answer(tmp_path, edit=edit), None)
    assert _checks(report)["CHAIN answer"] is CheckStatus.FAILED and report.status is CheckStatus.FAILED


def test_a_direct_answer_is_inconclusive_and_cannot_determine_is_verified(tmp_path):
    direct = CalculationRequest("V", formulas=(), inputs=(CalculationInput("V", "9 V"),))
    report = verify(_calc_answer(tmp_path, direct), None)
    assert report.status is CheckStatus.INCONCLUSIVE
    assert _checks(report)["DIRECT_ANSWER V"] is CheckStatus.INCONCLUSIVE

    missing = replace(SECTION_203, inputs=SECTION_203.inputs[:1] + SECTION_203.inputs[2:])
    report = verify(_calc_answer(tmp_path, missing), None)
    assert report.answer_status == "CANNOT_DETERMINE" and report.status is CheckStatus.VERIFIED


def test_assumptions_are_exposed(tmp_path):
    request = CalculationRequest("I", formulas=("I = V / R",), inputs=(CalculationInput("R", "5 Ω"),),
                                 assumptions=(CalculationAssumption("V", "10 V"),))
    report = verify(_calc_answer(tmp_path, request), None)
    assert report.exposure.assumptions == ("V = 10 V (ASSUMPTION)",)


def _stored_equation(repo):
    rows = QueryRows(repo)
    book = rows.document("series")
    source = rows.source(book)
    run = rows.run(book)
    item = rows.knowledge(KnowledgeType.EQUATION, "I = V / Rtotal")
    rows._add(Equation, expression="I = V / Rtotal", lifecycle_status=LifecycleStatus.ACTIVE, knowledge_id=item.id)
    rows.knowledge_occurrence(item, source, page=4, run=run)
    rows.commit()
    return book, item


def test_an_answer_with_its_admitted_equation_verifies_and_cites_its_page(repo, tmp_path):
    book, item = _stored_equation(repo)
    request = CalculationRequest("I", formulas=("Rtotal = R1 + R2",), inputs=SECTION_203.inputs,
                                 admissions=(item.id,))

    report = verify(_calc_answer(tmp_path, request, repo), repo)

    assert report.status is CheckStatus.VERIFIED and report.database_opened is True
    exposure = report.exposure
    assert exposure.source_status is ProvenanceStatus.AVAILABLE
    assert exposure.relevant_knowledge == (item.id,)
    assert exposure.pages == (f"{book.id} p.4",)
    assert exposure.derivation[1].endswith(f"stored equation {item.id}")


def test_without_the_database_an_admitted_answer_is_inconclusive(repo, tmp_path):
    _, item = _stored_equation(repo)
    request = CalculationRequest("I", formulas=("Rtotal = R1 + R2",), inputs=SECTION_203.inputs,
                                 admissions=(item.id,))
    report = verify(_calc_answer(tmp_path, request, repo), None)
    assert report.status is CheckStatus.INCONCLUSIVE
    assert report.exposure.source_status is ProvenanceStatus.UNAVAILABLE


def test_an_admitted_equation_deleted_since_fails_reproduction(repo, tmp_path):
    _, item = _stored_equation(repo)
    request = CalculationRequest("I", formulas=("Rtotal = R1 + R2",), inputs=SECTION_203.inputs,
                                 admissions=(item.id,))
    answer = _calc_answer(tmp_path, request, repo)
    repo.update(replace(item, lifecycle_status=LifecycleStatus.DELETED))

    report = verify(answer, repo)

    assert report.status is CheckStatus.FAILED
    assert _checks(report)["REPRODUCTION answer"] is CheckStatus.FAILED


# ------------------------------------------------------------ reasoning answers


def _reasoning_answer(repo, tmp_path, *inputs):
    request = ReasoningRequest.of_target("X", inputs=tuple(UserInput(i) for i in inputs))
    return read_answer(_file(tmp_path, reasoning_json(ReasoningEngine(repo).reason(request)), "reason.json"))


def test_a_reasoning_answer_is_reproduced_and_every_step_rechecked(repo, tmp_path):
    w = ReasoningRows(repo)
    g = section_201(w)
    w.commit()

    answer = _reasoning_answer(repo, tmp_path, "E", "D", "B")
    assert answer.kind is AnswerKind.REASONING
    report = verify(answer, repo)

    assert report.answer_status == "DETERMINED" and report.status is CheckStatus.VERIFIED
    steps = [c for c in report.checks if c.kind == "STEP"]
    assert len(steps) == 3 and all(c.status is CheckStatus.VERIFIED for c in steps)
    assert report.exposure.source_status is ProvenanceStatus.AVAILABLE
    assert report.exposure.calculation == ("No calculation: dependency reasoning performs none.",)
    assert g.x.id in {s.split(": ")[1].split(" ")[0] for s in report.exposure.derivation}


def test_a_reasoning_answer_whose_relationship_was_since_deleted_fails(repo, tmp_path):
    w = ReasoningRows(repo)
    g = section_201(w)
    w.commit()
    answer = _reasoning_answer(repo, tmp_path, "E", "D", "B")
    (edge,) = [r for r in repo.connection.execute(
        "select id from relationship where from_concept_id = ? and to_concept_id = ?", (g.c.id, g.e.id))]
    from app.models import Relationship

    stored = repo.get(Relationship, edge[0])
    repo.update(replace(stored, lifecycle_status=LifecycleStatus.DELETED))

    report = verify(answer, repo)

    assert report.status is CheckStatus.FAILED
    assert _checks(report)["REPRODUCTION answer"] is CheckStatus.FAILED
    assert any(c.kind == "STEP" and c.status is CheckStatus.FAILED for c in report.checks)


def test_without_the_database_a_reasoning_answer_is_inconclusive(repo, tmp_path):
    w = ReasoningRows(repo)
    section_201(w)
    w.commit()
    report = verify(_reasoning_answer(repo, tmp_path, "E"), None)
    assert report.status is CheckStatus.INCONCLUSIVE and report.database_opened is False


def test_a_malformed_forward_section_is_refused_not_crashed_on(repo, tmp_path):
    """Found by the Phase 12 check: forward-mode steps were read without a type check."""
    w = ReasoningRows(repo)
    section_201(w)
    w.commit()
    result = ReasoningEngine(repo).reason(ReasoningRequest.forward(inputs=(UserInput("E"),)))
    data = json.loads(reasoning_json(result))
    data["forward"]["steps"] = [{"concept_id": 7}]
    with pytest.raises(InvalidInputError):
        read_answer(_file(tmp_path, json.dumps(data), "forward.json"))
    data["forward"] = json.loads(reasoning_json(result))["forward"]
    assert verify(read_answer(_file(tmp_path, json.dumps(data), "forward-ok.json")), repo).status is CheckStatus.VERIFIED
