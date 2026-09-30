"""Phase 11 Batch B: the one admitted stored equation (N2 = (b); ADR 0042 P11-19).

What every test holds the admission to: at most one stored equation, named by its
knowledge-object identifier; an identifier that is not an existing `EQUATION` with
exactly one stored `equation` row makes the request invalid (exit code 2); P9-5
authorisation and scope withhold it, counted and never shown; P9-23 excludes `DELETED`
and `ARCHIVED`; `SUPERSEDED` is admitted with its stored pointer; an admitted equation
keeps its provenance and `knowledge_version`, is labelled `UNCERTAIN`, is parsed by the
whitelist and never executed, and is blocked by stored conflicting support; the scope
verdicts equal Phase 9's and Phase 10's; nothing is written.

Every test works in its own freshly migrated temporary database (`tests/conftest.py`);
the live database is never opened. Expected values are worked by hand.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.calculation import (
    AnswerStatus,
    CalculationEngine,
    CalculationInput,
    CalculationRequest,
    CalculationScope,
    MethodState,
    Origin,
    RefusalReason,
    SymbolState,
    to_json,
)
from app.calculation.scope import source_verdict
from app.core.errors import InvalidInputError, StorageError
from app.models import (
    Authorization,
    Equation,
    KnowledgeType,
    LifecycleStatus,
    RelationType,
    SourceCategory,
)
from app.query.requests import SourceScope
from app.query.scope import source_verdict as phase_9_verdict
from app.reasoning.requests import ReasoningScope
from app.reasoning.scope import source_verdict as phase_10_verdict
from app.storage import Repository, connect
from tests.unit.query_rows import QueryRows

INPUTS = (CalculationInput("R1", "10 Ω"), CalculationInput("R2", "20 Ω"), CalculationInput("V", "10 V"))


def _library(rows: QueryRows) -> SimpleNamespace:
    """One authorised book whose page 3 states I = V / Rtotal, as extraction stores it."""
    n = SimpleNamespace(rows=rows)
    n.book = rows.document("circuits")
    n.src = rows.source(n.book)
    n.run = rows.run(n.book)
    n.seg = rows.segment(n.book, 3)

    def equation(text, source=None, *, status=LifecycleStatus.ACTIVE, rows_of_equation=1, where=True):
        item = rows.knowledge(KnowledgeType.EQUATION, text, status=status)
        for _ in range(rows_of_equation):
            rows._add(Equation, expression=text, lifecycle_status=status, knowledge_id=item.id)
        source = n.src if source is None else source
        if where and source is n.src:
            rows.knowledge_occurrence(item, source, page=3, segment=n.seg, span=(0, len(text)), run=n.run)
        elif where:
            rows.knowledge_occurrence(item, source, page=1)
        return item

    n.equation = equation
    n.ohm = equation("I = V / Rtotal")
    return n


def _request(admitted, formulas=("Rtotal = R1 + R2",), scope=CalculationScope.MY_BOOKS, inputs=INPUTS):
    return CalculationRequest("I", formulas=formulas, inputs=inputs, admissions=(admitted.id,), scope=scope)


def _calc(repo, request):
    return CalculationEngine(repo).calculate(request)


def _invalid(call) -> InvalidInputError:
    with pytest.raises(InvalidInputError) as caught:
        call()
    assert caught.value.report.data_changed is False
    return caught.value


# ------------------------------------------------------------ the admitted equation


def test_section_203_with_the_one_admitted_stored_equation(repo):
    """N2: I = V / Rtotal admitted from the document; Rtotal = R1 + R2 from the request."""
    n = _library(QueryRows(repo))

    result = _calc(repo, _request(n.ohm))

    assert result.status is AnswerStatus.CALCULATED
    assert (result.result.exact, result.result.text) == ("1/3", "0.333333 A")
    assert [(f.number, f.text, f.origin, f.knowledge_id) for f in result.formulas] == [
        (1, "Rtotal = R1 + R2", Origin.USER_INPUT, None),
        (2, "I = V / Rtotal", Origin.ADMITTED_STORED_ITEM, n.ohm.id),
    ]
    first, second = result.steps
    assert (first.formula_origin, first.result.text) == (Origin.USER_INPUT, "30 Ω")
    assert (second.formula_origin, second.knowledge_id) == (Origin.ADMITTED_STORED_ITEM, n.ohm.id)
    assert second.uncertain_formula and not first.uncertain_formula
    admitted = result.admitted
    assert admitted.knowledge.id == n.ohm.id and admitted.knowledge.knowledge_version == 1
    assert admitted.label == "UNCERTAIN" and admitted.origin is Origin.ADMITTED_STORED_ITEM
    assert [(e.page_number, e.char_start, e.extraction_run_id) for e in admitted.evidence] == [
        (3, 0, n.run.id)]
    assert admitted.provenance.sources == (n.src,)
    assert [d.document.id for d in admitted.provenance.documents] == [n.book.id]
    assert admitted.provenance.runs == (n.run,)
    assert "labelled UNCERTAIN" in result.message
    assert result.database_opened is True


def test_a_request_admitting_two_equations_is_invalid_before_any_database_is_opened():
    request = CalculationRequest("I", inputs=INPUTS, admissions=("K-00000001", "K-00000002"))
    error = _invalid(lambda: CalculationEngine(None).calculate(request))
    assert "at most one stored equation" in error.report.reason


def test_an_identifier_that_is_not_an_existing_equation_is_invalid(repo):
    n = _library(QueryRows(repo))
    definition = n.rows.knowledge(KnowledgeType.DEFINITION, "Current is the flow of charge.")
    bare = n.rows.knowledge(KnowledgeType.EQUATION, "P = V * I")  # no equation row
    twice = n.equation("W = P * t", rows_of_equation=2)

    assert "does not exist" in _invalid(lambda: _calc(repo, _request(SimpleNamespace(id="K-00009999")))).report.summary
    assert "not a stored equation" in _invalid(lambda: _calc(repo, _request(definition))).report.summary
    assert "0 stored equation rows" in _invalid(lambda: _calc(repo, _request(bare))).report.summary
    assert "2 stored equation rows" in _invalid(lambda: _calc(repo, _request(twice))).report.summary


def test_unauthorised_out_of_scope_and_evidence_free_equations_are_withheld(repo):
    n = _library(QueryRows(repo))
    closed = n.rows.source(n.rows.document("closed"), authorization=Authorization.NOT_AUTHORIZED)
    external = n.rows.source(n.rows.document("web"), category=SourceCategory.AUTHORIZED_EXTERNAL_SOURCE)
    cases = {
        RefusalReason.NOT_AUTHORIZED: n.equation("I = V / Rtotal", closed),
        RefusalReason.OUT_OF_SCOPE: n.equation("I = V / Rtotal", external),
        RefusalReason.NO_EVIDENCE: n.equation("I = V / Rtotal", where=False),
    }
    for reason, equation in cases.items():
        result = _calc(repo, _request(equation))
        assert result.admitted is None
        assert (result.refused.knowledge_id, result.refused.reason) == (equation.id, reason)
        # Without it, I cannot be determined: the admission was withheld (exit code 3).
        assert result.status is AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION
        assert result.withheld.knowledge == 1
        assert "I = V / Rtotal" not in [f.text for f in result.formulas]  # never shown or used

    wider = _calc(repo, _request(cases[RefusalReason.OUT_OF_SCOPE], scope=CalculationScope.AUTHORIZED))
    assert wider.status is AnswerStatus.CALCULATED and wider.admitted is not None


def test_a_withheld_admission_does_not_block_what_the_request_alone_determines(repo):
    n = _library(QueryRows(repo))
    closed = n.rows.source(n.rows.document("closed"), authorization=Authorization.NOT_AUTHORIZED)
    hidden = n.equation("I = V / Rtotal", closed)

    result = _calc(repo, _request(hidden, formulas=("Rtotal = R1 + R2", "I = V / Rtotal")))

    assert result.status is AnswerStatus.CALCULATED and result.refused.reason is RefusalReason.NOT_AUTHORIZED
    assert result.withheld.unauthorized_evidence == 1


@pytest.mark.parametrize("status", [LifecycleStatus.DELETED, LifecycleStatus.ARCHIVED])
def test_deleted_and_archived_equations_are_not_admitted(repo, status):
    n = _library(QueryRows(repo))
    gone = n.equation("I = V / Rtotal", status=status)

    result = _calc(repo, _request(gone))

    assert result.admitted is None
    assert (result.refused.reason, result.refused.lifecycle_status) == (
        RefusalReason.EXCLUDED_BY_LIFECYCLE, status.value)
    assert result.status is AnswerStatus.CANNOT_DETERMINE  # exit code 0, not an authorisation gap
    assert result.withheld.excluded_by_lifecycle == 1


def test_a_superseded_equation_is_admitted_with_its_stored_pointer(repo):
    n = _library(QueryRows(repo))
    twin = n.equation("I = V / Rtotal")
    n.rows.supersede(n.ohm, twin)

    result = _calc(repo, _request(twin))

    assert result.status is AnswerStatus.CALCULATED
    assert result.admitted.knowledge.lifecycle_status is LifecycleStatus.SUPERSEDED
    assert result.admitted.superseded_by == n.ohm.id


def test_only_the_equations_evidence_in_scope_is_kept(repo):
    n = _library(QueryRows(repo))
    closed = n.rows.source(n.rows.document("closed"), authorization=Authorization.NOT_AUTHORIZED)
    n.rows.knowledge_occurrence(n.ohm, closed, page=9)

    result = _calc(repo, _request(n.ohm))

    assert {row.source_id for row in result.admitted.evidence} == {n.src.id}
    assert result.withheld.unauthorized_evidence == 1


# --------------------------------------------------------- parsing stored text


@pytest.mark.parametrize(
    ("stored", "problem"),
    [
        ("I = V / R total", "IMPLICIT_MULTIPLICATION"),
        ("V / Rtotal = I", "UNSUPPORTED_FORM"),
        ("I = sqrt(V)", "FUNCTION_CALL"),
        ("I ≈ V / Rtotal", "UNSUPPORTED_FORM"),
    ],
)
def test_an_admitted_equation_outside_the_grammar_is_not_usable_never_repaired(repo, stored, problem):
    n = _library(QueryRows(repo))
    item = n.equation(stored)

    result = _calc(repo, _request(item))

    assert result.status is AnswerStatus.CANNOT_DETERMINE
    assert problem in result.admitted.not_usable
    methods = [m for s in result.symbols for m in s.methods if m.origin is Origin.ADMITTED_STORED_ITEM]
    assert all(m.state is MethodState.NOT_USABLE for m in methods)
    assert result.steps == ()


def test_implicit_multiplication_is_judged_with_the_equations_own_target(repo):
    """V = IR: with I and R among the calculation's symbols, IR reads as I × R (P11-12)."""
    n = _library(QueryRows(repo))
    item = n.equation("V = IR")
    request = CalculationRequest("V", inputs=(CalculationInput("I", "2 A"), CalculationInput("R", "5 Ω")),
                                 admissions=(item.id,))

    result = _calc(repo, request)

    assert result.status is AnswerStatus.CANNOT_DETERMINE
    assert "IMPLICIT_MULTIPLICATION" in result.admitted.not_usable and "I × R" in result.admitted.not_usable


