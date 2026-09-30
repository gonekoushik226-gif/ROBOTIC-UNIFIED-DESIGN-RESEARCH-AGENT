"""Stage 15 - deduplication and equivalence, inline in the extraction writer.

Decision P8-11 (ADR 0033): *each new item is compared with existing knowledge
before any knowledge object is created for it*. Section 79 orders the comparison
before creation and calls it mandatory; section 225 says an exact duplicate adds
"source only". A step that compared after the run had committed would have stored
the duplicate first, so this runs inside the extraction transaction.

The extraction writer asks, for every item, what to do (`decide`), then reports back
what it wrote (`linked` or `created`); this class records the outcome:

    exact duplicate  -> the writer links a new source occurrence to the existing
                        object (smallest counter); one EXACT_DUPLICATE record
    new object       -> the writer creates it; each canonical member it is compared
                        with gets POSSIBLE_DUPLICATE, or - rule C1 - a conflict, a
                        CONTRADICTORY record and a POTENTIAL_CONTRADICTION issue
    end of the run   -> POSSIBLE_EQUIVALENT concept records, each with a
                        DUPLICATE_CONCEPT issue (P8-16, P8-17)

Which items get a non-exact comparison (P8-12, P8-13): a `DEFINITION`, and a
`PROPERTY` whose owner the writer resolved, against canonical objects of a concept
with the same identity; an `EQUATION` identical to one at another location. Every
other type is linked when identical and otherwise simply created.

The issues are returned to the writer as notes, because every issue belongs to the
run and the pipeline owns issue rows. Nothing here commits.
"""

from dataclasses import dataclass

from app.deduplication.equivalence import equivalent_pairs
from app.deduplication.index import CONCEPT_RELATION, Item, KnowledgeIndex
from app.deduplication.records import Recorder
from app.deduplication.rules import RULE_EXACT, RULE_SAME_CONCEPT, normalized_statement
from app.models.entities import Concept, ExtractionRun
from app.models.enums import ExtractionIssueType, KnowledgeEquivalenceOutcome, KnowledgeType
from app.storage import queries
from app.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class Decision:
    """What the writer must do with one item."""

    #: The existing object to link a new source occurrence to; None to create one.
    link_to: str | None = None
    #: The canonical objects a newly created object is compared with.
    compare_with: tuple[str, ...] = ()
    #: The rule those comparisons are recorded under.
    rule: str | None = None


@dataclass(frozen=True, slots=True)
class IssueNote:
    """An extraction issue stage 15 found; the pipeline turns it into a row."""

    issue_type: ExtractionIssueType
    detail: str
    page_number: int | None = None
    excerpt: str | None = None


