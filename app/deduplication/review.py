"""What `review` shows about one knowledge object (ADR 0034, P8-26).

A read model, assembled from stored rows only: the object and its evidence; each
conflict it is a claim of, with the other claim and that claim's evidence; each
equivalence assessment naming it; and, for the pointer records `merge` wrote, the
evidence of the objects superseded into it - so that a canonical object's full
sources are visible (ADR 0033, P8-19).

**`number_of_sources`** (Part 3 section 78) is the number of distinct source
records behind the union of the object's own source occurrences and those of the
objects superseded into it; the size of that union is reported beside it as
`source_occurrences`. The count is informational - section 78 forbids reading "more
sources" as "more correct".

Nothing here writes, lists, searches, ranks or traverses: one object, by identifier
(P7-1's exactness, carried to P8-26). Concept-equivalence records are not shown;
section 41 does not require them (a recorded limitation).
"""

from dataclasses import dataclass

from app.models.entities import (
    Conflict,
    KnowledgeEquivalence,
    KnowledgeObject,
    SourceOccurrence,
)
from app.models.enums import KnowledgeEquivalenceOutcome
from app.storage import queries
from app.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class ReviewedConflict:
    conflict: Conflict
    #: "A" or "B": which claim of the conflict the reviewed object is.
    this_claim: str
    other: KnowledgeObject | None
    other_evidence: tuple[dict, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewedAssessment:
    record: KnowledgeEquivalence
    #: "canonical" or "other": which side of the record the reviewed object is.
    this_side: str
    #: The knowledge object on the other side, when the other side is an object.
    other: KnowledgeObject | None = None
    #: The linked source occurrence, when the record is an extraction-time link.
    linked_occurrence: SourceOccurrence | None = None
    #: The other object's evidence - shown for a merge pointer, whose superseded
    #: object's sources belong to the canonical object's sources.
    other_evidence: tuple[dict, ...] = ()

    @property
    def is_merge_pointer(self) -> bool:
        """An EXACT_DUPLICATE record `merge` wrote: an object, and no run."""
        return (
            self.record.outcome is KnowledgeEquivalenceOutcome.EXACT_DUPLICATE
            and self.record.other_knowledge_id is not None
            and self.record.extraction_run_id is None
        )


@dataclass(frozen=True, slots=True)
class KnowledgeReview:
    knowledge: KnowledgeObject
    evidence: tuple[dict, ...]
    conflicts: tuple[ReviewedConflict, ...]
    assessments: tuple[ReviewedAssessment, ...]
    #: Objects `merge` superseded into this one, through its pointer records.
    superseded_into: tuple[str, ...]
    #: Source occurrences of this object and of the objects superseded into it.
    source_occurrences: int
    #: Distinct source records behind those occurrences (section 78).
    number_of_sources: int


def review_knowledge(repository: Repository, knowledge_id: str) -> KnowledgeReview | None:
    """Everything recorded about one knowledge object, or None when it does not exist."""
    knowledge = repository.get(KnowledgeObject, knowledge_id)
    if knowledge is None:
        return None
    connection = repository.connection

    def evidence(subject_id: str) -> tuple[dict, ...]:
        return tuple(queries.evidence_for(connection, subject_id))

    conflicts = []
    for conflict in queries.conflicts_of_knowledge(connection, knowledge_id):
        this_claim = "A" if conflict.claim_a_id == knowledge_id else "B"
        other_id = conflict.claim_b_id if this_claim == "A" else conflict.claim_a_id
        conflicts.append(
            ReviewedConflict(
                conflict=conflict,
                this_claim=this_claim,
                other=repository.get(KnowledgeObject, other_id),
                other_evidence=evidence(other_id),
            )
        )

    assessments = []
    superseded_into = []
    for record in queries.equivalences_of_knowledge(connection, knowledge_id):
        if record.canonical_knowledge_id == knowledge_id:
            this_side, other_id = "canonical", record.other_knowledge_id
        else:
            this_side, other_id = "other", record.canonical_knowledge_id
        assessment = ReviewedAssessment(
            record=record,
            this_side=this_side,
            other=None if other_id is None else repository.get(KnowledgeObject, other_id),
            linked_occurrence=(
                None
                if record.linked_occurrence_id is None
                else repository.get(SourceOccurrence, record.linked_occurrence_id)
            ),
        )
        if assessment.is_merge_pointer and this_side == "canonical":
            superseded_into.append(str(other_id))
            assessment = ReviewedAssessment(
                record=record,
                this_side=this_side,
                other=assessment.other,
                other_evidence=evidence(str(other_id)),
            )
        assessments.append(assessment)

    occurrences = list(queries.occurrences_of_knowledge(connection, knowledge_id))
    for other_id in superseded_into:
        occurrences.extend(queries.occurrences_of_knowledge(connection, other_id))
    return KnowledgeReview(
        knowledge=knowledge,
        evidence=evidence(knowledge_id),
        conflicts=tuple(conflicts),
        assessments=tuple(assessments),
        superseded_into=tuple(superseded_into),
        source_occurrences=len(occurrences),
        number_of_sources=len({o.source_id for o in occurrences}),
    )
