"""Phase 3 knowledge representation: the rules, not just the happy path.

Each test names the decision or specification section it defends, so a future change
that breaks one can tell whether it is breaking a requirement or a preference.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from app.core.errors import RudraError
from app.models import (
    ALL_ENTITIES,
    ENTITIES,
    PHASE_3_ENTITIES,
    Concept,
    ConceptAlias,
    ConceptView,
    KnowledgeType,
    LifecycleStatus,
    RelationType,
    Relationship,
    RelationshipOrigin,
)
from app.models.base import utc_now
from app.storage import queries


# ------------------------------------------------------- registries (D-29, D-32)


def test_section_184_registry_is_frozen_at_twenty_two():
    """Phase 2's guarantee must stay checkable by counting (decision D-29)."""
    from app.models import PHASE_4_ENTITIES, PHASE_5_ENTITIES, PHASE_6_ENTITIES, PHASE_8_ENTITIES

    assert len(ALL_ENTITIES) == 22
    assert len(PHASE_3_ENTITIES) == 3
    assert len(PHASE_4_ENTITIES) == 1
    assert len(PHASE_5_ENTITIES) == 2
    assert len(PHASE_6_ENTITIES) == 1
    assert len(PHASE_8_ENTITIES) == 2
    assert len(ENTITIES) == 31
    registries = (
        ALL_ENTITIES, PHASE_3_ENTITIES, PHASE_4_ENTITIES, PHASE_5_ENTITIES,
        PHASE_6_ENTITIES, PHASE_8_ENTITIES,
    )
    assert set().union(*map(set, registries)) == set(ENTITIES)
    # The phase registries never overlap: each entity belongs to exactly one.
    for i, left in enumerate(registries):
        for right in registries[i + 1:]:
            assert not set(left) & set(right)


def test_every_entity_declares_a_kind_and_table():
    for entity in ENTITIES:
        assert hasattr(entity, "KIND"), entity.__name__
        assert entity.TABLE and entity.TABLE.islower(), entity.__name__


def test_concept_view_is_not_an_entity():
    """Decision D-32: a read model has no KIND and no TABLE, and is not persisted."""
    assert ConceptView not in ENTITIES
    assert not hasattr(ConceptView, "KIND")
    assert not hasattr(ConceptView, "TABLE")


# ------------------------------------------------ endpoint integrity (D-22)


def _concepts(service, *names):
    return [service.create_concept(name) for name in names]


def test_an_edge_to_a_missing_endpoint_is_refused_by_the_database(graph):
    """Decision D-22: endpoints are real foreign keys, not format checks."""
    service, repo = graph["service"], graph["repo"]
    concept = service.create_concept("MOSFET")
    with pytest.raises(RudraError):
        service.attach_relationship(
            from_concept_id=concept.id,
            to_knowledge_id="K-00009999",
            relation_type=RelationType.DEFINED_BY,
            origin=RelationshipOrigin.INFERRED,
        )
    repo.connection.rollback()


def test_foreign_key_check_covers_relationship_endpoints(graph):
    """The pragma is only meaningful because endpoints are declarative FKs.

    Under the trigger-only design that was considered and rejected, this pragma
    would have been permanently blind to a dangling edge.
    """
    service = graph["service"]
    names = [row[2] for row in service.repository.connection.execute(
        "PRAGMA foreign_key_list(relationship)"
    )]
    assert sorted(names) == ["concept", "concept", "knowledge_object", "knowledge_object"]
    assert service.repository.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_deleting_a_concept_an_edge_points_at_is_blocked(graph):
    """ON DELETE RESTRICT, declarative, on every endpoint column."""
    service, repo = graph["service"], graph["repo"]
    parent, child = _concepts(service, "Transistor", "MOSFET")
    service.attach_relationship(
        from_concept_id=parent.id,
        to_concept_id=child.id,
        relation_type=RelationType.PARENT_OF,
        origin=RelationshipOrigin.INFERRED,
    )
    repo.connection.commit()
    with pytest.raises(sqlite3.IntegrityError):
        repo.connection.execute("DELETE FROM concept WHERE id = ?", (child.id,))
    repo.connection.rollback()


