"""Semantic retrieval as deterministic widening (D2, ADR 0035 P9-4; ADR 0036 P9-17).

From each resolved concept, **one step** over stored data only:

* recorded `POSSIBLE_EQUIVALENT` concept-equivalence records naming it, with their
  basis (shared name in the same or another document, or a stated `EQUIVALENT_TO`);
* ACTIVE `EXPLICIT` `EQUIVALENT_TO` edges touching it - the source-stated
  different-name case of section 44, reachable before any record is written.

Every concept reached is a **possible** equivalent: shown in its own section with its
basis and evidence and its own knowledge in its own groups; never treated as the same
concept (section 44; P6 section 45), never merged into the target's groups, never
counted among the target's sources, never widened again (an equivalence of an
equivalence is an inference Phase 9 does not make). A concept that answers to the
requested name itself is already shown above, so only its basis is listed.

A stated equivalence widens only when its statement has evidence in scope. The
request's relation-type and origin filters narrow listed knowledge, not the basis of
widening. No LLM, embedding, vector store, provider or dependency.
"""

from app.models.enums import ConceptEquivalenceBasis, ConceptEquivalenceStatus, RelationshipOrigin, RelationType
from app.query.concepts import build_section
from app.query.results import ConceptSection, LinkedEdge, PossibleEquivalent
from app.query.scope import QueryContext, counter
from app.query.traversal import label

NOTHING_STORED = (
    "No concept-equivalence record and no source-stated EQUIVALENT_TO edge is stored for "
    "the resolved concept(s). Records are written by stage 15 (extractor version "
    "4) or by `merge`; the absence of a record is not evidence that no equivalence exists."
)
NOTHING_IN_SCOPE = (
    "Stored equivalence data names the resolved concept(s), but none of it is backed by "
    "authorised evidence in scope, so nothing was widened."
)


def _seen_from_other_side(resolved: set[str], here: str, other: str) -> bool:
    """A pair of two resolved concepts is listed once, under the larger counter."""
    return here in resolved and other in resolved and counter(here) > counter(other)


class _Entry:
    def __init__(self) -> None:
        self.of: set[str] = set()
        self.records: dict[str, object] = {}
        self.edges: dict[str, LinkedEdge] = {}


def possible_equivalents(
    context: QueryContext, section: ConceptSection
) -> tuple[tuple[PossibleEquivalent, ...], str | None]:
    """The possible equivalents of a section's resolved concepts, and a note if none."""
    resolved = {rc.concept.id for rc in section.concepts}
    entries: dict[str, _Entry] = {}
    stored_anything = False
    for rc in section.concepts:
        here = rc.concept.id
        for record in rc.equivalence_records:
            if record.status is not ConceptEquivalenceStatus.POSSIBLE_EQUIVALENT:
                continue
            stored_anything = True
            stated = None
            if record.basis is ConceptEquivalenceBasis.STATED_EQUIVALENT_TO:
                edge = context.relationship(record.relationship_id)
                stated = None if edge is None else label(context, edge)
                if stated is None:
                    continue
            other = record.concept_b_id if record.concept_a_id == here else record.concept_a_id
            if _seen_from_other_side(resolved, here, other):
                continue
            entry = entries.setdefault(other, _Entry())
            entry.of.add(here)
            entry.records[record.id] = record
            if stated is not None:
                entry.edges[stated.relationship.id] = stated
        for edge in context.edges_of(here):
            if (
                edge.relation_type is not RelationType.EQUIVALENT_TO
                or edge.origin is not RelationshipOrigin.EXPLICIT
                or edge.from_concept_id is None
                or edge.to_concept_id is None
            ):
                continue
            stored_anything = True
            stated = label(context, edge)
            if stated is None:
                continue
            other = edge.to_concept_id if edge.from_concept_id == here else edge.from_concept_id
            if _seen_from_other_side(resolved, here, other):
                continue
            entry = entries.setdefault(other, _Entry())
            entry.of.add(here)
            entry.edges[edge.id] = stated

    found = []
    for other_id in sorted(entries, key=counter):
        entry = entries[other_id]
        concept = context.concept(other_id)
        if concept is None:  # pragma: no cover - records and edges name stored concepts
            continue
        common = {
            "concept": concept,
            "of_concept_ids": tuple(sorted(entry.of, key=counter)),
            "records": tuple(entry.records[key] for key in sorted(entry.records, key=counter)),
            "stated_edges": tuple(entry.edges[key] for key in sorted(entry.edges, key=counter)),
        }
        if other_id in resolved:
            found.append(PossibleEquivalent(**common, already_resolved=True))
            continue
        own = build_section(context, ((concept, None),))
        if own is not None:
            found.append(PossibleEquivalent(**common, section=own))
    if found:
        return tuple(found), None
    return (), NOTHING_IN_SCOPE if stored_anything else NOTHING_STORED
