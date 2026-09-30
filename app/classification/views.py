"""Views V1 and V2 over one extraction run (decision P6-9b, ADR 0026).

**Neither view stores anything and neither infers anything.** Groups and trees are
views (P6-9): each is rebuilt from stored edges every time it is asked for, and no
group or tree row exists anywhere. Part 2 section 42's *"TREE = navigation, GRAPH =
knowledge relationships"* is the basis.

**V1 - hierarchy navigation.** Stored `ACTIVE` `PARENT_OF`, `COMPOSED_OF`,
`PART_OF` and `INSTANCE_OF` edges among the run's concepts, nested as a tree. Every
nesting shows its **real** relation type and origin: a composition edge is never
relabelled `PARENT_OF` - storing or displaying one stated fact under a second name
is what ADR 0009 D-23 rejects. A concept with several parents appears under each.
A cycle is reported, and traversal still terminates.

Which end is the parent follows how each type is stored (Part 2 section 40, and the
Phase 5 detectors, which store "subject phrase object" as `from -> to`):

    PARENT_OF    from = parent, to = child            (D-23)
    COMPOSED_OF  from = whole,  to = part             "X consists of Y"
    PART_OF      from = part,   to = whole            "X is part of Y"
    INSTANCE_OF  from = instance, to = kind           "X is a type of Y"

**V2 - organisation view.** Stored `ACTIVE` `PREREQUISITE_OF`, `DEPENDS_ON`,
`REQUIRES`, `APPLIES_TO`, `USES` and `RELATED_TO` edges among the run's concepts,
each labelled `EXPLICIT` with its occurrences or `INFERRED` with its bases. This is
where Part 5 section 193's explicit-versus-inferred distinction becomes visible.
V2 shows what is stored; Phase 6 infers no prerequisite, dependency or application
(P6-7), so on a run whose source states none, those groups are empty - the correct
answer, not a gap to fill.

Neither view reads `document_structure` (P6-5) or any page text (P6-17).
"""

from dataclasses import dataclass

from app.models.entities import (
    Concept,
    Relationship,
    RelationshipInference,
    RelationshipOccurrence,
    SourceOccurrence,
)
from app.models.enums import RelationshipOrigin, RelationType
from app.storage import queries
from app.storage.repository import Repository

#: V1's relation types and, for each, whether the parent is the edge's `from` end.
HIERARCHY_TYPES: tuple[tuple[RelationType, bool], ...] = (
    (RelationType.PARENT_OF, True),
    (RelationType.COMPOSED_OF, True),
    (RelationType.PART_OF, False),
    (RelationType.INSTANCE_OF, False),
)

#: V2's groups, in the order Part 5 section 192 lists the areas.
ORGANISATION_GROUPS: tuple[tuple[str, tuple[RelationType, ...]], ...] = (
    ("Prerequisites", (RelationType.PREREQUISITE_OF,)),
    ("Dependencies", (RelationType.DEPENDS_ON, RelationType.REQUIRES)),
    ("Related concepts", (RelationType.RELATED_TO,)),
    ("Applications", (RelationType.APPLIES_TO, RelationType.USES)),
)


@dataclass(frozen=True, slots=True)
class Basis:
    """One recorded basis of an inferred edge, resolved far enough to read it."""

    inference: RelationshipInference
    #: The source occurrence the basis names - for R1, a definition's evidence.
    occurrence: SourceOccurrence | None
    #: The concept whose definition that occurrence is: the direction of the mention.
    mentioning_concept_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LabelledEdge:
    """A stored edge with what backs it - its occurrences, or its bases."""

    relationship: Relationship
    #: EXPLICIT backing: where a source states the relation.
    occurrences: tuple[RelationshipOccurrence, ...] = ()
    #: INFERRED backing: which rule inferred it, from what (ADR 0027).
    bases: tuple[Basis, ...] = ()

    @property
    def origin(self) -> RelationshipOrigin:
        return self.relationship.origin


@dataclass(frozen=True, slots=True)
class HierarchyNode:
    concept: Concept
    #: The edge that placed this node under its parent; None for a root.
    via: LabelledEdge | None
    children: tuple["HierarchyNode", ...] = ()