@pytest.mark.parametrize("side", ["from", "to"])
def test_exactly_one_endpoint_per_side_is_enforced_by_the_database(graph, side):
    """The trigger fires even for a writer that goes around the model."""
    service, repo = graph["service"], graph["repo"]
    concept = service.create_concept("MOSFET")
    now = utc_now()
    columns = {
        "from_concept_id": concept.id,
        "from_knowledge_id": None,
        "to_concept_id": None,
        "to_knowledge_id": None,
    }
    if side == "from":
        # zero on the `to` side
        pass
    else:
        columns["to_concept_id"] = concept.id
        columns["from_concept_id"] = None
    with pytest.raises(sqlite3.IntegrityError, match="exactly one"):
        repo.connection.execute(
            "INSERT INTO relationship(id, created_at, updated_at, relation_type, "
            "origin, lifecycle_status, from_concept_id, from_knowledge_id, "
            "to_concept_id, to_knowledge_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "REL-00009001", now, now, "RELATED_TO", "INFERRED", "ACTIVE",
                columns["from_concept_id"], columns["from_knowledge_id"],
                columns["to_concept_id"], columns["to_knowledge_id"],
            ),
        )
    repo.connection.rollback()


def test_a_self_edge_is_refused_by_the_database(graph):
    service, repo = graph["service"], graph["repo"]
    concept = service.create_concept("MOSFET")
    now = utc_now()
    with pytest.raises(sqlite3.IntegrityError, match="itself"):
        repo.connection.execute(
            "INSERT INTO relationship(id, created_at, updated_at, relation_type, "
            "origin, lifecycle_status, from_concept_id, to_concept_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("REL-00009002", now, now, "RELATED_TO", "INFERRED", "ACTIVE",
             concept.id, concept.id),
        )
    repo.connection.rollback()


# ------------------------------------------------------- edge identity (D-24)


ENDPOINT_COMBINATIONS = ["cc", "ck", "kc", "kk"]


def _edge_kwargs(combination, concept_a, concept_b, knowledge_a, knowledge_b):
    first = {"from_concept_id": concept_a.id} if combination[0] == "c" else {
        "from_knowledge_id": knowledge_a.id
    }
    second = {"to_concept_id": concept_b.id} if combination[1] == "c" else {
        "to_knowledge_id": knowledge_b.id
    }
    return {**first, **second}


@pytest.fixture
def four_endpoints(graph):
    service = graph["service"]
    concept_a = service.create_concept("Transistor")
    concept_b = service.create_concept("MOSFET")
    knowledge_a, _ = service.attach_definition(
        concept_a.id, "A transistor switches or amplifies.", label="transistor def",
        origin=RelationshipOrigin.INFERRED,
    )
    knowledge_b, _ = service.attach_definition(
        concept_b.id, "A MOSFET is voltage-controlled.", label="mosfet def",
        origin=RelationshipOrigin.INFERRED,
    )
    return service, concept_a, concept_b, knowledge_a, knowledge_b


@pytest.mark.parametrize("combination", ENDPOINT_COMBINATIONS)
def test_a_duplicate_active_edge_is_refused_for_every_endpoint_kind(
    four_endpoints, combination
):
    """Decision D-24, and the reason the index uses COALESCE.

    SQLite treats NULLs as DISTINCT in unique indexes, so a plain index over the
    four nullable endpoint columns would enforce nothing - measured. Parameterising
    over every combination is what makes a forgotten COALESCE term fail loudly when
    a later phase adds an endpoint kind.
    """
    service, ca, cb, ka, kb = four_endpoints
    kwargs = _edge_kwargs(combination, ca, cb, ka, kb)
    service.attach_relationship(
        relation_type=RelationType.RELATED_TO,
        origin=RelationshipOrigin.INFERRED,
        **kwargs,
    )
    with pytest.raises(RudraError):
        service.attach_relationship(
            relation_type=RelationType.RELATED_TO,
            origin=RelationshipOrigin.INFERRED,
            **kwargs,
        )
    service.repository.connection.rollback()


