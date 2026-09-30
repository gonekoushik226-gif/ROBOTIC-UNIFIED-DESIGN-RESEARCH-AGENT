"""Phase 9 step 7: exact search, page retrieval, filters and lifecycle handling.

ADR 0036 P9-21 (pages), P9-23 (filters and lifecycle defaults), P9-24 (exact search
and the answers that are not found or insufficient). Absence is an answer; nothing
is guessed.
"""

from __future__ import annotations

import pytest

from app.core.errors import InvalidInputError
from app.deduplication.editions import declare_edition
from app.models import (
    Authorization,
    ExtractionRunStatus,
    KnowledgeType,
    LifecycleStatus,
    RelationshipOrigin,
    RelationType,
    SourceCategory,
)
from app.query import AnswerStatus, QueryEngine, QueryFilters, QueryRequest, to_json
from app.query.results import EdgeItem, KnowledgeItem
from app.query.traversal import R1_LABEL
from tests.unit.query_rows import QueryRows, mosfet_library


def _engine(repo, db_path) -> QueryEngine:
    return QueryEngine(repo.connection, database_path=db_path)


def _group(section, name):
    return next(group for group in section.groups if group.name == name)


def _listed(section) -> list[str]:
    return [
        item.knowledge.id if isinstance(item, KnowledgeItem) else item.edge.relationship.id
        for group in section.groups for item in group.items
    ]


# ------------------------------------------------------------------ exact (P9-24)


