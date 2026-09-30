"""Phase 9 steps 4-6: concept mode, traversal and deterministic widening.

ADR 0036 P9-11 ... P9-17 (identity, groups, attribution, traversal, conflicts,
deduplication state, equivalence records) and ADR 0035 P9-3 (D1) and P9-4 (D2), on
`mosfet_library` and small purpose-built stores. Nothing is inferred, merged or
chosen; every absence is stated.
"""

from __future__ import annotations

from app.models import (
    Authorization,
    ConceptEquivalenceBasis,
    ConceptEquivalenceStatus,
    KnowledgeType,
    LifecycleStatus,
    RelationshipOrigin,
    RelationType,
)
from app.query import AnswerStatus, QueryEngine, QueryRequest
from app.query.concepts import GROUP_ORDER
from app.query.engine import D1_SCOPE, EXACT_IDENTITY, NOT_WIDENED
from app.query.results import EdgeEnd, EdgeItem, KnowledgeItem
from app.query.traversal import EXPLICIT_LABEL, R1_LABEL
from app.query.widening import NOTHING_IN_SCOPE, NOTHING_STORED
from app.storage import queries
from tests.unit.query_rows import QueryRows, mosfet_library


def _run(repo, db_path, name: str, **options):
    return QueryEngine(repo.connection, database_path=db_path).run(QueryRequest.concept(name, **options))


def _group(section, name: str):
    return next(group for group in section.groups if group.name == name)


def _edge_ids(group) -> list[str]:
    return [item.edge.relationship.id for item in group.items if isinstance(item, EdgeItem)]


def _knowledge_ids(section) -> set[str]:
    return {
        item.knowledge.id
        for group in section.groups
        for item in group.items
        if isinstance(item, KnowledgeItem)
    }


# -------------------------------------------------------------- identity (P9-11)