def test_origin_is_excluded_from_edge_identity(four_endpoints, graph):
    """The same relation inferred and stated is ONE edge (Part 3 sections 69-70).

    The second attempt supplies real evidence, so it gets past the decision D-28
    check and is refused by the uniqueness index itself - which is the thing under
    test. Asserting on the message keeps the two failure modes apart.
    """
    service, ca, cb, _, _ = four_endpoints
    service.attach_relationship(
        from_concept_id=ca.id, to_concept_id=cb.id,
        relation_type=RelationType.RELATED_TO, origin=RelationshipOrigin.INFERRED,
    )
    with pytest.raises(RudraError) as caught:
        service.attach_relationship(
            from_concept_id=ca.id, to_concept_id=cb.id,
            relation_type=RelationType.RELATED_TO,
            origin=RelationshipOrigin.EXPLICIT,
            evidence=graph["evidence"]("Transistors relate to MOSFETs.", 400),
        )
    assert "same unique value" in caught.value.report.reason, caught.value.report.reason
    service.repository.connection.rollback()


def test_a_non_active_duplicate_is_allowed(four_endpoints):
    """Tombstones must remain possible (ARCHITECTURE section 6.4 rule 3)."""
    service, ca, cb, _, _ = four_endpoints
    repo = service.repository
    first = service.attach_relationship(
        from_concept_id=ca.id, to_concept_id=cb.id,
        relation_type=RelationType.RELATED_TO, origin=RelationshipOrigin.INFERRED,
    )
    repo.update(replace(first, lifecycle_status=LifecycleStatus.ARCHIVED))
    second = service.attach_relationship(
        from_concept_id=ca.id, to_concept_id=cb.id,
        relation_type=RelationType.RELATED_TO, origin=RelationshipOrigin.INFERRED,
    )
    repo.connection.commit()
    assert repo.count(Relationship) >= 2
    assert repo.get(Relationship, first.id).lifecycle_status is LifecycleStatus.ARCHIVED
    assert repo.get(Relationship, second.id).lifecycle_status is LifecycleStatus.ACTIVE


def test_a_reversed_edge_is_a_different_edge(four_endpoints):
    """Direction is part of identity: A->B and B->A are two edges, not a duplicate."""
    service, ca, cb, _, _ = four_endpoints
    forward = service.attach_relationship(
        from_concept_id=ca.id, to_concept_id=cb.id,
        relation_type=RelationType.RELATED_TO, origin=RelationshipOrigin.INFERRED,
    )
    backward = service.attach_relationship(
        from_concept_id=cb.id, to_concept_id=ca.id,
        relation_type=RelationType.RELATED_TO, origin=RelationshipOrigin.INFERRED,
    )
    repo = service.repository
    repo.connection.commit()
    assert forward.id != backward.id
    assert repo.get(Relationship, forward.id) is not None
    assert repo.get(Relationship, backward.id) is not None
    assert (forward.from_id, forward.to_id) == (backward.to_id, backward.from_id)


# ------------------------------------------- EXPLICIT requires evidence (D-28)


def test_an_explicit_relationship_without_evidence_is_refused(graph):
    """Decision D-28: the requirement lives in the signature, so it fails early."""
    service = graph["service"]
    a, b = _concepts(service, "Transistor", "MOSFET")
    with pytest.raises(RudraError) as caught:
        service.attach_relationship(
            from_concept_id=a.id, to_concept_id=b.id,
            relation_type=RelationType.PARENT_OF,
            origin=RelationshipOrigin.EXPLICIT,
        )
    report = caught.value.report
    assert "evidence" in report.missing
    assert report.data_changed is False


def test_an_inferred_relationship_needs_no_evidence(graph):
    service = graph["service"]
    a, b = _concepts(service, "Transistor", "MOSFET")
    edge = service.attach_relationship(
        from_concept_id=a.id, to_concept_id=b.id,
        relation_type=RelationType.PARENT_OF, origin=RelationshipOrigin.INFERRED,
    )
    assert edge.origin is RelationshipOrigin.INFERRED
    assert queries.occurrences_for_relationship(service.repository.connection, edge.id) == ()


def test_graph_integrity_detects_an_edge_written_behind_the_service(graph):
    """The schema cannot hold this rule, so detection is the backstop (ADR 0011)."""
    service, repo = graph["service"], graph["repo"]
    a, b = _concepts(service, "Transistor", "MOSFET")
    now = utc_now()
    repo.connection.execute(
        "INSERT INTO relationship(id, created_at, updated_at, relation_type, origin, "
        "lifecycle_status, from_concept_id, to_concept_id) VALUES (?,?,?,?,?,?,?,?)",
        ("REL-00009100", now, now, "PARENT_OF", "EXPLICIT", "ACTIVE", a.id, b.id),
    )
    report = queries.graph_integrity(repo.connection)
    assert report.explicit_without_evidence == ("REL-00009100",)
    assert report.is_clean is False
    repo.connection.rollback()


