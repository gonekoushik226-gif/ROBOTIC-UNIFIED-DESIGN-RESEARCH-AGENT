"""Phase 9 steps 2-3: the structured request, source scope, authorisation and provenance.

ADR 0035 P9-5 (authorisation and "my books"), ADR 0036 P9-18 (provenance), P9-20
(stored availability and file presence as separate facts), P9-23 (the request's
filters), ADR 0035 P9-6 (every run disclosed, none preferred).
"""

from __future__ import annotations

import pytest

from app.core.errors import InvalidInputError
from app.deduplication.editions import declare_edition
from app.models import (
    Authorization,
    Source,
    SourceAvailability,
    SourceCategory,
)
from app.models.base import utc_now
from app.query import AnswerStatus, QueryEngine, QueryFilters, QueryRequest, SourceScope
from app.query.provenance import cited_rows
from app.query.scope import Verdict, source_verdict
from tests.unit.query_rows import QueryRows, mosfet_library


def _engine(repo, db_path) -> QueryEngine:
    return QueryEngine(repo.connection, database_path=db_path)


# ------------------------------------------------------------ requests (step 2)


@pytest.mark.parametrize(
    "request_",
    [
        QueryRequest.concept("   "),
        QueryRequest(mode="CONCEPT", name="MOSFET"),  # a string, not a QueryMode
        QueryRequest(mode=QueryRequest.concept("x").mode, name="MOSFET", identifier="K-00000001"),
        QueryRequest.exact("S-00000001"),  # an occurrence: not an exact-search kind
        QueryRequest.exact("MOSFET"),
        QueryRequest.page("K-00000001", 1),
        QueryRequest.page("DOC-00000001", -1),
        QueryRequest.concept("MOSFET", filters=QueryFilters(knowledge_types=("DEFINITION",))),
        QueryRequest.concept("MOSFET", filters=QueryFilters(document_ids=("book-a",))),
        QueryRequest.concept("MOSFET", filters=QueryFilters(pages=(-2,))),
        QueryRequest.concept("MOSFET", scope="MY_BOOKS"),
    ],
)
def test_a_request_that_cannot_be_answered_as_asked_is_refused(request_):
    with pytest.raises(InvalidInputError) as caught:
        request_.check()
    assert caught.value.report.data_changed is False


def test_a_request_is_checked_before_the_database_is_read(repo, db_path):
    before = repo.connection.total_changes
    with pytest.raises(InvalidInputError):
        _engine(repo, db_path).run(QueryRequest.concept(""))
    assert repo.connection.total_changes == before


# ------------------------------------------------------ authorisation (step 3)


def _source(category: SourceCategory, authorization: Authorization) -> Source:
    now = utc_now()
    return Source(
        id="SRC-00000001", created_at=now, updated_at=now, name="s",
        source_category=category, authorization=authorization,
        availability=SourceAvailability.AVAILABLE,
    )


@pytest.mark.parametrize(
    ("category", "authorization", "my_books", "authorized"),
    [
        (SourceCategory.USER_PROVIDED_SOURCE, Authorization.AUTHORIZED, Verdict.IN_SCOPE, Verdict.IN_SCOPE),
        (SourceCategory.LOCAL_SOURCE, Authorization.AUTHORIZED, Verdict.IN_SCOPE, Verdict.IN_SCOPE),
        (SourceCategory.AUTHORIZED_EXTERNAL_SOURCE, Authorization.AUTHORIZED, Verdict.OUT_OF_SCOPE, Verdict.IN_SCOPE),
        (SourceCategory.SYSTEM_DERIVED, Authorization.AUTHORIZED, Verdict.OUT_OF_SCOPE, Verdict.IN_SCOPE),
        (SourceCategory.USER_PROVIDED_SOURCE, Authorization.NOT_AUTHORIZED, Verdict.NOT_AUTHORIZED, Verdict.NOT_AUTHORIZED),
        (SourceCategory.UNAUTHORIZED_SOURCE, Authorization.AUTHORIZED, Verdict.NOT_AUTHORIZED, Verdict.NOT_AUTHORIZED),
    ],
)
def test_scope_is_read_from_the_stored_columns(category, authorization, my_books, authorized):
    source = _source(category, authorization)
    assert source_verdict(source, SourceScope.MY_BOOKS) is my_books
    assert source_verdict(source, SourceScope.AUTHORIZED) is authorized
    assert source_verdict(None, SourceScope.AUTHORIZED) is Verdict.NOT_AUTHORIZED


