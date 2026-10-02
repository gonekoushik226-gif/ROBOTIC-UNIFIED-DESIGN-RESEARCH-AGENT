"""Concept mode: exact identity, direction-aware groups, attribution (ADR 0036 P9-11 ... P9-17).

**Identity** (P9-11): the name is matched by D-30 only (NFKC, casefold, whitespace
collapse) against every ACTIVE alias. No plural folding, no stemming. Every concept
answering to the name is resolved; none is chosen (section 97).

**Groups** (P9-13), always all present, in this fixed order:

    Definitions    DEFINED_BY    concept -> object
    Properties     HAS_PROPERTY  concept -> object
    Prerequisites  PREREQUISITE_OF          prerequisite -> concept
    Dependencies   DEPENDS_ON, REQUIRES     concept -> dependency
    Applications   APPLIES_TO, USES         concept -> application
    Relationships  every other edge touching the concept, and the edges above in the
                   opposite direction, with their real type, direction and origin
    Equations, Variables, Examples
                   objects of that type with a stored edge to the concept - D1: none
                   are produced by extraction today, and this is stated, not hidden

An object of type EQUATION, VARIABLE or EXAMPLE goes to its own group whatever edge
reached it. Everything else follows the edge and its stored direction.

**Attribution and single listing:** an item reached from several resolved concepts is
listed once, with every concept and edge that reached it, in the earliest of its
groups. **No Mentions group:** a word match is not a link (P9-3).

**Order** (P9-22): within a group, by the item's first cited evidence row (document,
page, character offset) and then its numeric identifier counter.
"""

from app.models.entities import Concept, ConceptAlias, KnowledgeObject
from app.models.enums import KnowledgeType, RelationType
from app.query.conflicts import conflict_items, knowledge_item
from app.query.provenance import cited_rows, provenance_of
from app.query.results import (
    ConceptSection,
    EdgeEnd,
    EdgeItem,
    KnowledgeItem,
    Link,
    ResolvedConcept,
    ResultGroup,
)
from app.query.scope import QueryContext, counter, evidence_key, id_key
from app.query.traversal import closure, label
from app.storage import queries

DEFINITIONS = "Definitions"
PROPERTIES = "Properties"
PREREQUISITES = "Prerequisites"
DEPENDENCIES = "Dependencies"
APPLICATIONS = "Applications"
RELATIONSHIPS = "Relationships"
EQUATIONS = "Equations"
VARIABLES = "Variables"
EXAMPLES = "Examples"

GROUP_ORDER: tuple[str, ...] = (
    DEFINITIONS, PROPERTIES, PREREQUISITES, DEPENDENCIES, APPLICATIONS, RELATIONSHIPS,
    EQUATIONS, VARIABLES, EXAMPLES,
)

#: The section 199 categories D1 covers (ADR 0035 P9-3), by knowledge type.
D1_GROUPS: dict[KnowledgeType, str] = {
    KnowledgeType.EQUATION: EQUATIONS,
    KnowledgeType.VARIABLE: VARIABLES,
    KnowledgeType.EXAMPLE: EXAMPLES,
}

_EMPTY = "Nothing stored for the resolved concept(s) in this group, within the scope and filters."
_EMPTY_APPLICATIONS = (
    _EMPTY + " Applications are stored only as source-stated APPLIES_TO or USES edges from "
    "the concept; no APPLICATION object is ever written."
)


def resolve(context: QueryContext, normalized_name: str) -> tuple[tuple[Concept, ConceptAlias | None], ...]:
    """Every concept holding an ACTIVE alias with this D-30 form, by counter (P9-11)."""
    resolved = []
    for concept in queries.concepts_by_normalized_alias(context.connection, normalized_name):
        aliases = [
            alias for alias in queries.aliases_for_concept(context.connection, concept.id)
            if alias.normalized_alias == normalized_name
        ]
        alias = min(aliases, key=lambda a: counter(a.id)) if aliases else None
        resolved.append((concept, alias))
    return tuple(sorted(resolved, key=lambda pair: counter(pair[0].id)))


def group_of(relation_type: RelationType, end: EdgeEnd, other: KnowledgeObject | None) -> str:
    """The group of one link, from the stored edge and the concept's end of it."""
    if other is not None and other.knowledge_type in D1_GROUPS:
        return D1_GROUPS[other.knowledge_type]
    if other is not None and end is EdgeEnd.FROM:
        if relation_type is RelationType.DEFINED_BY:
            return DEFINITIONS
        if relation_type is RelationType.HAS_PROPERTY:
            return PROPERTIES
    if relation_type is RelationType.PREREQUISITE_OF and end is EdgeEnd.TO:
        return PREREQUISITES
    if relation_type in (RelationType.DEPENDS_ON, RelationType.REQUIRES) and end is EdgeEnd.FROM:
        return DEPENDENCIES
    if relation_type in (RelationType.APPLIES_TO, RelationType.USES) and end is EdgeEnd.FROM:
        return APPLICATIONS
    return RELATIONSHIPS


class _Draft:
    """One item while its links are being collected."""

    def __init__(self) -> None:
        self.links: list[Link] = []
        self.groups: set[int] = set()
        self.knowledge: KnowledgeObject | None = None
        self.evidence: tuple = ()
        self.neighbours: dict[str, Concept] = {}