def test_a_request_formula_the_admitted_target_makes_ambiguous_is_invalid(repo):
    """With I the admitted equation's target and R an input, `P = IR` reads as I × R."""
    n = _library(QueryRows(repo))
    request = CalculationRequest(
        "P", formulas=("P = IR", "Rtotal = R1 + R2"),
        inputs=(*INPUTS, CalculationInput("R", "5 Ω")), admissions=(n.ohm.id,),
    )
    error = _invalid(lambda: _calc(repo, request))
    assert "IMPLICIT_MULTIPLICATION" in error.report.summary


# --------------------------------------------------------- conflicting support


def test_a_stored_conflict_naming_the_equation_blocks_its_method_and_shows_both_claims(repo):
    n = _library(QueryRows(repo))
    closed = n.rows.source(n.rows.document("closed"), authorization=Authorization.NOT_AUTHORIZED)
    rival = n.equation("I = V * Rtotal", closed)  # the other claim, not authorised
    conflict = n.rows.conflict(n.ohm, rival)

    result = _calc(repo, _request(n.ohm))

    assert result.status is AnswerStatus.CANNOT_DETERMINE
    (method,) = [m for m in next(s for s in result.symbols if s.symbol == "I").methods]
    assert method.state is MethodState.CONFLICTING_SUPPORT and conflict.id in method.reason
    (item,) = result.admitted.conflicts
    assert item.claim_a.knowledge.id == n.ohm.id  # shown in full
    assert item.claim_b.knowledge is None and item.claim_b.knowledge_id == rival.id  # withheld
    assert item.resolution == "not automatically selected"
    assert result.steps[-1].symbol == "Rtotal"  # the admitted method was never used for a value


