"""Phase 10 step A2: the reasoning foundation - the structured request, its checks, and
the request's initial availability under OI-1 = Option B (ADR 0038 P10-5; ADR 0039
P10-11, P10-23; ADR 0040 P10-27).

What every test holds the foundation to: a node is AVAILABLE initially only when the
request supplies it as `USER_INPUT` or admits one stored knowledge object for it;
stored knowledge is never available merely because it exists; an admitted item passes
authorisation, scope and lifecycle (P9-5, P9-23) or is refused and reported; the two
origins stay distinct and only the admitted one carries stored provenance; an invalid
request is an `InvalidInputError` (exit code 2); results are deterministic; nothing is
written. Every test works in its own freshly migrated temporary database
(`tests/conftest.py`); the live database is never opened.
"""

from __future__ import annotations

from dataclasses import fields, replace
from types import SimpleNamespace

import pytest

from app.core.errors import FailureCategory, InvalidInputError
from app.models import (
    Authorization,
    KnowledgeType,
    LifecycleStatus,
    RelationType,
    SourceCategory,
)
from app.models.identifiers import EntityKind, format_id
from app.query.requests import SourceScope
from app.query.scope import source_verdict as phase_9_verdict
from app.reasoning import (
    Admission,
    Assumption,
    Origin,
    ReasoningMode,
    ReasoningRequest,
    ReasoningScope,
    RefusalReason,
    SuppliedInput,
    UserInput,
    initial_availability,
    to_json,
)
from app.reasoning.scope import source_verdict
from app.storage import Repository, connect
from app.ui.cli.main import _EXIT_BY_CATEGORY
from tests.unit.query_rows import QueryRows as _Rows


def _library(rows: _Rows) -> SimpleNamespace:
    """Section 201's concepts, and stored knowledge items to admit or refuse.

    Book A is authorised and "my books"; D has a stored, authorised definition linked
    to it - which must still not make D available unless the request admits it.
    """
    n = SimpleNamespace()
    n.book_a = rows.document("book-a")
    n.src_a = rows.source(n.book_a)
    n.run_a, n.seg_a = rows.run(n.book_a), rows.segment(n.book_a, 1)
    n.x, n.a, n.b, n.c, n.d, n.e = (rows.concept(name) for name in "XABCDE")

    def stored(statement, source=None, *, status=LifecycleStatus.ACTIVE, span=(0, 10)):
        item = rows.knowledge(KnowledgeType.DEFINITION, statement, status=status)
        if source is n.src_a:
            rows.knowledge_occurrence(
                item, source, page=1, segment=n.seg_a, span=span, run=n.run_a
            )
        elif source is not None:  # another book: its own page, no book-A segment or run
            rows.knowledge_occurrence(item, source, page=1)
        return item

    n.stored = stored
    n.def_d = stored("D is a stated quantity.", n.src_a)
    n.defined_d = rows.edge(
        RelationType.DEFINED_BY, from_concept_id=n.d.id, to_knowledge_id=n.def_d.id
    )
    rows.relationship_occurrence(n.defined_d, n.src_a, page=1, segment=n.seg_a, span=(0, 10), run=n.run_a)
    n.def_b = stored("B is another stated quantity.", n.src_a, span=(20, 40))
    return n


def _invalid(call) -> InvalidInputError:
    with pytest.raises(InvalidInputError) as caught:
        call()
    report = caught.value.report
    assert report.category is FailureCategory.INVALID_INPUT
    assert report.data_changed is False
    return caught.value


# ------------------------------------------------------- the structured request


def test_a_valid_request_passes_its_checks_unchanged():
    request = ReasoningRequest.of_target(
        "X",
        inputs=(UserInput("E", "10 V"),),
        admissions=(Admission("D", "K-00000001"),),
        assumptions=(Assumption("B", "B holds for this problem."),),
    )
    assert request.check() is request
    assert request.mode is ReasoningMode.TARGET and request.scope is ReasoningScope.MY_BOOKS
    forward = ReasoningRequest.forward(inputs=(UserInput("CPT-00000001"),))
    assert forward.check() is forward and forward.target is None