def test_a_clean_graph_reports_clean(graph):
    service, repo = graph["service"], graph["repo"]
    a, b = _concepts(service, "Transistor", "MOSFET")
    service.attach_relationship(
        from_concept_id=a.id, to_concept_id=b.id,
        relation_type=RelationType.PARENT_OF, origin=RelationshipOrigin.EXPLICIT,
        evidence=graph["evidence"]("Transistors include MOSFETs.", 400),
    )
    repo.connection.commit()
    report = queries.graph_integrity(repo.connection)
    assert report.is_clean, report


# ----------------------------------------------------- origin transitions (D-24)


def test_inferred_can_be_promoted_to_explicit(graph):
    """A source found later makes the edge genuinely explicit (Part 2 section 40)."""
    service, repo = graph["service"], graph["repo"]
    a, b = _concepts(service, "Transistor", "MOSFET")
    edge = service.attach_relationship(
        from_concept_id=a.id, to_concept_id=b.id,
        relation_type=RelationType.PARENT_OF, origin=RelationshipOrigin.INFERRED,
    )
    promoted = service.promote_to_explicit(
        edge, graph["evidence"]("Transistors include MOSFETs.", 400)
    )
    repo.connection.commit()
    assert promoted.origin is RelationshipOrigin.EXPLICIT
    assert repo.get(Relationship, edge.id).origin is RelationshipOrigin.EXPLICIT
    assert len(queries.occurrences_for_relationship(repo.connection, edge.id)) == 1
    assert queries.graph_integrity(repo.connection).is_clean


def test_explicit_can_never_be_downgraded_to_inferred(graph):
    """The reverse transition would misrepresent what a source said."""
    service, repo = graph["service"], graph["repo"]
    a, b = _concepts(service, "Transistor", "MOSFET")
    edge = service.attach_relationship(
        from_concept_id=a.id, to_concept_id=b.id,
        relation_type=RelationType.PARENT_OF, origin=RelationshipOrigin.EXPLICIT,
        evidence=graph["evidence"]("Transistors include MOSFETs.", 400),
    )
    repo.connection.commit()
    with pytest.raises(RudraError):
        repo.update(replace(edge, origin=RelationshipOrigin.INFERRED))
    repo.connection.rollback()
    assert repo.get(Relationship, edge.id).origin is RelationshipOrigin.EXPLICIT


# ------------------------------------------------- concept resolution (section 44)


def test_two_aliases_resolve_to_one_concept(graph):
    """Part 2 section 44's worked example, with its own vocabulary."""
    service = graph["service"]
    mosfet = service.create_concept("MOSFET")
    service.add_alias(mosfet.id, "MOS transistor")
    service.add_alias(mosfet.id, "Metal Oxide Semiconductor Field Effect Transistor")
    for name in ("MOSFET", "mosfet", "MOS TRANSISTOR", "  MOS transistor  "):
        resolved = service.resolve(name)
        assert [c.id for c in resolved] == [mosfet.id], name


def test_gain_stays_three_concepts_across_three_contexts(graph):
    """Part 6 section 46, verbatim: do not merge them because they share a word."""
    service = graph["service"]
    contexts = ["Voltage amplifier", "Current amplifier", "Control system"]
    made = [service.create_concept("Gain", context=c) for c in contexts]
    resolved = service.resolve("gain")
    assert len(resolved) == 3
    assert {c.id for c in resolved} == {c.id for c in made}
    assert sorted(c.context for c in resolved) == sorted(contexts)


def test_the_same_alias_may_belong_to_several_concepts(graph):
    """`normalized_alias` is unique within a concept, never globally."""
    service, repo = graph["service"], graph["repo"]
    first = service.create_concept("Gain", context="Voltage amplifier")
    second = service.create_concept("Gain", context="Control system")
    repo.connection.commit()
    assert len(service.resolve("Gain")) == 2
    assert first.id != second.id


