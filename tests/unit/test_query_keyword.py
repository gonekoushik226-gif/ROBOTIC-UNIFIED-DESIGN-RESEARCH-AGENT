"""Phase 9 step 10: keyword retrieval through the derived index.

ADR 0037 P9-25 (every hit re-read from `knowledge.db`), P9-29 (only keyword mode needs
the index; missing or stale is refused, never repaired), P9-30 (the Step 0 tokenizer:
D-30 first, every term one quoted string, a prefix only as `"term"*`), ADR 0035 P9-3
(a keyword hit is never a concept link) and P9-5 (unauthorised evidence never returned).
"""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from types import SimpleNamespace

import pytest

from app.core.errors import InvalidInputError, StorageError
from app.models import (
    Authorization,
    KnowledgeObject,
    KnowledgeType,
    LifecycleStatus,
    SourceCategory,
)
from app.query import AnswerStatus, QueryEngine, QueryFilters, QueryRequest, SourceScope, build_index, to_json
from app.query.keyword import KEYWORD_NOTE, TOKENIZER_NOTE
from app.query.results import KEYWORD_LABEL, IndexState
from app.storage import connect, queries
from tests.unit.query_rows import QueryRows

OHM_SIGN, MICRO_SIGN = "Ω", "µ"


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def world(repo, db_path, tmp_path):
    """Probe texts in three books - authorised, not authorised, out of 'my books' - indexed."""
    rows = QueryRows(repo)
    n = SimpleNamespace(rows=rows, db_path=db_path)
    n.book, n.secret_book, n.web = (rows.document(name) for name in ("book", "secret", "web"))
    n.src = rows.source(n.book)
    n.secret = rows.source(n.secret_book, authorization=Authorization.NOT_AUTHORIZED)
    n.external = rows.source(n.web, category=SourceCategory.AUTHORIZED_EXTERNAL_SOURCE)
    n.run = rows.run(n.book)

    def said(statement, source=None, *, kind=KnowledgeType.DEFINITION, status=LifecycleStatus.ACTIVE):
        knowledge = rows.knowledge(kind, statement, status=status)
        rows.knowledge_occurrence(knowledge, source or n.src, page=1, run=n.run if source is None else None)
        return knowledge

    n.mosfet = said("A MOSFET is a voltage-controlled transistor.")
    n.plural = said("MOSFETs are used in amplifiers.")
    n.ohm = said(f"The load is 10 {OHM_SIGN}.")
    n.micro = said(f"A 4.7 {MICRO_SIGN}F capacitor.")
    n.r1 = said("R₁ is the input resistor.")
    n.vgs = said("V_GS is the gate-source voltage.")
    n.flip = said("A flip–flop toggles.")
    n.cafe = said("The café sells coffee.")
    n.strasse = said("Die Straße.")
    n.equation = said("I_D = k (V_GS - V_T)^2", kind=KnowledgeType.EQUATION)
    n.archived = said("An archived relay note.", status=LifecycleStatus.ARCHIVED)
    n.hidden = said("A MOSFET is a vacuum tube.", n.secret)
    n.leaky = said("MOSFET gates leak at high temperature.", n.external)
    n.diode = said("A diode conducts in one direction.")
    # The same object, stated in other words by an unauthorised source.
    n.whisper = rows.knowledge_occurrence(
        n.diode, n.secret, page=3, text="A diode conducts one way, secretly.")
    n.concept = rows.concept("MOSFET")
    rows.concept_occurrence(n.concept, n.src, page=1)
    n.page = rows.segment(n.book, 1, text="Page one is about MOSFET devices.")
    n.secret_page = rows.segment(n.secret_book, 1, text="A secret page about MOSFET devices.")
    rows.commit()
    (tmp_path / "indexes").mkdir()
    n.index = tmp_path / "indexes" / "index.db"
    n.reader = connect(db_path, read_only=True)
    build_index(n.reader, n.index)
    yield n
    n.reader.close()


def _run(world, term: str, **options):
    engine = QueryEngine(world.reader, database_path=world.db_path, index_path=world.index)
    return engine.run(QueryRequest.keyword(term, **options))


def _knowledge_ids(result) -> list[str]:
    return [hit.knowledge.id for hit in result.keyword.knowledge]


# ------------------------------------------------------------ terms and tokens


def test_an_exact_term_matches_the_word_and_nothing_more(world):
    result = _run(world, "MOSFET")

    assert result.status is AnswerStatus.FOUND
    assert _knowledge_ids(result) == [world.mosfet.id]  # not "MOSFETs"; not the unauthorised one
    assert result.keyword.expression == '"mosfet"'
    assert [hit.concept.id for hit in result.keyword.concepts] == [world.concept.id]
    assert [hit.segment.id for hit in result.keyword.pages] == [world.page.id]