def test_evidence_from_an_unauthorised_source_is_never_returned_and_is_counted(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    for scope in SourceScope:
        result = _engine(repo, db_path).run(QueryRequest.concept("MOSFET", scope=scope))
        assert result.status is AnswerStatus.FOUND
        assert n.src_c.id not in {row.source_id for row in cited_rows(result)}
        assert n.hidden.statement not in str(result)
        assert [rc.concept.id for rc in result.concept.concepts] == [n.mosfet_a.id, n.mosfet_b.id]
        assert result.withheld.unauthorized_evidence > 0
        assert result.withheld.items_without_authorized_evidence >= 1  # the book C concept
        assert "withheld" in result.message


def test_my_books_leaves_out_an_authorised_external_source(repo, db_path):
    rows = QueryRows(repo)
    local, external = rows.document("local"), rows.document("external")
    near = rows.source(local, category=SourceCategory.LOCAL_SOURCE)
    far = rows.source(external, category=SourceCategory.AUTHORIZED_EXTERNAL_SOURCE)
    diode, thyristor = rows.concept("Diode"), rows.concept("Thyristor")
    rows.concept_occurrence(diode, near, page=1)
    rows.concept_occurrence(thyristor, far, page=1)
    rows.commit()
    engine = _engine(repo, db_path)

    assert engine.run(QueryRequest.concept("Diode")).status is AnswerStatus.FOUND
    mine = engine.run(QueryRequest.concept("Thyristor"))
    assert mine.status is AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION
    assert mine.message.startswith("Insufficient authorized information")
    assert mine.concept is None and mine.withheld.out_of_scope_evidence == 1
    wider = engine.run(QueryRequest.concept("Thyristor", scope=SourceScope.AUTHORIZED))
    assert wider.status is AnswerStatus.FOUND


def test_a_concept_known_only_to_an_unauthorised_source_is_insufficient_information(repo, db_path):
    rows = QueryRows(repo)
    book = rows.document("book")
    source = rows.source(book, authorization=Authorization.NOT_AUTHORIZED)
    rows.concept_occurrence(rows.concept("Magnetron"), source, page=1)
    rows.concept("Klystron")  # no evidence stored at all
    rows.commit()

    for name in ("Magnetron", "Klystron"):
        result = _engine(repo, db_path).run(QueryRequest.concept(name))
        assert result.status is AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION
        assert result.concept is None and result.trace.resolved == ()
    assert _engine(repo, db_path).run(QueryRequest.concept("Magnetron")).withheld.items_without_authorized_evidence == 1
    assert _engine(repo, db_path).run(QueryRequest.concept("Klystron")).withheld.items_without_evidence == 1


# --------------------------------------------------------- provenance (step 3)


def test_provenance_holds_every_cited_row_context_exactly_as_stored(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    section = _engine(repo, db_path).run(QueryRequest.concept("MOSFET")).concept

    assert section.provenance.sources == (n.src_a, n.src_b)
    assert [d.document for d in section.provenance.documents] == [n.book_a, n.book_b]
    assert section.provenance.runs == (n.run_a, n.run_b)
    assert [(r.status.value, r.extractor_version) for r in section.provenance.runs] == [
        ("COMPLETED", "4"), ("PARTIAL", "3"),
    ]
    # Every cited row's source, document and run is in the provenance - and nothing else.
    cited = cited_rows((section.concepts, section.groups, section.conflicts))
    assert {row.source_id for row in cited} == {s.id for s in section.provenance.sources}
    assert {row.document_id for row in cited} == {d.document.id for d in section.provenance.documents}


def test_file_presence_is_a_separate_fact_from_stored_availability(repo, db_path, tmp_path):
    rows = QueryRows(repo)
    preserved = tmp_path / "preserved.pdf"
    preserved.write_bytes(b"%PDF-1.4 stand-in")
    kept, gone = rows.document("kept", file_path=str(preserved)), rows.document("gone")
    for document, name in ((kept, "Varactor"), (gone, "Varistor")):
        rows.concept_occurrence(rows.concept(name), rows.source(document), page=1)
    rows.commit()
    engine = _engine(repo, db_path)

    present = engine.run(QueryRequest.concept("Varactor")).concept.provenance
    absent = engine.run(QueryRequest.concept("Varistor")).concept.provenance
    assert present.documents[0].preserved_file_present is True
    assert absent.documents[0].preserved_file_present is False
    # Stored availability is shown as stored in both cases; no label is derived.
    assert present.sources[0].availability is absent.sources[0].availability is SourceAvailability.AVAILABLE
    assert "ACCESSIBLE" not in str(absent) and "SOURCE_UNAVAILABLE" not in str(absent)


def test_declared_editions_are_part_of_provenance(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    declare_edition(repo, edition_id=n.book_b.id, work_id=n.book_a.id,
                    label="2nd edition", work_label="1st edition")
    repo.connection.commit()
    documents = _engine(repo, db_path).run(QueryRequest.concept("MOSFET")).concept.provenance.documents

    labels = {d.document.id: [v.version_label for v in d.editions] for d in documents}
    assert labels == {n.book_a.id: ["1st edition"], n.book_b.id: ["2nd edition"]}
    assert {v.document_id for d in documents for v in d.editions} == {n.book_a.id}


def test_partial_and_pre_stage_15_runs_are_disclosed_not_preferred(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    result = _engine(repo, db_path).run(QueryRequest.concept("MOSFET"))

    assert any("PARTIAL" in note and n.run_b.id in note for note in result.notes)
    assert any("extractor version below 4" in note and n.run_b.id in note for note in result.notes)
    # Both runs' evidence is returned: no current-run preference (P9-6).
    definition = result.concept.groups[0].items[0]
    assert {row.extraction_run_id for row in definition.evidence} == {n.run_a.id, n.run_b.id}


def test_number_of_sources_is_informational_and_counts_evidence_in_scope(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    definition = _engine(repo, db_path).run(QueryRequest.concept("MOSFET")).concept.groups[0].items[0]

    assert definition.knowledge == n.definition
    assert (definition.source_occurrences, definition.number_of_sources) == (2, 2)