@pytest.mark.parametrize(
    "request_",
    [
        ReasoningRequest(mode=ReasoningMode.TARGET),  # no target
        ReasoningRequest.of_target("   "),  # blank target
        ReasoningRequest.of_target("K-00000001"),  # a knowledge object is not a node
        ReasoningRequest.forward(target="X", inputs=(UserInput("E"),)),  # forward takes no target
        ReasoningRequest.forward(),  # nothing to reason from
        ReasoningRequest(mode="TARGET", target="X"),  # not a ReasoningMode
        ReasoningRequest.of_target("X", scope="MY_BOOKS"),  # not a ReasoningScope
        ReasoningRequest.of_target("X", inputs=[UserInput("E")]),  # not a tuple
        ReasoningRequest.of_target("X", inputs=("E",)),  # not UserInput values
        ReasoningRequest.of_target("X", inputs=(UserInput(" "),)),  # blank node
        ReasoningRequest.of_target("X", inputs=(UserInput("E", "  "),)),  # blank value
        ReasoningRequest.of_target("X", assumptions=(Assumption("REL-00000001"),)),  # wrong kind
        ReasoningRequest.of_target("X", assumptions=(Assumption("B", ""),)),  # blank statement
    ],
)
def test_a_request_that_cannot_be_answered_as_asked_is_invalid(request_):
    _invalid(request_.check)


def test_an_invalid_request_is_exit_code_2_at_the_command_line():
    """ADR 0040 P10-28: the CLI maps an invalid-input failure to exit code 2."""
    error = _invalid(ReasoningRequest(mode=ReasoningMode.TARGET).check)
    assert int(_EXIT_BY_CATEGORY[error.report.category]) == 2


# --------------------------------------------------------- admission identifiers


@pytest.mark.parametrize("identifier", ["K-12", "not-an-identifier", "ZZ-00000001", "", "k-00000001"])
def test_a_malformed_admission_identifier_is_invalid(identifier):
    error = _invalid(ReasoningRequest.of_target("X", admissions=(Admission("D", identifier),)).check)
    assert "is not an identifier" in error.report.summary


@pytest.mark.parametrize(
    "identifier",
    [
        format_id(EntityKind.CONCEPT, 1),
        format_id(EntityKind.RELATIONSHIP, 1),
        format_id(EntityKind.DOCUMENT, 1),
        format_id(EntityKind.SOURCE_OCCURRENCE, 1),
    ],
)
def test_a_wrong_kind_admission_identifier_is_invalid(identifier):
    """Only knowledge objects can be admitted (ADR 0040 P10-27)."""
    error = _invalid(ReasoningRequest.of_target("X", admissions=(Admission("D", identifier),)).check)
    assert "wrong kind" in error.report.summary


def test_a_nonexistent_admitted_item_makes_the_request_invalid(repo):
    _library(_Rows(repo))
    missing = format_id(EntityKind.KNOWLEDGE_OBJECT, 999_999)
    request = ReasoningRequest.of_target("X", admissions=(Admission("D", missing),))
    error = _invalid(lambda: initial_availability(repo, request))
    assert "does not exist" in error.report.summary


# ---------------------------------------------------------------- node resolution


@pytest.mark.parametrize(
    "request_",
    [
        ReasoningRequest.of_target("X", inputs=(UserInput("Nowhere"),)),
        ReasoningRequest.of_target("X", admissions=(Admission("Nowhere", "K-00000001"),)),
        ReasoningRequest.of_target("X", assumptions=(Assumption("Nowhere"),)),
        ReasoningRequest.of_target("X", inputs=(UserInput(format_id(EntityKind.CONCEPT, 999_999)),)),
    ],
)
def test_an_unresolved_node_makes_the_request_invalid(repo, request_):
    n = _library(_Rows(repo))
    if request_.admissions:
        request_ = replace(request_, admissions=(Admission("Nowhere", n.def_d.id),))
    error = _invalid(lambda: initial_availability(repo, request_))
    assert "does not resolve" in error.report.summary


