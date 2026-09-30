"""Classify one extraction run with rule R1 (Part 5 section 192, ADRs 0026-0029).

    select one named run -> read its stored definitions and names -> R1
    -> write each new edge WITH its basis -> commit once -> report

**What this writes, and nothing else** (ADR 0026, P6-8/P6-9): new `ACTIVE`
`INFERRED` `RELATED_TO` relationships and new `relationship_inference` rows. It
never creates or changes an `EXPLICIT` edge, never updates or deletes a row, never
writes a lifecycle status, and creates no concept, alias or knowledge object. It
writes nothing to `document`, `document_version` or `extraction_run`. Re-running
adds nothing already present and retracts nothing.

**What it reads** (P6-17): the run's concepts and the stored `normalized_alias` of
their ACTIVE aliases, and the stored source occurrences of their definitions -
never page text, never `document_structure` (P6-5), and never the extractor.

**Names are used as stored** (I6-E, decided 2026-09-23). R1 reads each alias's
stored `normalized_alias` rather than normalising the alias text again, and first
checks that the database records the alias normalisation version this build
implements (`alias_normalization_version`, ADR 0010). If it does not, classification
is refused: silently matching with a different normalisation would change what R1
means. By ADR 0010 S-1 every canonical name has its own alias row; a run concept
without an ACTIVE one is refused too, rather than its name being recomputed.

**One classification is one transaction** (ADR 0027). Unlike the Phase 5 pipeline,
this class owns its transaction: it refuses to start while the connection has
uncommitted work, so the transaction it opens holds exactly its own writes; it
commits once at the end; and any failure rolls everything back. An edge therefore
never becomes visible without its basis, and the report is built only from what
was committed (Part 4 section 137).

Edges are written through `ConceptService`, so the edge-identity index (D-24), the
shape triggers (D-22) and the origin rules keep applying.
"""

from dataclasses import dataclass

from app.classification.rule_r1 import (
    RULE,
    RULE_VERSION,
    DefinitionScan,
    DefinitionText,
    Mention,
    build_name_set,
    scan_definition,
)
from app.classification.selection import REFUSED_STATUSES
from app.core.errors import InvalidInputError
from app.knowledge.concepts import ConceptService
from app.models.base import utc_now
from app.models.entities import ExtractionRun, Relationship, RelationshipInference
from app.models.enums import ExtractionRunStatus, RelationshipOrigin, RelationType
from app.models.naming import NORMALIZATION_VERSION
from app.storage import queries, read_metadata
from app.storage.repository import Repository

#: How Phase 6 treats each Part 5 section 192 area (ADR 0026, "Per-area status").
#: Stated in every report so the organisation never reads as more than it is.
AREA_STATUS: tuple[tuple[str, str], ...] = (
    ("Related concepts", "INFERRED by rule R1 (INFERRED RELATED_TO, each with a basis)"),
    ("Parent/child relationships", "organised from source-stated edges only (view V1); not inferred"),
    ("Concept grouping", "organised from source-stated edges only (view V1); not inferred"),
    ("Prerequisites", "organised from source-stated edges only (view V2); not inferred"),
    ("Dependencies", "organised from source-stated edges only (view V2); not inferred"),
    ("Applications", "organised from source-stated edges only (view V2); not inferred"),
    ("Cross-references", "NOT REPRESENTED (a recorded limitation, ADR 0026 P6-6)"),
)


@dataclass(frozen=True, slots=True)
class PairOutcome:
    """What happened to one unordered pair R1 found."""

    #: Canonical storage order (decision P6-4b).
    concept_ids: tuple[str, str]
    #: Every distinct basis for the pair, in (occurrence, matched text) order.
    bases: tuple[Mention, ...]
    #: CREATED, BASIS_ADDED, ALREADY_RECORDED or EXPLICIT_EXISTS.
    outcome: str
    #: The edge the bases belong to; None when an EXPLICIT edge made R1 stand down.
    relationship_id: str | None
    #: Basis rows this classification wrote for the pair.
    bases_added: int = 0


@dataclass(frozen=True, slots=True)
class ClassificationReport:
    """What one classification actually committed, and what it deliberately skipped.

    Every number is a count of real rows or real matches (Part 4 section 137).
    """

    run: ExtractionRun
    rule: str
    rule_version: str
    #: The input run's extraction-issue counts, by section 60 type (ADR 0029).
    issue_counts: dict[str, int]
    concepts: int
    names: int
    definitions_read: int
    #: Knowledge ids skipped because the mention's direction would be ambiguous:
    #: more than one DEFINED_BY edge, or more than one occurrence in the run.
    definitions_ambiguous: tuple[str, ...]
    #: Knowledge ids whose defining concept is not a concept of this run.
    definitions_outside_run: tuple[str, ...]
    mentions: int
    own_name_matches: int
    #: Matches of a name several concepts of the run share - skipped, not guessed.
    ambiguous_mentions: int
    #: Matches whose source text could not be proved (I6-B) - skipped, not guessed.
    unmappable_mentions: int
    pairs: tuple[PairOutcome, ...]
    edges_created: int
    basis_rows_added: int
    bases_already_recorded: int
    pairs_skipped_explicit: int

    @property
    def partial(self) -> bool:
        """Whether the input run is PARTIAL - which must be disclosed (P6-3b)."""
        return self.run.status is ExtractionRunStatus.PARTIAL


