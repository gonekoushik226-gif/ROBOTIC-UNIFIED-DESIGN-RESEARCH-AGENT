"""Stored conflict information attached to a node (ADR 0039 P10-19; U8a(i), U8b(i)).

Only information already stored is read - Phase 8's `conflict` rows and ACTIVE
`CONTRADICTS` relationships. Nothing is compared, judged or written, and no new
consistency rule exists (U8b(i)).

**Attachment** follows Phase 9's convention (ADR 0036 P9-15): a conflict belongs to a
concept when one of its claims is a knowledge object that a stored relationship links
to that concept; a `CONTRADICTS` relationship belongs to both its endpoints. For a node
made available by an admitted item, a conflict naming that item as a claim, or a
`CONTRADICTS` relationship touching it, also belongs to the node: it is the node's
support.

**Scope** (P9-5): a linking relationship, a `CONTRADICTS` relationship and the claim a
conflict is found through - the knowledge object linked to the node, or the admitted
item - are used only with evidence in the requested scope. The conflict then attaches
whatever the other claim's scope, as Phase 9 attaches it (P9-15; ADR 0039 P10-19, as the
user confirmed on 2026-09-25): the node is CONFLICTING and everything depending on it is
BLOCKED. The other claim is shown in full only when it too has evidence in scope and is
not `DELETED` or `ARCHIVED`; otherwise it is **withheld** - its identifier kept, its
content never shown - and counted. A conflict stored `DELETED` or `ARCHIVED` is not used
(P9-23).
"""

from app.models.entities import KnowledgeObject
from app.models.enums import LifecycleStatus, RelationType
from app.reasoning.context import ReasoningContext
from app.reasoning.provenance import superseded_pointer
from app.reasoning.results import Claim, ConflictItem, ContradictionItem
from app.reasoning.scope import counter, lifecycle_excluded
from app.storage import queries


def _claim(context: ReasoningContext, knowledge_id: str) -> Claim:
    """One side of a conflict: in full when usable in scope, otherwise withheld."""
    knowledge = context.get(KnowledgeObject, knowledge_id)
    if not context.usable_knowledge(knowledge):  # counted as withheld or excluded
        return Claim(knowledge_id=knowledge_id)
    evidence = context.kept(knowledge_id)
    pointer = (
        superseded_pointer(context.connection, knowledge_id)
        if knowledge.lifecycle_status is LifecycleStatus.SUPERSEDED
        else None
    )
    return Claim(
        knowledge_id=knowledge_id,
        knowledge=knowledge,
        evidence=evidence,
        provenance=context.provenance(evidence),
        superseded_by=pointer,
    )


def _contradiction(context: ReasoningContext, relationship) -> ContradictionItem:
    evidence = context.kept(relationship.id)
    return ContradictionItem(
        relationship=relationship, evidence=evidence, provenance=context.provenance(evidence)
    )


def conflict_information(
    context: ReasoningContext, concept_id: str, admitted: tuple[str, ...] = ()
) -> tuple[tuple[ConflictItem, ...], tuple[ContradictionItem, ...]]:
    """The stored conflicts and contradictions attached to one node, by numeric counter."""
    linked: set[str] = set(admitted)
    contradictions: dict[str, ContradictionItem] = {}
    for relationship in queries.relationships_for_concept(context.connection, concept_id):
        if not context.usable_relationship(relationship):
            continue
        if relationship.relation_type is RelationType.CONTRADICTS:
            contradictions[relationship.id] = _contradiction(context, relationship)
        for knowledge_id in (relationship.from_knowledge_id, relationship.to_knowledge_id):
            if knowledge_id is not None and context.usable_knowledge(
                context.get(KnowledgeObject, knowledge_id)
            ):
                linked.add(knowledge_id)
    for knowledge_id in admitted:
        for relationship in queries.relationships_touching_knowledge(
            context.connection, knowledge_id, RelationType.CONTRADICTS
        ):
            if context.usable_relationship(relationship):
                contradictions[relationship.id] = _contradiction(context, relationship)
    conflicts: dict[str, ConflictItem] = {}
    for knowledge_id in sorted(linked, key=counter):
        for conflict in queries.conflicts_of_knowledge(context.connection, knowledge_id):
            if conflict.id in conflicts or lifecycle_excluded(conflict.lifecycle_status):
                continue
            conflicts[conflict.id] = ConflictItem(
                conflict=conflict,
                claim_a=_claim(context, conflict.claim_a_id),
                claim_b=_claim(context, conflict.claim_b_id),
            )
    return (
        tuple(conflicts[key] for key in sorted(conflicts, key=counter)),
        tuple(contradictions[key] for key in sorted(contradictions, key=counter)),
    )