def test_one_concept_cannot_hold_the_same_alias_twice(graph):
    service, repo = graph["service"], graph["repo"]
    concept = service.create_concept("MOSFET")
    with pytest.raises(RudraError):
        service.add_alias(concept.id, "mosfet")  # normalises to the canonical name
    repo.connection.rollback()


def test_an_alias_whose_normalised_form_is_wrong_is_refused(graph):
    """The database cannot check this; the model is the only place that can."""
    service, repo = graph["service"], graph["repo"]
    concept = service.create_concept("MOSFET")
    now = utc_now()
    bad = ConceptAlias(
        id=repo.new_id(ConceptAlias),
        created_at=now,
        updated_at=now,
        concept_id=concept.id,
        alias="MOS transistor",
        normalized_alias="something else entirely",
        lifecycle_status=LifecycleStatus.ACTIVE,
    )
    with pytest.raises(RudraError):
        repo.add(bad)


# ---------------------------------------------------- derived relations (D-23)


def test_child_of_is_derived_not_stored(graph):
    """Only PARENT_OF is a row; CHILD_OF is a query (decision D-23)."""
    service, repo = graph["service"], graph["repo"]
    parent, child = _concepts(service, "Transistor", "MOSFET")
    service.attach_relationship(
        from_concept_id=parent.id, to_concept_id=child.id,
        relation_type=RelationType.PARENT_OF, origin=RelationshipOrigin.INFERRED,
    )
    repo.connection.commit()
    stored = [r.relation_type for r in queries.relationships_for_concept(repo.connection, child.id)]
    assert RelationType.PARENT_OF in stored
    assert RelationType.CHILD_OF not in stored
    assert queries.children_of_concept(repo.connection, parent.id) == (child.id,)


def test_a_concept_may_have_several_parents(graph):
    """Part 2 section 42: do not force knowledge into a single tree."""
    service, repo = graph["service"], graph["repo"]
    child = service.create_concept("MOSFET")
    parents = _concepts(service, "Transistor", "Semiconductor device")
    for parent in parents:
        service.attach_relationship(
            from_concept_id=parent.id, to_concept_id=child.id,
            relation_type=RelationType.PARENT_OF, origin=RelationshipOrigin.INFERRED,
        )
    repo.connection.commit()
    assert set(queries.ancestors_of_concept(repo.connection, child.id)) == {
        p.id for p in parents
    }


def test_ancestor_closure_is_transitive(graph):
    service, repo = graph["service"], graph["repo"]
    a, b, c = _concepts(service, "Electronics", "Analog", "MOSFET")
    for parent, kid in ((a, b), (b, c)):
        service.attach_relationship(
            from_concept_id=parent.id, to_concept_id=kid.id,
            relation_type=RelationType.PARENT_OF, origin=RelationshipOrigin.INFERRED,
        )
    repo.connection.commit()
    assert set(queries.ancestors_of_concept(repo.connection, c.id)) == {a.id, b.id}
    assert set(queries.descendants_of_concept(repo.connection, a.id)) == {b.id, c.id}


def test_a_cyclic_hierarchy_terminates(graph):
    """Part 2 section 42 calls knowledge a graph; a cycle must be survived."""
    service, repo = graph["service"], graph["repo"]
    a, b, c = _concepts(service, "A", "B", "C")
    for parent, kid in ((a, b), (b, c), (c, a)):
        service.attach_relationship(
            from_concept_id=parent.id, to_concept_id=kid.id,
            relation_type=RelationType.PARENT_OF, origin=RelationshipOrigin.INFERRED,
        )
    repo.connection.commit()
    assert set(queries.ancestors_of_concept(repo.connection, a.id)) == {a.id, b.id, c.id}
    assert set(queries.descendants_of_concept(repo.connection, a.id)) == {a.id, b.id, c.id}


def test_prerequisite_direction_is_asserted(graph):
    """`from` is the prerequisite; `to` is what needs it."""
    service, repo = graph["service"], graph["repo"]
    mosfet, physics = _concepts(service, "MOSFET", "Semiconductor physics")
    edge = service.attach_prerequisite(
        mosfet.id, physics.id, origin=RelationshipOrigin.INFERRED
    )
    repo.connection.commit()
    assert edge.from_id == physics.id
    assert edge.to_id == mosfet.id
    found = queries.prerequisites_of_concept(repo.connection, mosfet.id)
    assert [r.from_id for r in found] == [physics.id]
    assert queries.prerequisites_of_concept(repo.connection, physics.id) == ()


