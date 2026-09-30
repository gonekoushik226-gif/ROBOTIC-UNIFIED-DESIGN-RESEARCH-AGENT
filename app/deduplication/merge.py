"""`merge` - supersede in place the exact duplicates stored before stage 15.

Decision P8-19 (ADR 0033), command P8-30 (ADR 0034). It runs only when the user asks
for it: `extract` and `db` never call it, because merging stored knowledge while the
user asked for something else would expand the requested objective (section 108;
Part 6 sections 31-32).

**What it writes, and nothing else.**

1. Exact-duplicate groups are formed among the ACTIVE knowledge objects by P8-12's
   rule. In each group the member with the smallest numeric identifier counter is
   canonical; every other member becomes `SUPERSEDED` (with `updated_at`), and so
   does its companion row (`equation`, `variable`, `rule`, `procedure`). One
   `EXACT_DUPLICATE` record with no run is the pointer from the canonical object to
   each superseded one - there is no `merged_into` column and no move log.
2. The comparison rules P8-12 ... P8-16 are then applied among the stored knowledge,
   canonical members only, with no run: `POSSIBLE_DUPLICATE`, or - rule C1 - a
   conflict and a `CONTRADICTORY` record, and `POSSIBLE_EQUIVALENT` concept records.
   A pair already recorded (by stage 15 or an earlier merge) is not recorded again.

**What it never touches:** no source occurrence, relationship, relationship
occurrence or `relationship_inference` row is moved, deleted or rewritten; statement,
certainty, `knowledge_version` and `normalized_hash` (still NULL) are unchanged; no
extraction issue is written, because every issue belongs to an extraction run and a
merge is not one (P8-17). Nothing is deleted.

**One merge is one transaction**, as one classification is (ADR 0027): it refuses to
start on uncommitted work, commits once, and rolls everything back on failure. A
second merge finds no ACTIVE exact duplicates and no unrecorded pair, and writes
nothing.
"""

from dataclasses import dataclass, replace

from app.core.errors import InvalidInputError
from app.deduplication.equivalence import equivalent_pairs
from app.deduplication.index import CONCEPT_RELATION, KnowledgeIndex
from app.deduplication.records import Recorder
from app.deduplication.rules import RULE_EXACT, RULE_SAME_CONCEPT, counter
from app.models.entities import KnowledgeObject
from app.models.enums import KnowledgeType, LifecycleStatus
from app.storage import queries
from app.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class MergeReport:
    """What one merge committed. Every number is a count of rows written."""

    #: Each exact-duplicate group merged, canonical member first.
    groups: tuple[tuple[str, ...], ...]
    #: Knowledge objects changed from ACTIVE to SUPERSEDED.
    superseded: int
    #: Companion rows changed from ACTIVE to SUPERSEDED with their objects.
    companions_superseded: int
    #: EXACT_DUPLICATE pointer records written (one per superseded object).
    pointers: int
    #: Non-exact comparison records written, by outcome.
    assessments: dict[str, int]
    #: Conflicts created by rule C1.
    conflicts: tuple[str, ...]
    #: POSSIBLE_EQUIVALENT concept records written.
    concept_records: int

    @property
    def changed(self) -> bool:
        return bool(self.pointers or self.assessments or self.concept_records)


class Merger:
    """Supersede stored exact duplicates in place. Holds no state between merges."""

    __slots__ = ("_repository",)

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def merge(self) -> MergeReport:
        """Merge, record and commit, as one transaction; or change nothing."""
        connection = self._repository.connection
        if connection.in_transaction:
            raise InvalidInputError.of(
                "The merge was not started: the connection has uncommitted work.",
                "A merge is exactly one transaction (ADR 0033, P8-19). Starting it on top "
                "of pending writes would commit or roll back work that is not its own.",
                stage="deduplication.merge",
                data_changed=False,
                retry_safe=True,
                next_options=("Commit or roll back the pending work, then merge.",),
            )
        try:
            report = self._merge()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return report

    def _merge(self) -> MergeReport:
        repository = self._repository
        connection = repository.connection
        index = KnowledgeIndex.load(connection)
        recorder = Recorder(repository)

        groups = index.duplicate_groups()
        superseded = companions = 0
        for group in groups:
            canonical, others = group[0], group[1:]
            for other in others:
                knowledge = repository.get(KnowledgeObject, other)
                repository.update(replace(knowledge, lifecycle_status=LifecycleStatus.SUPERSEDED))
                for companion in queries.companions_of_knowledge(connection, other):
                    if companion.lifecycle_status is LifecycleStatus.ACTIVE:
                        repository.update(
                            replace(companion, lifecycle_status=LifecycleStatus.SUPERSEDED)
                        )
                        companions += 1
                recorder.exact(canonical, run_id=None, other_knowledge_id=other, rule=RULE_EXACT)
                index.remove(other)
                superseded += 1

        compared = set(queries.compared_knowledge_pairs(connection))
        assessments: dict[str, int] = {}
        conflicts: list[str] = []
        for knowledge_id in index.ids():
            knowledge_type = index.knowledge_type(knowledge_id)
            if knowledge_type is KnowledgeType.EQUATION:
                peers, rule = index.statement_peers(knowledge_id), RULE_EXACT
            elif knowledge_type in CONCEPT_RELATION:
                peers = index.same_concept(
                    knowledge_type,
                    index.normalized(knowledge_id),
                    index.object_aliases(knowledge_id),
                )
                rule = RULE_SAME_CONCEPT
            else:
                continue
            for peer in peers:
                # Each unordered pair once, the smaller counter as the existing side.
                if counter(peer) <= counter(knowledge_id):
                    continue
                pair = frozenset((knowledge_id, peer))
                if pair in compared:
                    continue
                record = recorder.compare(
                    knowledge_id,
                    peer,
                    existing_statement=index.normalized(knowledge_id),
                    new_statement=index.normalized(peer),
                    rule=rule,
                    run_id=None,
                )
                compared.add(pair)
                assessments[record.outcome.value] = assessments.get(record.outcome.value, 0) + 1
                if record.conflict_id is not None:
                    conflicts.append(record.conflict_id)

        recorded = queries.recorded_concept_pairs(connection)
        concept_records = 0
        for pair in equivalent_pairs(connection):
            if (pair.concept_a_id, pair.concept_b_id) in recorded:
                continue
            recorder.concepts(
                pair.concept_a_id,
                pair.concept_b_id,
                basis=pair.basis,
                run_id=None,
                shared_name=pair.shared_name,
                relationship_id=pair.relationship_id,
            )
            concept_records += 1

        return MergeReport(
            groups=tuple(tuple(group) for group in groups),
            superseded=superseded,
            companions_superseded=companions,
            pointers=superseded,
            assessments=dict(sorted(assessments.items())),
            conflicts=tuple(conflicts),
            concept_records=concept_records,
        )


__all__ = ["MergeReport", "Merger"]