def test_exact_knowledge_object_names_every_concept_whose_edge_reaches_it(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    result = _engine(repo, db_path).run(QueryRequest.exact(n.definition.id))

    assert result.status is AnswerStatus.FOUND and result.exact.kind == "KNOWLEDGE_OBJECT"
    item = result.exact.knowledge
    assert item.knowledge == n.definition
    assert [(l.concept_id, l.edge.relationship.id) for l in item.links] == [
        (n.mosfet_a.id, n.defined_a.id), (n.mosfet_b.id, n.defined_b.id),
    ]
    (conflict,) = result.exact.conflicts
    assert conflict.conflict.id == n.hidden_conflict.id and conflict.claim_b.knowledge is None
    assert result.exact.provenance.sources == (n.src_a, n.src_b)


def test_exact_property_finds_its_owner_through_the_reverse_has_property_lookup(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    item = _engine(repo, db_path).run(QueryRequest.exact(n.property.id)).exact.knowledge
    assert [(l.concept_id, l.edge.relationship.relation_type) for l in item.links] == [
        (n.mosfet_a.id, RelationType.HAS_PROPERTY),
    ]


def test_exact_relationship_is_labelled_and_names_its_endpoints(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    engine = _engine(repo, db_path)

    related = engine.run(QueryRequest.exact(n.related.id)).exact
    assert related.relationship.label == R1_LABEL
    assert related.endpoints == (n.mosfet_a, n.bjt)
    assert related.provenance.sources == (n.src_a,)  # through its basis occurrence
    owns = engine.run(QueryRequest.exact(n.owns.id)).exact
    assert owns.endpoints == (n.mosfet_a, n.property)


def _stated_in_a_reaching(rows, *, only_in_u: bool, status=LifecycleStatus.ACTIVE):
    """A concept in authorised book A whose stored edge, stated in A, reaches an object
    whose own evidence is in unauthorised book U (`only_in_u`) or in A."""
    book_a, book_u = rows.document("book-a"), rows.document("book-u")
    src_a = rows.source(book_a)
    src_u = rows.source(book_u, authorization=Authorization.NOT_AUTHORIZED)
    concept = rows.concept("Widget")
    rows.concept_occurrence(concept, src_a, page=1)
    target = rows.knowledge(KnowledgeType.PROPERTY, "The characteristic of a widget is its secret drift.",
                            status=status)
    rows.knowledge_occurrence(target, src_u if only_in_u else src_a, page=1)
    edge = rows.edge(RelationType.HAS_PROPERTY, from_concept_id=concept.id, to_knowledge_id=target.id)
    rows.relationship_occurrence(edge, src_a, page=1)
    rows.commit()
    return concept, target, edge


def test_exact_relationship_withholds_an_endpoint_without_authorised_evidence(repo, db_path):
    """The edge is stated in book A, so it is shown; the object it reaches has evidence
    only in NOT_AUTHORIZED book U, so it is withheld exactly as concept mode withholds it
    (P9-5): its stored identifier stays in the relationship row, its content never shows."""
    concept, hidden, edge = _stated_in_a_reaching(QueryRows(repo), only_in_u=True)
    engine = _engine(repo, db_path)

    result = engine.run(QueryRequest.exact(edge.id))
    assert result.status is AnswerStatus.FOUND
    assert result.exact.endpoints == (concept,)
    assert result.exact.relationship.relationship.to_knowledge_id == hidden.id  # as stored
    assert f"Its endpoint {hidden.id} is withheld" in result.message
    assert "secret drift" not in to_json(result)
    assert result.withheld.items_without_authorized_evidence == 1
    # Provenance is the edge's own source only: nothing of book U.
    assert [s.authorization for s in result.exact.provenance.sources] == [Authorization.AUTHORIZED]
    # Concept mode withholds the same object.
    section = engine.run(QueryRequest.concept("Widget")).concept
    assert hidden.id not in _listed(section)


def test_exact_relationship_endpoint_follows_the_superseded_default_and_filters(repo, db_path):
    concept, old, edge = _stated_in_a_reaching(QueryRows(repo), only_in_u=False,
                                               status=LifecycleStatus.SUPERSEDED)
    engine = _engine(repo, db_path)

    shown = engine.run(QueryRequest.exact(edge.id))  # by default, shown as stored (P9-16)
    assert shown.exact.endpoints == (concept, old) and "secret drift" in to_json(shown)

    for request in (QueryRequest.exact(edge.id, include_superseded=False),
                    QueryRequest.exact(edge.id, filters=QueryFilters(knowledge_types=(KnowledgeType.DEFINITION,)))):
        result = engine.run(request)
        assert result.status is AnswerStatus.FOUND and result.exact.endpoints == (concept,)
        assert "secret drift" not in to_json(result)
        assert f"Its endpoint {old.id} is withheld: excluded by the request's filters" in result.message
        assert result.withheld.items_filtered_out == 1


def test_exact_document_lists_every_run_its_editions_and_its_sources_in_scope(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    declare_edition(repo, edition_id=n.book_b.id, work_id=n.book_a.id,
                    label="2nd edition", work_label="1st edition")
    repo.connection.commit()
    document = _engine(repo, db_path).run(QueryRequest.exact(n.book_b.id)).exact

    assert document.kind == "DOCUMENT" and document.document.document == n.book_b
    assert document.runs == (n.run_b,) and document.runs[0].status is ExtractionRunStatus.PARTIAL
    assert document.sources == (n.src_b,)
    assert [v.version_label for v in document.document.editions] == ["2nd edition"]
    assert document.document.preserved_file_present is False


def test_exact_concept_is_answered_as_a_concept_section(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    result = _engine(repo, db_path).run(QueryRequest.exact(n.mosfet_b.id))

    (resolved,) = result.concept.concepts
    assert resolved.concept == n.mosfet_b and resolved.matched_alias is None
    assert [p.concept.id for p in result.possible_equivalents] == [n.mos.id]


@pytest.mark.parametrize(
    ("which", "status"),
    [
        ("K-99999999", AnswerStatus.NOT_FOUND),
        ("REL-99999999", AnswerStatus.NOT_FOUND),
        ("mosfet_c", AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION),
        ("hidden", AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION),
        ("book_c", AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION),
    ],
)
def test_exact_absence_and_insufficiency_are_answers(repo, db_path, which, status):
    n = mosfet_library(QueryRows(repo))
    identifier = which if "-" in which else getattr(n, which).id
    result = _engine(repo, db_path).run(QueryRequest.exact(identifier))

    assert result.status is status
    assert result.exact is None and result.concept is None
    if status is AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION:
        assert result.message.startswith("Insufficient authorized information")


def test_exact_refuses_an_identifier_kind_it_does_not_search(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    with pytest.raises(InvalidInputError):
        _engine(repo, db_path).run(QueryRequest.exact(n.def_a.id))  # a source occurrence


# ------------------------------------------------------------------ pages (P9-21)


def test_a_page_is_its_stored_text_and_its_occurrences_in_text_order(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    result = _engine(repo, db_path).run(QueryRequest.page(n.book_a.id, 1))

    page = result.page
    assert result.status is AnswerStatus.FOUND and page.segments == (n.seg_a,)
    starts = [o.evidence.char_start for o in page.occurrences]
    assert starts == sorted(starts)
    kinds = {o.evidence.subject_kind for o in page.occurrences}
    assert kinds == {"CONCEPT", "KNOWLEDGE_OBJECT", "RELATIONSHIP"}
    by_row = {o.evidence.id: o.subject for o in page.occurrences}
    assert by_row[n.def_a.id] == n.definition and by_row[n.eq_a.id] == n.equation
    assert page.provenance.sources == (n.src_a,) and page.document.document == n.book_a


def test_a_page_whose_document_has_no_authorised_source_is_withheld(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    result = _engine(repo, db_path).run(QueryRequest.page(n.book_c.id, 1))

    assert result.status is AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION
    assert result.page is None and "withheld" in result.message


def test_an_empty_page_and_an_unknown_document_are_not_found(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    engine = _engine(repo, db_path)
    assert engine.run(QueryRequest.page(n.book_a.id, 99)).status is AnswerStatus.NOT_FOUND
    assert engine.run(QueryRequest.page("DOC-99999999", 1)).status is AnswerStatus.NOT_FOUND


def test_page_filters_narrow_its_occurrences(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    only_properties = QueryFilters(knowledge_types=(KnowledgeType.PROPERTY,))
    page = _engine(repo, db_path).run(QueryRequest.page(n.book_a.id, 1, filters=only_properties)).page

    knowledge = {o.subject.id for o in page.occurrences if o.evidence.subject_kind == "KNOWLEDGE_OBJECT"}
    assert knowledge == {n.property.id}


# ---------------------------------------------------------------- filters (P9-23)


def _concept(repo, db_path, **filters):
    return _engine(repo, db_path).run(QueryRequest.concept("MOSFET", filters=QueryFilters(**filters)))


def test_a_knowledge_type_filter_narrows_the_objects(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    result = _concept(repo, db_path, knowledge_types=(KnowledgeType.PROPERTY,))

    assert _group(result.concept, "Definitions").items == ()
    assert [i.knowledge.id for i in _group(result.concept, "Properties").items] == [n.property.id, n.rival.id]
    assert result.withheld.items_filtered_out >= 1


def test_origin_and_relation_type_filters_narrow_the_edges(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    inferred = _concept(repo, db_path, origins=(RelationshipOrigin.INFERRED,)).concept
    assert _listed(inferred) == [n.related.id]
    uses = _concept(repo, db_path, relation_types=(RelationType.USES,)).concept
    assert _listed(uses) == [n.uses.id, n.used_in.id]


@pytest.mark.parametrize(
    ("filters", "shown"),
    [
        ({"run_ids": "run_a"}, ["mosfet_a"]),
        ({"run_statuses": (ExtractionRunStatus.PARTIAL,)}, ["mosfet_b"]),
        ({"extractor_versions": ("4",)}, ["mosfet_a"]),
        ({"document_ids": "book_b"}, ["mosfet_b"]),
        ({"pages": (3,)}, ["mosfet_b"]),
        ({"source_categories": (SourceCategory.USER_PROVIDED_SOURCE,)}, ["mosfet_a", "mosfet_b"]),
    ],
)
def test_evidence_filters_narrow_which_concepts_have_evidence(repo, db_path, filters, shown):
    n = mosfet_library(QueryRows(repo))
    resolved = {
        name: (getattr(n, value).id,) if isinstance(value, str) else value
        for name, value in filters.items()
    }
    result = _concept(repo, db_path, **resolved)

    assert [rc.concept.id for rc in result.concept.concepts] == [getattr(n, s).id for s in shown]
    if shown != ["mosfet_a", "mosfet_b"]:
        assert result.withheld.filtered_evidence > 0


def test_filters_that_leave_nothing_answer_not_found_not_insufficient(repo, db_path):
    mosfet_library(QueryRows(repo))
    result = _concept(repo, db_path, pages=(99,))
    assert result.status is AnswerStatus.NOT_FOUND
    assert "filters" in result.message


def test_lifecycle_default_excludes_archived_and_a_lifecycle_filter_replaces_it(repo, db_path):
    rows = QueryRows(repo)
    source = rows.source(rows.document("book"))
    triode = rows.concept("Triode")
    rows.concept_occurrence(triode, source, page=1)
    current = rows.knowledge(KnowledgeType.DEFINITION, "A triode has three electrodes.")
    archived = rows.knowledge(KnowledgeType.DEFINITION, "A triode is obsolete.",
                              status=LifecycleStatus.ARCHIVED)
    for knowledge in (current, archived):
        rows.knowledge_occurrence(knowledge, source, page=1)
        edge = rows.edge(RelationType.DEFINED_BY, from_concept_id=triode.id, to_knowledge_id=knowledge.id)
        rows.relationship_occurrence(edge, source, page=1)
    rows.commit()
    engine = _engine(repo, db_path)

    default = engine.run(QueryRequest.concept("Triode")).concept
    assert [i.knowledge.id for i in _group(default, "Definitions").items] == [current.id]
    only = engine.run(QueryRequest.concept(
        "Triode", filters=QueryFilters(lifecycle_statuses=(LifecycleStatus.ARCHIVED,))))
    assert [i.knowledge.id for i in _group(only.concept, "Definitions").items] == [archived.id]


def test_items_within_a_group_are_ordered_by_document_then_position(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    relationships = _group(_concept(repo, db_path).concept, "Relationships")
    documents = [
        min(r.document_id for r in item.edge.evidence + tuple(b.occurrence for b in item.edge.bases))
        for item in relationships.items if isinstance(item, EdgeItem)
    ]
    assert documents == [n.book_a.id, n.book_a.id, n.book_a.id, n.book_b.id]