# --------------------------------------------------------- I-KO-NAME (A-4)


def test_retrieval_does_not_depend_on_a_knowledge_objects_name(graph):
    """Invariant I-KO-NAME, proved rather than asserted (decision A-4, ADR 0012).

    `canonical_name` labels the knowledge object. If any retrieval path resolved a
    concept through that label, renaming it would change the result - which is the
    coupling D-18 separated, returning as a string join.
    """
    service, repo = graph["service"], graph["repo"]
    concept = service.create_concept("MOSFET")
    knowledge, _ = service.attach_definition(
        concept.id,
        "A MOSFET is a voltage-controlled device.",
        label="MOSFET",  # deliberately identical to the concept name
        evidence=graph["evidence"]("A MOSFET is a voltage-controlled device.", 412),
    )
    repo.connection.commit()

    before = service.retrieve(concept.id)
    assert [d.knowledge.id for d in before.definitions] == [knowledge.id]

    repo.update(replace(knowledge, canonical_name="zzz-unrelated-label"))
    repo.connection.commit()

    after = service.retrieve(concept.id)
    assert [d.knowledge.id for d in after.definitions] == [knowledge.id]
    assert after.definitions[0].knowledge.canonical_name == "zzz-unrelated-label"


def test_phase_3_creates_no_concept_typed_knowledge_objects(graph):
    """Decision D-34: a CONCEPT knowledge object is never how a concept is stored."""
    service, repo = graph["service"], graph["repo"]
    concept = service.create_concept("MOSFET")
    service.attach_definition(
        concept.id, "A MOSFET is voltage-controlled.", label="def",
        evidence=graph["evidence"]("A MOSFET is voltage-controlled.", 412),
    )
    service.attach_equation(
        concept.id, "I_D = k(V_GS-V_th)^2", label="drain current",
        relation_type=RelationType.USES,
        evidence=graph["evidence"]("I_D = k(V_GS-V_th)^2", 415),
    )
    repo.connection.commit()
    types = [
        row[0]
        for row in repo.connection.execute("SELECT knowledge_type FROM knowledge_object")
    ]
    assert KnowledgeType.CONCEPT.value not in types
    assert set(types) == {"DEFINITION", "EQUATION"}


# ------------------------------------------------------------ retrieval shape


def test_retrieve_returns_none_for_a_missing_concept(graph):
    assert graph["service"].retrieve("CPT-00009999") is None


def test_a_concept_without_evidence_reports_absence(graph):
    """Part 5 section 205: say so; never invent a citation."""
    service, repo = graph["service"], graph["repo"]
    concept = service.create_concept("Undocumented idea")
    repo.connection.commit()
    view = service.retrieve(concept.id)
    assert view.occurrences == ()
    assert view.has_provenance is False


def test_equations_keep_no_canonical_form_in_phase_3(graph):
    """Parsing is the Phase 11 calculation engine; Phase 3 must not pretend."""
    service, repo = graph["service"], graph["repo"]
    concept = service.create_concept("MOSFET")
    equation, _, _ = service.attach_equation(
        concept.id, "I_D = k(V_GS-V_th)^2", label="drain current",
        relation_type=RelationType.USES,
        evidence=graph["evidence"]("I_D = k(V_GS-V_th)^2", 415),
    )
    repo.connection.commit()
    assert equation.canonical_form is None
    assert service.retrieve(concept.id).equations[0].canonical_form is None


def test_attach_equation_requires_an_explicit_relation_type(graph):
    """The specification names no concept-to-equation relation, so RUDRA invents none.

    Part 2 section 39 introduces its vocabulary with "Examples:", and `USES`,
    `APPLIES_TO` and `DERIVED_FROM` each appear exactly once in the whole
    specification - in that list, never applied to anything. Sections 21, 65 and 249
    stay untyped ("relate to", "associated with"). A default here would put an
    invented choice into storage for every later phase to inherit.
    """
    service = graph["service"]
    concept = service.create_concept("MOSFET")
    with pytest.raises(TypeError, match="relation_type"):
        service.attach_equation(
            concept.id, "I = V/R", label="ohm",
            evidence=graph["evidence"]("I = V/R", 1),
        )