def test_a_contradicts_relationship_in_scope_blocks_the_method(repo):
    n = _library(QueryRows(repo))
    other = n.equation("I = Rtotal / V")
    edge = n.rows.edge(RelationType.CONTRADICTS, from_knowledge_id=n.ohm.id, to_knowledge_id=other.id)
    n.rows.relationship_occurrence(edge, n.src, page=3)

    result = _calc(repo, _request(n.ohm))

    (item,) = result.admitted.contradictions
    assert item.relationship.id == edge.id
    assert result.status is AnswerStatus.CANNOT_DETERMINE


def test_a_contradiction_without_evidence_in_scope_is_withheld_and_counted(repo):
    n = _library(QueryRows(repo))
    other = n.equation("I = Rtotal / V")
    n.rows.edge(RelationType.CONTRADICTS, from_knowledge_id=n.ohm.id, to_knowledge_id=other.id)

    result = _calc(repo, _request(n.ohm))

    assert result.admitted.contradictions == () and result.withheld.relationships == 1
    assert result.status is AnswerStatus.CALCULATED


# ------------------------------------------------------ scope parity, read-only


def test_calculation_scope_verdicts_equal_phase_9s_and_phase_10s():
    """ADR 0043 P11-26: re-applied, not imported - and the same verdict every time."""
    from app.models import Document, Source, SourceAvailability

    for category in SourceCategory:
        for authorization in Authorization:
            source = Source(id="SRC-00000001", created_at="t", updated_at="t", name="s",
                            source_category=category, authorization=authorization,
                            availability=SourceAvailability.AVAILABLE)
            for scope in CalculationScope:
                ours = source_verdict(source, scope).value
                assert ours == phase_9_verdict(source, SourceScope(scope.value)).value
                assert ours == phase_10_verdict(source, ReasoningScope(scope.value)).value
    assert source_verdict(None, CalculationScope.AUTHORIZED).value == "NOT_AUTHORIZED"
    del Document