class Classifier:
    """Rule R1 over one explicitly named extraction run. Holds no state between runs."""

    __slots__ = ("_repository", "_concepts")

    def __init__(self, repository: Repository) -> None:
        self._repository = repository
        self._concepts = ConceptService(repository)

    @property
    def _connection(self):
        return self._repository.connection

    def classify(self, run: ExtractionRun) -> ClassificationReport:
        """Infer, write and commit, as one transaction; or change nothing."""
        self._require_classifiable(run)
        try:
            report = self._classify(run)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return report

    # --------------------------------------------------------------- internals

    def _require_classifiable(self, run: ExtractionRun) -> None:
        # `select_run` already refuses these; checked again because a caller may
        # hold a run object from elsewhere, and the rule is cheap to restate.
        if run.status in REFUSED_STATUSES:
            raise InvalidInputError.of(
                f"Extraction run {run.id} cannot be classified; nothing was classified.",
                f"Run {run.id} is {run.status} and has no committed knowledge (ADR 0029).",
                stage="classification.classify",
                data_changed=False,
                retry_safe=True,
                next_options=("Classify a COMPLETED or PARTIAL run by name.",),
            )
        if self._connection.in_transaction:
            raise InvalidInputError.of(
                "Classification was not started: the connection has uncommitted work.",
                "A classification is exactly one transaction (ADR 0027). Starting it "
                "on top of pending writes would commit or roll back work that is not "
                "its own.",
                stage="classification.classify",
                data_changed=False,
                retry_safe=True,
                next_options=("Commit or roll back the pending work, then classify.",),
            )
        stored = read_metadata(self._connection).get("alias_normalization_version")
        if stored != str(NORMALIZATION_VERSION):
            raise InvalidInputError.of(
                "Classification was not started: the stored concept names use an "
                "alias normalisation this build does not implement.",
                f"The database records alias_normalization_version={stored!r}; rule R1 "
                f"matches stored names under version {NORMALIZATION_VERSION} (decision "
                "D-30, ADR 0010). Matching them under another rule would silently change "
                "what R1 means, so nothing is recomputed and nothing is guessed.",
                stage="classification.classify",
                available=(f"alias_normalization_version: {stored!r}",),
                data_changed=False,
                retry_safe=True,
                next_options=(
                    "Use the RUDRA build that matches this database's normalisation version.",
                    "Run 'python -m app db' to inspect the database metadata.",
                ),
            )

    def _classify(self, run: ExtractionRun) -> ClassificationReport:
        connection = self._connection
        concepts = queries.concepts_of_run(connection, run.id)
        in_run = {concept.id for concept in concepts}
        aliases = queries.active_aliases_of_run(connection, run.id)
        names: dict[str, list[str]] = {concept.id: [] for concept in concepts}
        spelled: dict[str, set[str]] = {concept.id: set() for concept in concepts}
        for alias in aliases:
            names[alias.concept_id].append(alias.normalized_alias)
            spelled[alias.concept_id].add(alias.alias)
        _require_canonical_aliases(concepts, spelled)
        name_set = build_name_set(names)

        texts, ambiguous, outside = self._definition_texts(run, in_run)
        scans = tuple(scan_definition(text, name_set) for text in texts)
        grouped = _group_by_pair(scans)

        outcomes = tuple(self._write_pair(pair, grouped[pair]) for pair in sorted(grouped))
        written = [o for o in outcomes if o.outcome != "EXPLICIT_EXISTS"]

        return ClassificationReport(
            run=run,
            rule=RULE,
            rule_version=RULE_VERSION,
            issue_counts=queries.issue_counts_for_run(connection, run.id),
            concepts=len(concepts),
            names=len(name_set),
            definitions_read=len(texts),
            definitions_ambiguous=tuple(sorted(ambiguous)),
            definitions_outside_run=tuple(sorted(outside)),
            mentions=sum(len(scan.mentions) for scan in scans),
            own_name_matches=sum(scan.own_name_matches for scan in scans),
            ambiguous_mentions=sum(len(scan.ambiguous_matches) for scan in scans),
            unmappable_mentions=sum(len(scan.unmappable_matches) for scan in scans),
            pairs=outcomes,
            edges_created=sum(1 for o in outcomes if o.outcome == "CREATED"),
            basis_rows_added=sum(o.bases_added for o in written),
            bases_already_recorded=sum(len(o.bases) - o.bases_added for o in written),
            pairs_skipped_explicit=len(outcomes) - len(written),
        )

    def _definition_texts(
        self, run: ExtractionRun, in_run: set[str]
    ) -> tuple[list[DefinitionText], set[str], set[str]]:
        """The definitions R1 may read, and the ones it must skip rather than guess."""
        texts: list[DefinitionText] = []
        ambiguous: set[str] = set()
        outside: set[str] = set()
        for definition in queries.definitions_of_run(self._connection, run.id):
            if definition.defined_by_edges != 1 or len(definition.occurrences) != 1:
                ambiguous.add(definition.knowledge_id)
                continue
            if definition.concept_id not in in_run:
                outside.add(definition.knowledge_id)
                continue
            occurrence = definition.occurrences[0]
            texts.append(
                DefinitionText(
                    concept_id=definition.concept_id,
                    knowledge_id=definition.knowledge_id,
                    occurrence_id=occurrence.id,
                    text=occurrence.original_text,
                )
            )
        return texts, ambiguous, outside

    def _write_pair(self, pair: tuple[str, str], bases: tuple[Mention, ...]) -> PairOutcome:
        """ADR 0028's output rule for one pair."""
        existing = queries.active_edges_between(
            self._connection,
            relation_type=RelationType.RELATED_TO,
            concept_a=pair[0],
            concept_b=pair[1],
        )
        if any(edge.origin is RelationshipOrigin.EXPLICIT for edge in existing):
            # A source already states it; R1 writes nothing (ADR 0028).
            return PairOutcome(pair, bases, "EXPLICIT_EXISTS", None)

        edge = _prefer_canonical(existing, pair)
        outcome = "BASIS_ADDED"
        if edge is None:
            edge = self._concepts.attach_relationship(
                relation_type=RelationType.RELATED_TO,
                origin=RelationshipOrigin.INFERRED,
                from_concept_id=pair[0],
                to_concept_id=pair[1],
            )
            outcome = "CREATED"

        added = 0
        for mention in bases:
            if outcome != "CREATED" and queries.inference_is_recorded(
                self._connection,
                relationship_id=edge.id,
                rule=RULE,
                rule_version=RULE_VERSION,
                basis_occurrence_id=mention.basis_occurrence_id,
                matched_text=mention.matched_text,
            ):
                continue
            now = utc_now()
            self._repository.add(
                RelationshipInference(
                    id=self._repository.new_id(RelationshipInference),
                    created_at=now,
                    updated_at=now,
                    relationship_id=edge.id,
                    rule=RULE,
                    rule_version=RULE_VERSION,
                    basis_occurrence_id=mention.basis_occurrence_id,
                    matched_text=mention.matched_text,
                )
            )
            added += 1
        if outcome == "BASIS_ADDED" and added == 0:
            outcome = "ALREADY_RECORDED"
        return PairOutcome(pair, bases, outcome, edge.id, added)


