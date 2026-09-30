"""Writing the outcome of a comparison: assessment records, conflicts, concept records.

Shared by stage 15 and `merge`, so both write exactly the same rows for the same
judgement. Everything goes through `Repository`, which validates before the schema
checks again. Nothing here commits; the caller owns the transaction.

What each outcome writes (ADR 0033):

* `EXACT_DUPLICATE` - one assessment record; the link itself (a new source
  occurrence) or the supersession is the caller's.
* `POSSIBLE_DUPLICATE` - one assessment record. Both objects stay as they are.
* `CONTRADICTORY` (rule C1) - a `conflict` row with cause `UNDETERMINED`, lifecycle
  `ACTIVE` and no context (none is stored to establish one, and none is invented),
  plus the assessment record naming it. Neither claim's lifecycle, certainty or
  evidence changes (P8-15).
* `POSSIBLE_EQUIVALENT` - one concept-equivalence record per unordered pair.
"""

from app.deduplication.rules import (
    RULE_C1,
    RULE_CONCEPT,
    RULE_SAME_CONCEPT,
    RULE_VERSION,
    c1_contradicts,
    canonical_pair,
)
from app.models.base import utc_now
from app.models.entities import (
    ConceptEquivalence,
    Conflict,
    KnowledgeEquivalence,
)
from app.models.enums import (
    ConceptEquivalenceBasis,
    ConceptEquivalenceStatus,
    ConflictCause,
    KnowledgeEquivalenceOutcome,
    LifecycleStatus,
)
from app.storage.repository import Repository


class Recorder:
    """Writes the rows a comparison outcome requires, and nothing else."""

    __slots__ = ("_repository",)

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def exact(
        self,
        canonical_id: str,
        *,
        run_id: str | None,
        other_knowledge_id: str | None = None,
        linked_occurrence_id: str | None = None,
        rule: str,
    ) -> KnowledgeEquivalence:
        """An `EXACT_DUPLICATE` record: a linked occurrence, or a merge's pointer."""
        return self._assessment(
            canonical_id,
            KnowledgeEquivalenceOutcome.EXACT_DUPLICATE,
            rule=rule,
            run_id=run_id,
            other_knowledge_id=other_knowledge_id,
            linked_occurrence_id=linked_occurrence_id,
        )

    def compare(
        self,
        existing_id: str,
        new_id: str,
        *,
        existing_statement: str,
        new_statement: str,
        rule: str,
        run_id: str | None,
    ) -> KnowledgeEquivalence:
        """A non-exact comparison of two canonical objects.

        Rule C1 is applied to same-concept pairs (rule `P8-13`) only: it is defined
        for "two same-type, same-concept statements" (P8-14). A C1 pair becomes a
        conflict plus a `CONTRADICTORY` record; every other pair `POSSIBLE_DUPLICATE`.
        """
        if rule == RULE_SAME_CONCEPT and c1_contradicts(existing_statement, new_statement):
            now = utc_now()
            conflict = self._repository.add(
                Conflict(
                    id=self._repository.new_id(Conflict),
                    created_at=now,
                    updated_at=now,
                    claim_a_id=existing_id,
                    claim_b_id=new_id,
                    cause=ConflictCause.UNDETERMINED,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                )
            )
            return self._assessment(
                existing_id,
                KnowledgeEquivalenceOutcome.CONTRADICTORY,
                rule=RULE_C1,
                run_id=run_id,
                other_knowledge_id=new_id,
                conflict_id=conflict.id,
            )
        return self._assessment(
            existing_id,
            KnowledgeEquivalenceOutcome.POSSIBLE_DUPLICATE,
            rule=rule,
            run_id=run_id,
            other_knowledge_id=new_id,
        )

    def concepts(
        self,
        first: str,
        second: str,
        *,
        basis: ConceptEquivalenceBasis,
        run_id: str | None,
        shared_name: str | None = None,
        relationship_id: str | None = None,
    ) -> ConceptEquivalence:
        """One `POSSIBLE_EQUIVALENT` record for an unordered concept pair (P8-16)."""
        concept_a, concept_b = canonical_pair(first, second)
        now = utc_now()
        return self._repository.add(
            ConceptEquivalence(
                id=self._repository.new_id(ConceptEquivalence),
                created_at=now,
                updated_at=now,
                concept_a_id=concept_a,
                concept_b_id=concept_b,
                status=ConceptEquivalenceStatus.POSSIBLE_EQUIVALENT,
                basis=basis,
                rule=RULE_CONCEPT,
                rule_version=RULE_VERSION,
                shared_name=shared_name,
                relationship_id=relationship_id,
                extraction_run_id=run_id,
            )
        )

    def _assessment(
        self,
        canonical_id: str,
        outcome: KnowledgeEquivalenceOutcome,
        *,
        rule: str,
        run_id: str | None,
        other_knowledge_id: str | None = None,
        linked_occurrence_id: str | None = None,
        conflict_id: str | None = None,
    ) -> KnowledgeEquivalence:
        now = utc_now()
        return self._repository.add(
            KnowledgeEquivalence(
                id=self._repository.new_id(KnowledgeEquivalence),
                created_at=now,
                updated_at=now,
                canonical_knowledge_id=canonical_id,
                outcome=outcome,
                rule=rule,
                rule_version=RULE_VERSION,
                other_knowledge_id=other_knowledge_id,
                linked_occurrence_id=linked_occurrence_id,
                extraction_run_id=run_id,
                conflict_id=conflict_id,
            )
        )