def test_a_node_resolves_by_identifier_or_exact_name_and_every_match_is_listed(repo):
    rows = _Rows(repo)
    n = _library(rows)
    other_e = rows.concept("E")  # a second concept answering to "E", as another run stores it

    by_name = initial_availability(repo, ReasoningRequest.of_target("X", inputs=(UserInput("e"),)))
    assert [i.concept.id for i in by_name.inputs] == [n.e.id, other_e.id]  # D-30; none chosen
    by_id = initial_availability(repo, ReasoningRequest.of_target("X", inputs=(UserInput(n.e.id),)))
    assert [i.concept.id for i in by_id.inputs] == [n.e.id]


def test_deleted_and_archived_concepts_do_not_resolve(repo):
    rows = _Rows(repo)
    _library(rows)
    for status in (LifecycleStatus.DELETED, LifecycleStatus.ARCHIVED):
        gone = rows.concept(f"Gone {status.value}")
        repo.update(replace(gone, lifecycle_status=status))
        for reference in (gone.id, gone.canonical_name):
            request = ReasoningRequest.of_target("X", inputs=(UserInput(reference),))
            _invalid(lambda: initial_availability(repo, request))


# -------------------------------------------------- OI-1 = Option B: availability


def test_stored_knowledge_is_not_available_merely_because_it_exists(repo):
    """D has an authorised stored definition, linked by a stated edge; unless the
    request admits it, D is not available (OI-1 = Option B)."""
    n = _library(_Rows(repo))

    found = initial_availability(repo, ReasoningRequest.of_target("X", inputs=(UserInput("E"),)))

    assert found.is_available(n.e.id)
    assert not found.is_available(n.d.id)
    assert [entry.concept_id for entry in found.available] == [n.e.id]
    assert found.admitted == () and found.refused == ()


def test_a_user_input_is_available_with_its_value_carried_verbatim(repo):
    n = _library(_Rows(repo))
    value = "  E = 10 V (as given)  "

    found = initial_availability(repo, ReasoningRequest.forward(inputs=(UserInput("E", value),)))

    (given,) = found.inputs
    assert (given.reference, given.concept, given.value) == ("E", n.e, value)
    assert given.origin is Origin.USER_INPUT
    assert found.origins_of(n.e.id) == (Origin.USER_INPUT,)


def test_an_explicitly_admitted_stored_item_is_available_with_its_provenance(repo):
    n = _library(_Rows(repo))

    found = initial_availability(
        repo, ReasoningRequest.of_target("X", admissions=(Admission("D", n.def_d.id),))
    )

    (admitted,) = found.admitted
    assert (admitted.concept, admitted.knowledge) == (n.d, n.def_d)
    assert admitted.origin is Origin.ADMITTED_STORED_ITEM
    assert [row.subject_id for row in admitted.evidence] == [n.def_d.id]
    assert admitted.provenance.sources == (n.src_a,)
    assert [d.document for d in admitted.provenance.documents] == [n.book_a]
    assert admitted.provenance.documents[0].preserved_file_present is False  # no file on disk
    assert admitted.provenance.runs == (n.run_a,)
    assert admitted.superseded_by is None
    assert found.origins_of(n.d.id) == (Origin.ADMITTED_STORED_ITEM,)
    assert found.available[0].knowledge_id == n.def_d.id


def test_an_admission_is_the_requests_statement_and_needs_no_stored_link(repo):
    """B's definition is admitted for X, to which no stored relationship links it: no
    link is required, checked or inferred, and no requirement changes (P10-27)."""
    n = _library(_Rows(repo))
    before = repo.connection.total_changes

    found = initial_availability(
        repo, ReasoningRequest.of_target("X", admissions=(Admission("X", n.def_b.id),))
    )

    assert [(a.concept.id, a.knowledge.id) for a in found.admitted] == [(n.x.id, n.def_b.id)]
    assert repo.connection.total_changes == before  # nothing written, no edge added