def _require_canonical_aliases(concepts, spelled: dict[str, set[str]]) -> None:
    """Every run concept's canonical name must have its own ACTIVE alias row (S-1).

    That row carries the canonical name's stored normalised form. Without it R1
    would have to recompute the name, which I6-E rules out, or leave out a name
    ADR 0028 requires in the name set; it refuses instead. Phase 5 always writes
    the row (`ConceptService.create_concept`), so this can only follow a manual edit.
    """
    missing = [c.id for c in concepts if c.canonical_name not in spelled[c.id]]
    if missing:
        raise InvalidInputError.of(
            "Classification was not started: a concept's canonical name has no stored "
            "normalised form.",
            f"{len(missing)} concept(s) of this run have no ACTIVE alias row for their "
            "canonical name (ADR 0010 S-1), so the name's stored normalised form is "
            "missing. Rule R1 uses stored names only and does not recompute them (I6-E).",
            stage="classification.classify",
            missing=tuple(missing[:20]),
            data_changed=False,
            retry_safe=True,
            next_options=("Restore the canonical-name alias rows, then classify again.",),
        )


def _group_by_pair(scans: tuple[DefinitionScan, ...]) -> dict[tuple[str, str], tuple[Mention, ...]]:
    """Mentions by unordered pair; one basis per (occurrence, matched source text) (I6-D)."""
    grouped: dict[tuple[str, str], dict[tuple[str, str], Mention]] = {}
    for scan in scans:
        for mention in scan.mentions:
            key = (mention.basis_occurrence_id, mention.matched_text)
            grouped.setdefault(mention.pair, {}).setdefault(key, mention)
    return {
        pair: tuple(bases[key] for key in sorted(bases)) for pair, bases in grouped.items()
    }


def _prefer_canonical(
    existing: tuple[Relationship, ...], pair: tuple[str, str]
) -> Relationship | None:
    """The existing INFERRED edge to add bases to, if there is one.

    D-24 allows one ACTIVE edge per direction, so two reversed INFERRED edges can
    exist only if something other than Phase 6 wrote them. The canonical-direction
    one is preferred so the choice never depends on row order.
    """
    inferred = [edge for edge in existing if edge.origin is RelationshipOrigin.INFERRED]
    for edge in inferred:
        if (edge.from_concept_id, edge.to_concept_id) == pair:
            return edge
    return inferred[0] if inferred else None


__all__ = ["AREA_STATUS", "ClassificationReport", "Classifier", "PairOutcome"]