class Stage15:
    """Deduplication for one extraction run. Holds the in-memory index for the run."""

    __slots__ = ("_repository", "_run", "_index", "_recorder",
                 "assessments", "conflicts", "concept_records", "issues")

    def __init__(self, repository: Repository, run: ExtractionRun) -> None:
        self._repository = repository
        self._run = run
        self._index = KnowledgeIndex.load(repository.connection)
        self._recorder = Recorder(repository)
        #: Assessment records written, by outcome.
        self.assessments: dict[str, int] = {}
        #: Conflicts created, in order: (conflict, claim A - existing, claim B - new).
        self.conflicts: list[tuple[str, str, str]] = []
        #: Concept-equivalence records written.
        self.concept_records = 0
        self.issues: list[IssueNote] = []

    # ------------------------------------------------------------------ items

    def decide(self, item: Item) -> Decision:
        """Link, or create and compare - decided before anything is written."""
        self._know(item.concept_id)
        matches = self._index.exact_matches(item)
        if matches:
            return Decision(link_to=matches[0])
        if item.knowledge_type is KnowledgeType.EQUATION:
            return Decision(compare_with=tuple(self._index.same_statement(item)), rule=RULE_EXACT)
        if item.knowledge_type in CONCEPT_RELATION and item.concept_id is not None:
            candidates = self._index.same_concept(
                item.knowledge_type,
                normalized_statement(item.statement),
                self._index.concept_aliases(item.concept_id),
            )
            return Decision(compare_with=tuple(candidates), rule=RULE_SAME_CONCEPT)
        return Decision()

    def linked(self, item: Item, knowledge_id: str, occurrence_id: str) -> None:
        """The writer linked `occurrence_id` to the existing object: record it."""
        self._index.add_location(knowledge_id, item.location)
        self._recorder.exact(
            knowledge_id, run_id=self._run.id, linked_occurrence_id=occurrence_id, rule=RULE_EXACT
        )
        self._count(KnowledgeEquivalenceOutcome.EXACT_DUPLICATE)

    def created(self, item: Item, knowledge_id: str, decision: Decision, *, excerpt: str) -> None:
        """The writer created a new object: compare it with each canonical member."""
        self._index.add(knowledge_id, item.knowledge_type, item.statement)
        self._index.add_location(knowledge_id, item.location)
        for existing in decision.compare_with:
            record = self._recorder.compare(
                existing,
                knowledge_id,
                existing_statement=self._index.normalized(existing),
                new_statement=item.statement,
                rule=decision.rule or RULE_SAME_CONCEPT,
                run_id=self._run.id,
            )
            self._count(record.outcome)
            if record.conflict_id is not None:
                self.conflicts.append((record.conflict_id, existing, knowledge_id))
                self.issues.append(
                    IssueNote(
                        issue_type=ExtractionIssueType.POTENTIAL_CONTRADICTION,
                        detail=(
                            f"rule C1: {knowledge_id} and {existing} are {item.knowledge_type} "
                            "statements of the same concept that differ only in numeric values; "
                            f"conflict {record.conflict_id} recorded with cause UNDETERMINED. Both "
                            "claims stay ACTIVE with their evidence; nothing was merged. Review: "
                            f"python -m app review {knowledge_id}"
                        ),
                        page_number=item.location[1],
                        excerpt=excerpt,
                    )
                )

    def attach_concept(self, knowledge_id: str, concept_id: str) -> None:
        """The writer joined a concept to the object (DEFINED_BY or HAS_PROPERTY)."""
        self._know(concept_id)
        self._index.attach_concept(knowledge_id, concept_id)

    # ------------------------------------------------------------ end of run

    def finish(self, concept_ids) -> None:
        """P8-16 for the run's concepts: record each qualifying pair once."""
        connection = self._repository.connection
        pairs = equivalent_pairs(connection, tuple(concept_ids))
        if not pairs:
            return
        recorded = queries.recorded_concept_pairs(connection)
        for pair in pairs:
            if (pair.concept_a_id, pair.concept_b_id) in recorded:
                continue
            record = self._recorder.concepts(
                pair.concept_a_id,
                pair.concept_b_id,
                basis=pair.basis,
                run_id=self._run.id,
                shared_name=pair.shared_name,
                relationship_id=pair.relationship_id,
            )
            self.concept_records += 1
            self.issues.append(
                IssueNote(
                    issue_type=ExtractionIssueType.DUPLICATE_CONCEPT,
                    detail=(
                        f"concepts {self._name(pair.concept_a_id)} and "
                        f"{self._name(pair.concept_b_id)} are recorded POSSIBLE_EQUIVALENT "
                        f"({record.id}, basis {pair.basis}"
                        + (f", shared name \"{pair.shared_name}\"" if pair.shared_name else "")
                        + (f", edge {pair.relationship_id}" if pair.relationship_id else "")
                        + "). They are not merged (Part 2 section 44)."
                    ),
                )
            )

    # ------------------------------------------------------------- internals

    def _know(self, concept_id: str | None) -> None:
        """Load a concept's stored names the first time the run uses the concept."""
        if concept_id is None or self._index.knows_concept(concept_id):
            return
        aliases = queries.aliases_for_concept(self._repository.connection, concept_id)
        self._index.set_aliases(concept_id, (alias.normalized_alias for alias in aliases))

    def _name(self, concept_id: str) -> str:
        concept = self._repository.get(Concept, concept_id)
        return concept_id if concept is None else f"{concept_id} (\"{concept.canonical_name}\")"

    def _count(self, outcome: KnowledgeEquivalenceOutcome) -> None:
        self.assessments[outcome.value] = self.assessments.get(outcome.value, 0) + 1