def test_user_input_and_an_admitted_item_stay_distinct_origins(repo):
    n = _library(_Rows(repo))

    found = initial_availability(
        repo,
        ReasoningRequest.of_target(
            "X", inputs=(UserInput("D", "given"),), admissions=(Admission("D", n.def_d.id),)
        ),
    )

    assert found.origins_of(n.d.id) == (Origin.USER_INPUT, Origin.ADMITTED_STORED_ITEM)
    # Only the admitted item carries stored provenance; a USER_INPUT never does.
    assert {f.name for f in fields(SuppliedInput)} == {"reference", "concept", "value"}
    assert found.admitted[0].provenance.sources == (n.src_a,)
    plain = to_json(found)
    assert '"origin": "USER_INPUT"' in plain and '"origin": "ADMITTED_STORED_ITEM"' in plain


def test_an_assumption_is_labelled_and_separate_from_inputs(repo):
    n = _library(_Rows(repo))

    found = initial_availability(
        repo, ReasoningRequest.of_target("X", assumptions=(Assumption("B", "B holds."),))
    )

    (stated,) = found.assumptions
    assert (stated.concept, stated.statement, stated.origin) == (n.b, "B holds.", Origin.ASSUMPTION)
    assert found.inputs == () and found.origins_of(n.b.id) == (Origin.ASSUMPTION,)


# --------------------------------------------------- refused and withheld items


def test_deleted_and_archived_items_are_excluded_and_reported(repo):
    rows = _Rows(repo)
    n = _library(rows)
    deleted = n.stored("Deleted claim.", n.src_a, status=LifecycleStatus.DELETED)
    archived = n.stored("Archived claim.", n.src_a, status=LifecycleStatus.ARCHIVED)

    found = initial_availability(
        repo,
        ReasoningRequest.of_target(
            "X", admissions=(Admission("D", deleted.id), Admission("B", archived.id))
        ),
    )

    assert found.admitted == ()
    assert [(r.concept.id, r.knowledge.id, r.reason) for r in found.refused] == [
        (n.b.id, archived.id, RefusalReason.EXCLUDED_BY_LIFECYCLE),
        (n.d.id, deleted.id, RefusalReason.EXCLUDED_BY_LIFECYCLE),
    ]
    assert not found.is_available(n.d.id) and not found.is_available(n.b.id)
    assert found.withheld == 0  # excluded by lifecycle, not withheld by authorisation


def test_unauthorised_out_of_scope_and_evidence_free_items_are_withheld(repo):
    rows = _Rows(repo)
    n = _library(rows)
    closed = rows.document("closed-book")
    unauthorised = n.stored(
        "From a source not authorised.", rows.source(closed, authorization=Authorization.NOT_AUTHORIZED)
    )
    external = n.stored(
        "From an authorised external source.",
        rows.source(rows.document("external"), category=SourceCategory.AUTHORIZED_EXTERNAL_SOURCE),
    )
    bare = n.stored("Stored without any evidence.")
    admissions = (Admission("A", unauthorised.id), Admission("B", external.id), Admission("C", bare.id))

    found = initial_availability(repo, ReasoningRequest.of_target("X", admissions=admissions))

    assert found.admitted == ()
    assert [(r.concept.id, r.reason) for r in found.refused] == [
        (n.a.id, RefusalReason.NOT_AUTHORIZED),
        (n.b.id, RefusalReason.OUT_OF_SCOPE),
        (n.c.id, RefusalReason.NO_EVIDENCE),
    ]
    assert found.withheld == 3 and found.available == ()

    # The authorised external item is in the wider AUTHORIZED scope (P9-5).
    wider = initial_availability(
        repo,
        ReasoningRequest.of_target("X", admissions=admissions, scope=ReasoningScope.AUTHORIZED),
    )
    assert [(a.concept.id, a.knowledge.id) for a in wider.admitted] == [(n.b.id, external.id)]