def build_section(
    context: QueryContext, candidates: tuple[tuple[Concept, ConceptAlias | None], ...]
) -> ConceptSection | None:
    """The knowledge of these concepts, or None when none of them can be shown."""
    kept: list[tuple[Concept, ConceptAlias | None, tuple]] = []
    for concept, alias in candidates:
        if not context.concept_allowed(concept):
            context.filter_out(concept.id)
            continue
        occurrences = context.judge_item(concept.id, context.evidence(concept.id))
        if occurrences:
            context.listed(concept.id)
            kept.append((concept, alias, occurrences))
    if not kept:
        return None
    resolved_ids = {concept.id for concept, _, _ in kept}

    drafts: dict[str, _Draft] = {}
    for concept, _, _ in kept:
        for edge in context.edges_of(concept.id):
            if not context.edge_allowed(edge):
                context.filter_out(edge.id)
                continue
            linked = label(context, edge)
            if linked is None:
                continue
            end = EdgeEnd.FROM if edge.from_concept_id == concept.id else EdgeEnd.TO
            other_concept = edge.to_concept_id if end is EdgeEnd.FROM else edge.from_concept_id
            other_knowledge = edge.to_knowledge_id if end is EdgeEnd.FROM else edge.from_knowledge_id
            link = Link(concept_id=concept.id, concept_end=end, edge=linked)
            if other_knowledge is not None:
                knowledge = context.knowledge(other_knowledge)
                if knowledge is None:  # pragma: no cover - endpoints are foreign keys
                    continue
                if not context.knowledge_allowed(knowledge):
                    context.filter_out(knowledge.id)
                    continue
                evidence = context.judge_item(knowledge.id, context.evidence(knowledge.id))
                if not evidence:
                    continue
                draft = drafts.setdefault(knowledge.id, _Draft())
                draft.knowledge, draft.evidence = knowledge, evidence
            else:
                draft = drafts.setdefault(edge.id, _Draft())
                if other_concept not in resolved_ids:
                    neighbour = context.concept(other_concept)
                    if neighbour is not None:
                        draft.neighbours[neighbour.id] = neighbour
                knowledge = None
            draft.links.append(link)
            draft.groups.add(GROUP_ORDER.index(group_of(edge.relation_type, end, knowledge)))

    grouped: dict[int, list] = {index: [] for index in range(len(GROUP_ORDER))}
    for key, draft in drafts.items():
        links = tuple(sorted(
            draft.links,
            key=lambda l: (counter(l.concept_id), counter(l.edge.relationship.id), l.concept_end.value),
        ))
        if draft.knowledge is not None:
            item: KnowledgeItem | EdgeItem = knowledge_item(context, draft.knowledge, links, draft.evidence)
        else:
            item = EdgeItem(
                links=links,
                neighbours=tuple(sorted(draft.neighbours.values(), key=lambda c: counter(c.id))),
            )
        context.listed(key)
        grouped[min(draft.groups)].append((_position(item), id_key(key), item))

    groups = tuple(
        ResultGroup(
            name=name,
            items=tuple(entry[2] for entry in sorted(grouped[index], key=lambda e: (e[0], e[1]))),
            note=_note(context, name, bool(grouped[index])),
        )
        for index, name in enumerate(GROUP_ORDER)
    )
    listed_knowledge = [
        item.knowledge.id for group in groups for item in group.items if isinstance(item, KnowledgeItem)
    ]
    conflicts = conflict_items(context, sorted(listed_knowledge, key=counter))

    concepts = tuple(
        ResolvedConcept(
            concept=concept,
            matched_alias=alias,
            occurrences=occurrences,
            ancestors=_named(context, closure(context, concept.id, upward=True)),
            descendants=_named(context, closure(context, concept.id, upward=False)),
            equivalence_records=queries.equivalences_of_concept(context.connection, concept.id),
        )
        for concept, alias, occurrences in kept
    )
    return ConceptSection(
        concepts=concepts,
        groups=groups,
        conflicts=conflicts,
        provenance=provenance_of(context, cited_rows((concepts, groups, conflicts))),
    )


def _named(context: QueryContext, concept_ids: tuple[str, ...]) -> tuple[Concept, ...]:
    return tuple(c for c in (context.concept(i) for i in concept_ids) if c is not None)


def _position(item: KnowledgeItem | EdgeItem) -> tuple:
    """The item's place in text: its first cited evidence row (P9-22)."""
    if isinstance(item, KnowledgeItem):
        rows = item.evidence
    else:
        rows = item.edge.evidence + tuple(
            basis.occurrence for basis in item.edge.bases if basis.occurrence is not None
        )
    return min((evidence_key(row) for row in rows), default=((1 << 62),))


def _note(context: QueryContext, name: str, filled: bool) -> str | None:
    """What an empty group means; for the D1 groups, always the D1 statement."""
    d1_type = next((t for t, group in D1_GROUPS.items() if group == name), None)
    if d1_type is not None:
        if filled:
            return (
                f"Only {d1_type.value} objects with a stored edge to a resolved concept are "
                "listed; none is attached by co-location or a word match."
            )
        if d1_type not in context.linked_types():
            return (
                f"No stored edge links any {d1_type.value} object to any concept in this "
                "database, so none is claimed as knowledge about the concept. Section 199 is "
                "PARTIALLY IMPLEMENTED for this category."
            )
        return (
            f"No {d1_type.value} object has a stored edge to the resolved concept(s) within "
            "the scope and filters."
        )
    if filled:
        return None
    return _EMPTY_APPLICATIONS if name == APPLICATIONS else _EMPTY