def test_every_concept_answering_to_the_exact_name_is_resolved_and_none_is_chosen(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    result = _run(repo, db_path, "  mosfet ")

    assert result.status is AnswerStatus.FOUND
    assert [rc.concept.id for rc in result.concept.concepts] == [n.mosfet_a.id, n.mosfet_b.id]
    assert {rc.matched_alias.normalized_alias for rc in result.concept.concepts} == {"mosfet"}
    assert "none was chosen" in result.message
    assert result.notes[0] == EXACT_IDENTITY


def test_a_regular_plural_finds_its_singular_and_says_so_but_nothing_else_is_rewritten(repo, db_path):
    mosfet_library(QueryRows(repo))
    plural = _run(repo, db_path, "MOSFETs")  # "What properties of MOSFETs ...?" asks about the MOSFET
    assert plural.concept is not None and any("singular 'mosfet' was used" in note for note in plural.notes)
    for name in ("MOS-FET", "MOSFET transistor", "MOSFETss"):
        result = _run(repo, db_path, name)
        assert result.status is AnswerStatus.NOT_FOUND
        assert "nothing was guessed" in result.message and result.concept is None


# -------------------------------------------------------- groups (P9-13, D1)


def test_groups_are_fixed_and_follow_the_stored_direction(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    section = _run(repo, db_path, "MOSFET").concept

    assert tuple(group.name for group in section.groups) == GROUP_ORDER
    assert [item.knowledge.id for item in _group(section, "Definitions").items] == [n.definition.id]
    assert [item.knowledge.id for item in _group(section, "Properties").items] == [n.property.id, n.rival.id]
    assert _edge_ids(_group(section, "Prerequisites")) == [n.prereq.id]
    assert _edge_ids(_group(section, "Applications")) == [n.uses.id]
    # "Gate oxide USES MOSFET" is not an application of MOSFET: it is listed as a
    # relationship, with its real type and direction.
    relationships = _group(section, "Relationships")
    assert _edge_ids(relationships) == [n.used_in.id, n.parent.id, n.related.id, n.same_as.id]
    used_in = relationships.items[0]
    assert (used_in.links[0].concept_end, used_in.edge.relationship.relation_type) == (
        EdgeEnd.TO, RelationType.USES,
    )
    assert [c.id for c in used_in.neighbours] == [n.gate_oxide.id]
    # Neighbours are named, never expanded.
    assert [c.id for c in _group(section, "Applications").items[0].neighbours] == [n.amplifier.id]
    assert _group(section, "Dependencies").items == ()
    assert _group(section, "Dependencies").note.startswith("Nothing stored")


def test_an_object_reached_from_two_concepts_is_listed_once_with_both_links(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    (definition,) = _group(_run(repo, db_path, "MOSFET").concept, "Definitions").items

    assert [(link.concept_id, link.edge.relationship.id) for link in definition.links] == [
        (n.mosfet_a.id, n.defined_a.id), (n.mosfet_b.id, n.defined_b.id),
    ]
    assert [row.id for row in definition.evidence] == [n.def_a.id, n.def_b.id]


def test_no_mentions_group_and_no_distractor_knowledge(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    section = _run(repo, db_path, "MOSFET").concept

    listed = _knowledge_ids(section)
    assert listed == {n.definition.id, n.property.id, n.rival.id}
    # BJT's definition names MOSFET, the equation is about MOSFETs, and book C's
    # definition is unauthorised: none is attached to MOSFET.
    assert not listed & {n.bjt_definition.id, n.equation.id, n.hidden.id, n.mos_definition.id}
    assert "Mentions" not in {group.name for group in section.groups}


def test_d1_groups_state_that_no_concept_link_is_stored(repo, db_path):
    mosfet_library(QueryRows(repo))
    result = _run(repo, db_path, "MOSFET")

    for name, kind in (("Equations", "EQUATION"), ("Variables", "VARIABLE"), ("Examples", "EXAMPLE")):
        group = _group(result.concept, name)
        assert group.items == ()
        assert f"No stored edge links any {kind} object" in group.note
        assert "PARTIALLY IMPLEMENTED" in group.note
    assert D1_SCOPE in result.notes


def test_a_linked_equation_is_listed_with_its_edge_and_the_note_says_so(repo, db_path):
    rows = QueryRows(repo)
    book = rows.document("book")
    source = rows.source(book)
    law, other = rows.concept("Ohm's law"), rows.concept("Kirchhoff's law")
    for concept in (law, other):
        rows.concept_occurrence(concept, source, page=1)
    equation = rows.knowledge(KnowledgeType.EQUATION, "V = I R")
    rows.knowledge_occurrence(equation, source, page=1)
    edge = rows.edge(RelationType.RELATED_TO, from_concept_id=law.id, to_knowledge_id=equation.id)
    rows.relationship_occurrence(edge, source, page=1)
    rows.commit()

    linked = _group(_run(repo, db_path, "Ohm's law").concept, "Equations")
    assert [item.knowledge.id for item in linked.items] == [equation.id]
    assert linked.items[0].links[0].edge.relationship.id == edge.id
    assert linked.note.startswith("Only EQUATION objects with a stored edge")
    unlinked = _group(_run(repo, db_path, "Kirchhoff's law").concept, "Equations")
    assert unlinked.items == () and unlinked.note.startswith("No EQUATION object has a stored edge")


# ------------------------------------------------ conflicts and records (P9-15 ... P9-17)


def test_conflicts_show_both_claims_and_select_neither(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    conflicts = _run(repo, db_path, "MOSFET").concept.conflicts

    assert [item.conflict.id for item in conflicts] == [n.conflict.id, n.hidden_conflict.id]
    shown = conflicts[0]
    assert (shown.claim_a.knowledge, shown.claim_b.knowledge) == (n.property, n.rival)
    assert shown.claim_a.evidence and shown.claim_b.evidence
    assert shown.resolution == "not automatically selected"
    assert shown.conflict.cause.value == "UNDETERMINED"
    # The other claim of the second conflict is unauthorised: withheld, never shown.
    withheld = conflicts[1]
    assert withheld.claim_a.knowledge == n.definition
    assert (withheld.claim_b.knowledge_id, withheld.claim_b.knowledge, withheld.claim_b.evidence) == (
        n.hidden.id, None, (),
    )


def test_a_superseded_object_stays_under_its_own_concept_with_its_pointer(repo, db_path):
    rows = QueryRows(repo)
    first, second = rows.document("first"), rows.document("second")
    one, two = rows.source(first), rows.source(second)
    diode_1, diode_2 = rows.concept("Diode"), rows.concept("Diode")
    canonical = rows.knowledge(KnowledgeType.DEFINITION, "A diode conducts in one direction.")
    duplicate = rows.knowledge(KnowledgeType.DEFINITION, "A diode conducts in one direction.")
    for concept, knowledge, source in ((diode_1, canonical, one), (diode_2, duplicate, two)):
        rows.concept_occurrence(concept, source, page=1)
        rows.knowledge_occurrence(knowledge, source, page=1)
        edge = rows.edge(RelationType.DEFINED_BY, from_concept_id=concept.id, to_knowledge_id=knowledge.id)
        rows.relationship_occurrence(edge, source, page=1)
    pointer = rows.supersede(canonical, duplicate)
    rows.commit()

    items = _group(_run(repo, db_path, "Diode").concept, "Definitions").items
    assert [item.knowledge.id for item in items] == [canonical.id, duplicate.id]
    kept, superseded = items
    assert superseded.knowledge.lifecycle_status is LifecycleStatus.SUPERSEDED
    assert superseded.superseded_by == canonical.id
    assert [link.concept_id for link in superseded.links] == [diode_2.id]  # its own concept
    assert kept.superseded_into == (duplicate.id,)
    assert (kept.source_occurrences, kept.number_of_sources) == (2, 2)
    assert pointer in kept.assessments and pointer in superseded.assessments

    without = _run(repo, db_path, "Diode", include_superseded=False)
    assert [i.knowledge.id for i in _group(without.concept, "Definitions").items] == [canonical.id]
    assert without.withheld.items_filtered_out == 1


def test_stored_equivalence_records_naming_a_concept_are_shown_as_stored(repo, db_path):
    rows = QueryRows(repo)
    book = rows.document("book")
    source = rows.source(book)
    a, b = rows.concept("Gain"), rows.concept("Gain")
    for concept in (a, b):
        rows.concept_occurrence(concept, source, page=1)
    denied = rows.equivalence(a, b, status=ConceptEquivalenceStatus.NOT_EQUIVALENT)
    rows.commit()

    result = _run(repo, db_path, "Gain")
    assert [rc.equivalence_records for rc in result.concept.concepts] == [(denied,), (denied,)]
    # A NOT_EQUIVALENT record is shown, and widens nothing (P9-4).
    assert result.possible_equivalents == () and result.equivalence_note == NOTHING_STORED


# ------------------------------------------------------------ traversal (P9-14)


def test_rule_r1_edges_are_labelled_inferred_with_their_basis(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    relationships = _group(_run(repo, db_path, "MOSFET").concept, "Relationships")
    related = next(i for i in relationships.items if i.edge.relationship.id == n.related.id)

    assert related.edge.origin is RelationshipOrigin.INFERRED and related.edge.label == R1_LABEL
    (basis,) = related.edge.bases
    assert basis.inference == n.basis
    assert basis.occurrence.id == n.bjt_def_a.id
    assert basis.mentioning_concept_ids == (n.bjt.id,)
    assert related.edge.evidence == ()  # a basis is not a source statement
    assert [c.id for c in related.neighbours] == [n.bjt.id]
    uses = _group(_run(repo, db_path, "MOSFET").concept, "Applications").items[0]
    assert uses.edge.label == EXPLICIT_LABEL and uses.edge.evidence


def test_hierarchy_closures_follow_stored_parent_edges(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    mosfet = _run(repo, db_path, "MOSFET").concept.concepts[0]
    assert [c.id for c in mosfet.ancestors] == [n.fet.id, n.semiconductor.id]
    assert mosfet.descendants == ()
    assert set(queries.ancestors_of_concept(repo.connection, n.mosfet_a.id)) == {n.fet.id, n.semiconductor.id}

    top = _run(repo, db_path, "Semiconductor device").concept.concepts[0]
    assert [c.id for c in top.descendants] == [n.mosfet_a.id, n.fet.id]  # by counter
    assert set(queries.descendants_of_concept(repo.connection, n.semiconductor.id)) == {
        n.fet.id, n.mosfet_a.id,
    }


def test_closures_terminate_on_a_cycle(repo, db_path):
    rows = QueryRows(repo)
    source = rows.source(rows.document("book"))
    a, b = rows.concept("Alpha"), rows.concept("Beta")
    for concept in (a, b):
        rows.concept_occurrence(concept, source, page=1)
    for parent, child in ((a, b), (b, a)):
        edge = rows.edge(RelationType.PARENT_OF, from_concept_id=parent.id, to_concept_id=child.id)
        rows.relationship_occurrence(edge, source, page=1)
    rows.commit()

    alpha = _run(repo, db_path, "Alpha").concept.concepts[0]
    assert [c.id for c in alpha.ancestors] == [b.id] and [c.id for c in alpha.descendants] == [b.id]


def test_an_edge_stated_only_by_an_unauthorised_source_is_not_traversed(repo, db_path):
    rows = QueryRows(repo)
    book = rows.document("book")
    good, bad = rows.source(book), rows.source(book, authorization=Authorization.NOT_AUTHORIZED)
    child, parent = rows.concept("Child"), rows.concept("Parent")
    for concept in (child, parent):
        rows.concept_occurrence(concept, good, page=1)
    edge = rows.edge(RelationType.PARENT_OF, from_concept_id=parent.id, to_concept_id=child.id)
    rows.relationship_occurrence(edge, bad, page=1)
    rows.commit()

    result = _run(repo, db_path, "Child")
    assert result.concept.concepts[0].ancestors == ()
    assert _group(result.concept, "Relationships").items == ()
    assert result.withheld.items_without_authorized_evidence == 1


# --------------------------------------------------------- widening (D2, P9-4)


def test_a_stated_equivalent_is_a_separate_possible_section(repo, db_path):
    n = mosfet_library(QueryRows(repo))
    result = _run(repo, db_path, "MOSFET")

    (possible,) = result.possible_equivalents
    assert possible.concept == n.mos and possible.of_concept_ids == (n.mosfet_b.id,)
    assert "POSSIBLE" in possible.label and possible.already_resolved is False
    assert [edge.relationship.id for edge in possible.stated_edges] == [n.same_as.id]
    assert possible.stated_edges[0].evidence  # the statement's own evidence
    assert [i.knowledge.id for i in _group(possible.section, "Definitions").items] == [n.mos_definition.id]
    # Never merged into the target's groups.
    assert n.mos_definition.id not in _knowledge_ids(result.concept)
    assert result.equivalence_note is None and result.trace.widened == (n.mos.id,)


def test_widening_is_one_step_and_its_sources_are_not_the_targets(repo, db_path):
    rows = QueryRows(repo)
    first, second, third = (rows.document(name) for name in ("first", "second", "third"))
    one, two, three = rows.source(first), rows.source(second), rows.source(third)
    x, y, z = rows.concept("Xenon lamp"), rows.concept("Yttrium lamp"), rows.concept("Zinc lamp")
    for concept, source in ((x, one), (y, two), (z, three)):
        rows.concept_occurrence(concept, source, page=1)
    for left, right, source in ((x, y, one), (y, z, two)):
        edge = rows.edge(RelationType.EQUIVALENT_TO, from_concept_id=left.id, to_concept_id=right.id)
        rows.relationship_occurrence(edge, source, page=1)
    rows.commit()

    result = _run(repo, db_path, "Xenon lamp")
    assert [p.concept.id for p in result.possible_equivalents] == [y.id]  # never Z
    widened = result.possible_equivalents[0].section
    # Y's own edge to Z is listed in Y's section, Z named and not expanded.
    assert [c.id for c in _group(widened, "Relationships").items[1].neighbours] == [z.id]
    assert [s.id for s in result.concept.provenance.sources] == [one.id]
    assert two.id in {s.id for s in widened.provenance.sources}


def test_a_shared_name_record_between_resolved_concepts_adds_only_its_basis(repo, db_path):
    rows = QueryRows(repo)
    source = rows.source(rows.document("book"))
    a, b = rows.concept("Relay"), rows.concept("Relay")
    for concept in (a, b):
        rows.concept_occurrence(concept, source, page=1)
    record = rows.equivalence(a, b, basis=ConceptEquivalenceBasis.SHARED_NAME_SAME_DOCUMENT)
    rows.commit()

    (possible,) = _run(repo, db_path, "Relay").possible_equivalents
    assert (possible.concept, possible.of_concept_ids, possible.records) == (b, (a.id,), (record,))
    assert possible.already_resolved is True and possible.section is None


def test_absence_of_equivalence_data_is_stated(repo, db_path):
    rows = QueryRows(repo)
    book = rows.document("book")
    good, bad = rows.source(book), rows.source(book, authorization=Authorization.NOT_AUTHORIZED)
    lone, told, other = rows.concept("Solenoid"), rows.concept("Inductor"), rows.concept("Coil")
    for concept in (lone, told, other):
        rows.concept_occurrence(concept, good, page=1)
    edge = rows.edge(RelationType.EQUIVALENT_TO, from_concept_id=told.id, to_concept_id=other.id)
    rows.relationship_occurrence(edge, bad, page=1)  # stated only by an unauthorised source
    rows.commit()

    assert _run(repo, db_path, "Solenoid").equivalence_note == NOTHING_STORED
    assert _run(repo, db_path, "Solenoid", widen=False).equivalence_note == NOT_WIDENED
    told_result = _run(repo, db_path, "Inductor")
    assert told_result.possible_equivalents == ()
    assert told_result.equivalence_note == NOTHING_IN_SCOPE