def test_an_admitted_item_keeps_only_its_evidence_in_scope(repo):
    """Unauthorised evidence is never used (P9-5), even for an admitted item."""
    rows = _Rows(repo)
    n = _library(rows)
    hidden = rows.source(rows.document("closed"), authorization=Authorization.NOT_AUTHORIZED)
    rows.knowledge_occurrence(n.def_d, hidden, page=2)

    found = initial_availability(
        repo, ReasoningRequest.of_target("X", admissions=(Admission("D", n.def_d.id),))
    )

    (admitted,) = found.admitted
    assert {row.source_id for row in admitted.evidence} == {n.src_a.id}
    assert admitted.provenance.sources == (n.src_a,)


def test_a_superseded_item_is_admitted_labelled_with_its_stored_pointer(repo):
    rows = _Rows(repo)
    n = _library(rows)
    duplicate = n.stored("D is a stated quantity (twin).", n.src_a, span=(50, 70))
    rows.supersede(n.def_d, duplicate)

    found = initial_availability(
        repo, ReasoningRequest.of_target("X", admissions=(Admission("D", duplicate.id),))
    )

    (admitted,) = found.admitted
    assert admitted.knowledge.lifecycle_status is LifecycleStatus.SUPERSEDED
    assert admitted.superseded_by == n.def_d.id


# ------------------------------------------------------------ scope parity (P9-5)


def test_reasoning_scope_verdicts_equal_phase_9s_on_the_same_sources(repo):
    """ADR 0040 P10-26: re-applied, not imported - and the same verdict every time."""
    rows = _Rows(repo)
    book = rows.document("book")
    for category in SourceCategory:
        for authorization in Authorization:
            source = rows.source(book, category=category, authorization=authorization)
            for scope in ReasoningScope:
                ours = source_verdict(source, scope)
                theirs = phase_9_verdict(source, SourceScope(scope.value))
                assert ours.value == theirs.value, (category, authorization, scope)
    assert source_verdict(None, ReasoningScope.AUTHORIZED).value == "NOT_AUTHORIZED"


# ------------------------------------------------ determinism and read-only use


def test_the_result_is_deterministic_whatever_order_the_request_gives(repo):
    n = _library(_Rows(repo))
    items = {
        "inputs": (UserInput("E", "1"), UserInput("C"), UserInput(n.a.id)),
        "admissions": (Admission("D", n.def_d.id), Admission("B", n.def_b.id)),
        "assumptions": (Assumption("X", "assumed"), Assumption("B")),
    }
    one = ReasoningRequest.of_target("X", **items)
    other = ReasoningRequest.of_target("X", **{k: tuple(reversed(v)) for k, v in items.items()})

    first = to_json(initial_availability(repo, one))
    assert to_json(initial_availability(repo, one)) == first  # byte-identical every time
    assert to_json(initial_availability(repo, other)) == first
    found = initial_availability(repo, one)
    assert [i.concept.id for i in found.inputs] == [n.a.id, n.c.id, n.e.id]  # numeric order
    assert [a.concept.id for a in found.admitted] == [n.b.id, n.d.id]


def test_initial_availability_writes_nothing_and_runs_on_a_read_only_connection(repo, db_path):
    rows = _Rows(repo)
    n = _library(rows)
    rows.commit()
    request = ReasoningRequest.of_target(
        "X",
        inputs=(UserInput("E"),),
        admissions=(Admission("D", n.def_d.id),),
        assumptions=(Assumption("B"),),
    )

    before = repo.connection.total_changes
    answer = to_json(initial_availability(repo, request))
    assert repo.connection.total_changes == before

    reader = connect(db_path, read_only=True)
    try:
        assert to_json(initial_availability(Repository(reader), request)) == answer
    finally:
        reader.close()
