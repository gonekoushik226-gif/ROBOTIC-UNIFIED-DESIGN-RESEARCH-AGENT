"""Conflicts, assessment records and superseded objects, as stored (ADR 0036 P9-15 ... P9-17).

Everything here is `review`'s read model (ADR 0034 P8-26) put through the query's
scope - nothing is compared, judged or canonicalised at query time (P6 section 45):

* **Conflicts** (sections 46, 229): both claims with their evidence, the stored
  cause and context, and *"Resolution: not automatically selected"*. A claim with no
  evidence in scope is withheld - its identifier is kept, its content never shown.
* **Assessment records** naming an object, with their stored outcome (P9-17).
* **Superseded objects** (P9-16): the stored `merge` pointer to the canonical
  object; the objects superseded into a canonical one.
* **`number_of_sources`** (section 78): distinct sources behind the object's own
  source occurrences and those of the objects superseded into it - `review`'s
  count, over the evidence in scope. Informational: more sources is not more correct.
"""

from app.models.entities import KnowledgeObject
from app.query.results import Claim, ConflictItem, EvidenceRow, KnowledgeItem, Link
from app.query.scope import QueryContext, counter
from app.storage import queries


def knowledge_item(
    context: QueryContext,
    knowledge: KnowledgeObject,
    links: tuple[Link, ...],
    evidence: tuple[EvidenceRow, ...],
) -> KnowledgeItem:
    """One listed object with its stored companions, records and pointers."""
    review = context.review(knowledge.id)
    assessments = () if review is None else review.assessments
    superseded_into = () if review is None else review.superseded_into
    superseded_by = next(
        (
            a.record.canonical_knowledge_id
            for a in assessments
            if a.this_side == "other" and a.is_merge_pointer
        ),
        None,
    )
    counted = [row for row in evidence if row.subject_kind == "KNOWLEDGE_OBJECT"]
    for other in superseded_into:
        counted.extend(
            row for row in context.keep(context.evidence(other))
            if row.subject_kind == "KNOWLEDGE_OBJECT"
        )
    distinct = {row.id: row for row in counted}
    return KnowledgeItem(
        knowledge=knowledge,
        links=links,
        evidence=evidence,
        companions=queries.companions_of_knowledge(context.connection, knowledge.id),
        assessments=tuple(sorted((a.record for a in assessments), key=lambda r: counter(r.id))),
        superseded_by=superseded_by,
        superseded_into=tuple(sorted(superseded_into, key=counter)),
        source_occurrences=len(distinct),
        number_of_sources=len({row.source_id for row in distinct.values()}),
    )


def claim(context: QueryContext, knowledge_id: str) -> Claim:
    """One side of a conflict: shown with its evidence in scope, or withheld."""
    knowledge = context.knowledge(knowledge_id)
    evidence = () if knowledge is None else context.judge_item(knowledge_id, context.evidence(knowledge_id))
    if evidence:
        context.listed(knowledge_id)
    return Claim(
        knowledge_id=knowledge_id,
        knowledge=knowledge if evidence else None,
        evidence=evidence,
    )


def conflict_items(context: QueryContext, knowledge_ids) -> tuple[ConflictItem, ...]:
    """Every stored conflict one of these objects is a claim of, once each, by counter."""
    found: dict[str, ConflictItem] = {}
    for knowledge_id in knowledge_ids:
        review = context.review(knowledge_id)
        for reviewed in () if review is None else review.conflicts:
            conflict = reviewed.conflict
            if conflict.id not in found:
                found[conflict.id] = ConflictItem(
                    conflict=conflict,
                    claim_a=claim(context, conflict.claim_a_id),
                    claim_b=claim(context, conflict.claim_b_id),
                )
    return tuple(found[key] for key in sorted(found, key=counter))