@dataclass(frozen=True, slots=True)
class HierarchyView:
    """V1. `roots` is empty when the run has no source-stated hierarchy edge."""

    run_id: str
    roots: tuple[HierarchyNode, ...]
    edges: tuple[LabelledEdge, ...]
    #: Each cycle met during traversal, as the concept ids around it.
    cycles: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class OrganisationView:
    """V2. Every group is present, empty or not, so absence is stated, not implied."""

    run_id: str
    groups: tuple[tuple[str, tuple[LabelledEdge, ...]], ...]
    #: Concept id to canonical name, for every concept of the run.
    concept_names: dict[str, str]

    def group(self, name: str) -> tuple[LabelledEdge, ...]:
        return dict(self.groups)[name]


def hierarchy_view(repository: Repository, run_id: str) -> HierarchyView:
    """Build V1 for one run from stored edges. Stores nothing, infers nothing."""
    connection = repository.connection
    concepts = {c.id: c for c in queries.concepts_of_run(connection, run_id)}
    parent_is_from = dict(HIERARCHY_TYPES)
    stored = queries.edges_among_run_concepts(
        connection, run_id, tuple(t for t, _ in HIERARCHY_TYPES)
    )
    edges = tuple(_label(repository, edge) for edge in stored)

    children: dict[str, list[tuple[str, LabelledEdge]]] = {}
    has_parent: set[str] = set()
    for labelled in edges:
        edge = labelled.relationship
        if parent_is_from[edge.relation_type]:
            parent, child = edge.from_concept_id, edge.to_concept_id
        else:
            parent, child = edge.to_concept_id, edge.from_concept_id
        children.setdefault(parent, []).append((child, labelled))
        has_parent.add(child)
    for items in children.values():
        items.sort(key=lambda item: (concepts[item[0]].canonical_name, item[0], item[1].relationship.id))

    cycles: list[tuple[str, ...]] = []
    visited: set[str] = set()

    def build(concept_id: str, via: LabelledEdge | None, path: tuple[str, ...]) -> HierarchyNode:
        visited.add(concept_id)
        nested = []
        for child, labelled in children.get(concept_id, ()):
            if child in path or child == concept_id:
                # A cycle: report it and stop here, so traversal terminates.
                cycle = path[path.index(child):] + (concept_id,) if child in path else (concept_id,)
                cycles.append(cycle + (child,))
                continue
            nested.append(build(child, labelled, path + (concept_id,)))
        return HierarchyNode(concept=concepts[concept_id], via=via, children=tuple(nested))

    def order(ids) -> list[str]:
        return sorted(ids, key=lambda i: (concepts[i].canonical_name, i))

    roots = [build(root, None, ()) for root in order(p for p in children if p not in has_parent)]
    # Concepts reachable only around a cycle have no root; start each such cycle at
    # its first concept in name order, so it is still shown and still terminates.
    for start in order(p for p in children if p not in visited):
        if start not in visited:
            roots.append(build(start, None, ()))
    return HierarchyView(run_id=run_id, roots=tuple(roots), edges=edges, cycles=tuple(cycles))


def organisation_view(repository: Repository, run_id: str) -> OrganisationView:
    """Build V2 for one run from stored edges. Stores nothing, infers nothing."""
    groups = []
    for name, types in ORGANISATION_GROUPS:
        stored = queries.edges_among_run_concepts(repository.connection, run_id, types)
        groups.append((name, tuple(_label(repository, edge) for edge in stored)))
    names = {c.id: c.canonical_name for c in queries.concepts_of_run(repository.connection, run_id)}
    return OrganisationView(run_id=run_id, groups=tuple(groups), concept_names=names)


def _label(repository: Repository, edge: Relationship) -> LabelledEdge:
    """Attach what backs an edge: occurrences for EXPLICIT, bases for INFERRED.

    Both are read whatever the origin, because an edge R1 inferred may later be
    promoted to EXPLICIT and keeps its bases as the record of how it was first
    inferred (ADR 0028).
    """
    connection = repository.connection
    bases = []
    for inference in queries.inferences_for_relationship(connection, edge.id):
        occurrence = (
            repository.get(SourceOccurrence, inference.basis_occurrence_id)
            if inference.basis_occurrence_id is not None
            else None
        )
        mentioning = (
            queries.concepts_defined_by(connection, occurrence.knowledge_id)
            if occurrence is not None
            else ()
        )
        bases.append(Basis(inference=inference, occurrence=occurrence, mentioning_concept_ids=mentioning))
    return LabelledEdge(
        relationship=edge,
        occurrences=queries.occurrences_for_relationship(connection, edge.id),
        bases=tuple(bases),
    )