def test_admission_verdicts_equal_reasonings_on_the_same_stored_items(repo):
    """The same stored item is admitted or refused, for the same reason, by both phases."""
    from app.reasoning import Admission, ReasoningRequest, initial_availability

    rows = QueryRows(repo)
    n = _library(rows)
    concept = rows.concept("Current")
    closed = rows.source(rows.document("closed"), authorization=Authorization.NOT_AUTHORIZED)
    external = rows.source(rows.document("web"), category=SourceCategory.AUTHORIZED_EXTERNAL_SOURCE)
    items = [n.ohm, n.equation("I = V / Rtotal", closed), n.equation("I = V / Rtotal", external),
             n.equation("I = V / Rtotal", where=False),
             n.equation("I = V / Rtotal", status=LifecycleStatus.DELETED)]
    for scope in CalculationScope:
        for item in items:
            ours = _calc(repo, _request(item, scope=scope))
            theirs = initial_availability(repo, ReasoningRequest.of_target(
                "Current", admissions=(Admission(concept.id, item.id),), scope=ReasoningScope(scope.value)))
            ours_reason = None if ours.refused is None else ours.refused.reason.value
            theirs_reason = None if not theirs.refused else theirs.refused[0].reason.value
            assert ours_reason == theirs_reason, (item.id, scope)


def test_an_older_schema_is_refused_and_never_migrated(repo, db_path):
    n = _library(QueryRows(repo))
    repo.connection.commit()
    repo.connection.execute("PRAGMA user_version = 5")
    repo.connection.commit()
    reader = connect(db_path, read_only=True)
    try:
        with pytest.raises(StorageError) as caught:
            CalculationEngine(Repository(reader)).calculate(_request(n.ohm))
    finally:
        reader.close()
    assert "schema version is 5" in caught.value.report.reason


def test_the_admission_reads_only_and_runs_on_a_read_only_connection(repo, db_path):
    n = _library(QueryRows(repo))
    repo.connection.commit()
    before = repo.connection.total_changes
    answer = to_json(_calc(repo, _request(n.ohm)))
    assert repo.connection.total_changes == before

    reader = connect(db_path, read_only=True)
    try:
        result = CalculationEngine(Repository(reader)).calculate(_request(n.ohm))
    finally:
        reader.close()
    assert result.read_only_connection is True
    assert to_json(result).replace('"read_only_connection": true', "") == answer.replace(
        '"read_only_connection": false', "")
    # The calculation and derivation tables stay empty: nothing is persisted (N5).
    for table in ("calculation", "derivation"):
        assert repo.connection.execute(f"select count(*) from {table}").fetchone()[0] == 0
    assert _calc(repo, _request(n.ohm)).symbols[0].state is SymbolState.DERIVED