def test_a_prefix_is_asked_for_explicitly(world):
    result = _run(world, "MOSFET", prefix=True)
    assert result.keyword.expression == '"mosfet"*'
    assert _knowledge_ids(result) == [world.mosfet.id, world.plural.id]
    assert _run(world, "MOSFET*").keyword.expression == '"mosfet*"'  # a star is text, not syntax


@pytest.mark.parametrize(
    ("term", "found"),
    [
        ("Ω", "ohm"),  # GREEK CAPITAL OMEGA finds the OHM SIGN
        ("μF", "micro"),  # GREEK MU finds the MICRO SIGN
        ("R1", "r1"),  # NFKC: a subscript one is a one
        ("strasse", "strasse"),  # casefold: ß is ss
        ("mOsFeT", "mosfet"),
        ("flip-flop", "flip"),  # a hyphen and an en dash both separate
        ("V GS", "vgs"),
    ],
)
def test_d30_and_the_tokenizer_decide_what_matches(world, term, found):
    assert getattr(world, found).id in _knowledge_ids(_run(world, term))


@pytest.mark.parametrize("term", ["cafe", "VGS", "uF", "flipflop", "°", "±"])
def test_the_recorded_tokenizer_limits_hold(world, term):
    result = _run(world, term)
    assert result.status is AnswerStatus.NOT_FOUND and result.keyword.knowledge == ()


def test_mixed_case_gives_the_same_answer(world):
    upper, mixed = _run(world, "MOSFET"), _run(world, "mOsFeT")
    assert upper.keyword.knowledge == mixed.keyword.knowledge
    assert upper.keyword.expression == mixed.keyword.expression


@pytest.mark.parametrize(
    "build",
    [
        lambda: QueryRequest.keyword("   "),
        lambda: QueryRequest.keyword(""),
        lambda: QueryRequest.keyword("MOSFET", prefix="yes"),
        lambda: QueryRequest(mode=QueryRequest.concept("x").mode, name="MOSFET", prefix=True),
        lambda: QueryRequest(mode=QueryRequest.keyword("x").mode, term="MOSFET", name="MOSFET"),
    ],
)
def test_an_empty_or_malformed_keyword_request_is_refused(world, build):
    with pytest.raises(InvalidInputError):
        QueryEngine(world.reader, database_path=world.db_path, index_path=world.index).run(build())


@pytest.mark.parametrize(
    "term",
    ['mosfet" OR "diode', "NOT mosfet", "body:mosfet", "mosfet)", "NEAR(mosfet transistor)",
     "*", '"', "'; DROP TABLE entry; --", "mosfet AND diode", "^mosfet"],
)
def test_user_text_is_never_read_as_fts5_or_sql_syntax(world, term):
    index_before = _sha256(world.index)
    result = _run(world, term)
    again = _run(world, term)
    assert to_json(result) == to_json(again)
    assert result.keyword.expression.startswith('"') and result.keyword.expression.endswith('"')
    if term in ('mosfet" OR "diode', "NOT mosfet", "mosfet AND diode"):
        assert result.status is AnswerStatus.NOT_FOUND  # a phrase, not an operator
    assert _sha256(world.index) == index_before
    with closing(connect(world.index, read_only=True)) as reader:  # the table is still there
        assert reader.execute("SELECT count(*) FROM entry").fetchone()[0] > 0


# ----------------------------------------------------- authority and scope


def test_every_hit_is_re_read_from_knowledge_db(world):
    result = _run(world, "voltage-controlled transistor")
    (hit,) = result.keyword.knowledge
    assert hit.knowledge == world.rows.repo.get(KnowledgeObject, world.mosfet.id)
    stored = [row["id"] for row in queries.evidence_for(world.reader, world.mosfet.id)]
    assert [row.id for row in hit.evidence] == stored
    assert hit.statement_matched is True and hit.label == KEYWORD_LABEL


def test_an_unlinked_equation_is_reachable_only_as_a_labelled_keyword_hit(world):
    result = _run(world, "V_GS")
    ids = _knowledge_ids(result)
    assert world.equation.id in ids and world.vgs.id in ids
    hit = next(h for h in result.keyword.knowledge if h.knowledge.id == world.equation.id)
    assert hit.label == KEYWORD_LABEL and "not a stored link to any concept" in hit.label
    assert result.trace.resolved == () and result.trace.links == ()  # nothing resolved or linked


def test_unauthorised_evidence_is_never_returned(world):
    result = _run(world, "MOSFET", scope=SourceScope.AUTHORIZED)
    text = to_json(result)
    assert world.hidden.id not in text and "vacuum tube" not in text
    assert world.secret_page.id not in text and world.secret.id not in text
    assert result.withheld.unauthorized_evidence > 0
    only_secret = _run(world, "vacuum tube")
    assert only_secret.status is AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION


def test_a_match_only_in_unauthorised_text_is_withheld_though_the_object_is_authorised(world):
    result = _run(world, "secretly")
    assert result.status is AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION
    shown = to_json(result)
    assert result.keyword.knowledge == ()
    assert world.whisper.id not in shown and "one way, secretly" not in shown
    assert world.diode.statement not in shown  # not even the object's own statement
    # The same object is found by its authorised text, with its authorised evidence only.
    (hit,) = _run(world, "one direction").keyword.knowledge
    assert hit.knowledge.id == world.diode.id
    assert {row.source_id for row in hit.evidence} == {world.src.id}


def test_my_books_leaves_out_an_authorised_external_source(world):
    mine = _run(world, "gates leak")
    assert mine.status is AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION
    assert mine.withheld.out_of_scope_evidence > 0
    wider = _run(world, "gates leak", scope=SourceScope.AUTHORIZED)
    assert _knowledge_ids(wider) == [world.leaky.id]


def test_filters_and_the_lifecycle_default_apply(world):
    assert _run(world, "archived relay note").status is AnswerStatus.NOT_FOUND  # ARCHIVED, by default
    archived = _run(world, "archived relay note",
                    filters=QueryFilters(lifecycle_statuses=(LifecycleStatus.ARCHIVED,)))
    assert _knowledge_ids(archived) == [world.archived.id]
    equations = _run(world, "V_GS", filters=QueryFilters(knowledge_types=(KnowledgeType.EQUATION,)))
    assert _knowledge_ids(equations) == [world.equation.id]
    other_page = _run(world, "MOSFET", filters=QueryFilters(pages=(9,)))
    assert other_page.status is AnswerStatus.NOT_FOUND and "filters" in other_page.message


def test_provenance_and_trace(world):
    result = _run(world, "MOSFET")
    assert result.keyword.provenance.sources == (world.src,)
    assert [d.document.id for d in result.keyword.provenance.documents] == [world.book.id]
    assert result.keyword.pages[0].document.document == world.book
    trace = result.trace
    assert trace.index.startswith(f"used read-only: {world.index} - FRESH")
    assert set(trace.evidence_ids) >= {row.id for row in result.keyword.knowledge[0].evidence}
    assert trace.persisted is False and result.keyword.index.state is IndexState.FRESH
    assert result.notes[:2] == (KEYWORD_NOTE, TOKENIZER_NOTE)


def test_repeated_keyword_queries_are_byte_identical(world):
    first = to_json(_run(world, "MOSFET", prefix=True))
    assert to_json(_run(world, "MOSFET", prefix=True)) == first
    with closing(connect(world.db_path, read_only=True)) as other:
        fresh = QueryEngine(other, database_path=world.db_path, index_path=world.index)
        assert to_json(fresh.run(QueryRequest.keyword("MOSFET", prefix=True))) == first

    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    # No field carries a score or a rank: order is by stored attributes (P9-22).
    assert not {k for k in keys(json.loads(first)) if any(w in k for w in ("score", "rank", "bm25"))}


# ------------------------------------------------------- the index lifecycle


def test_a_missing_index_is_refused_and_never_created(world, tmp_path):
    absent = tmp_path / "indexes" / "absent.db"
    engine = QueryEngine(world.reader, database_path=world.db_path, index_path=absent)
    with pytest.raises(StorageError) as caught:
        engine.run(QueryRequest.keyword("MOSFET"))
    assert caught.value.report.detail.startswith("MISSING") and not absent.exists()
    unconfigured = QueryEngine(world.reader, database_path=world.db_path)
    with pytest.raises(StorageError):
        unconfigured.run(QueryRequest.keyword("MOSFET"))


def test_a_stale_index_stays_refused_and_is_never_repaired(world):
    before = _sha256(world.index)
    world.rows.knowledge(KnowledgeType.EXAMPLE, "A MOSFET switches a lamp.")
    world.rows.commit()
    for _ in range(2):
        with pytest.raises(StorageError) as caught:
            _run(world, "MOSFET")
        assert caught.value.report.detail.startswith("STALE")
        assert "knowledge_object changed" in caught.value.report.reason
    assert _sha256(world.index) == before


def test_keyword_queries_write_nothing(world):
    connection = world.rows.repo.connection
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    database, index = _sha256(world.db_path), _sha256(world.index)
    for term in ("MOSFET", "V_GS", "vacuum tube"):
        _run(world, term)
    assert (_sha256(world.db_path), _sha256(world.index)) == (database, index)
    assert world.reader.total_changes == 0


def test_the_other_modes_never_open_the_index(world):
    world.index.write_bytes(b"not an index at all")  # a broken index cannot matter to them
    with_index = QueryEngine(world.reader, database_path=world.db_path, index_path=world.index)
    without = QueryEngine(world.reader, database_path=world.db_path)
    for request in (
        QueryRequest.concept("MOSFET"),
        QueryRequest.exact(world.mosfet.id),
        QueryRequest.page(world.book.id, 1),
    ):
        answered = with_index.run(request)
        assert answered.status is AnswerStatus.FOUND
        assert to_json(answered) == to_json(without.run(request))
        assert answered.trace.index.startswith("not used")
