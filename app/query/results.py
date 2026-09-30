"""The typed result and trace read models of the query engine (ADR 0036, ADR 0037 P9-32).

Read models, not entities: nothing here is persisted (P9-19). Every collection is a
tuple and every model frozen, as everywhere in `app.models` (ADR 0005).

**What a result never contains:** a relevance score (P9-22; section 78), a derived
source-accessibility label (P9-20), an item attached to a concept without a stored
edge (D1, P9-3; P9-13's "no Mentions group"), evidence from a source that is not
authorised (P9-5), or anything merged at query time (P9-16).

`as_plain` and `to_json` give the one canonical serialisation: equal results give
byte-identical JSON.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from pathlib import PurePath

from app.models.entities import (
    Concept,
    ConceptAlias,
    ConceptEquivalence,
    Conflict,
    Document,
    DocumentSegment,
    DocumentVersion,
    ExtractionRun,
    KnowledgeEquivalence,
    KnowledgeObject,
    Relationship,
    RelationshipInference,
    Source,
)
from app.models.enums import RelationshipOrigin, RelationType
from app.query.requests import QueryRequest


class AnswerStatus(StrEnum):
    """What kind of answer a result is (sections 228-230; P9-24)."""

    #: Something stored answers the request, and is shown.
    FOUND = "FOUND"
    #: Nothing stored answers it. An answer, not a failure (section 230).
    NOT_FOUND = "NOT_FOUND"
    #: Something is stored, but nothing from an authorised source in scope
    #: remains: *"Insufficient authorized information"* (sections 67, 228).
    INSUFFICIENT_AUTHORIZED_INFORMATION = "INSUFFICIENT_AUTHORIZED_INFORMATION"


class EdgeEnd(StrEnum):
    """Which end of a stored edge a concept is (direction comes from storage, P9-13)."""

    FROM = "FROM"
    TO = "TO"


# ------------------------------------------------------------------- evidence


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    """One row of the `evidence` view, exactly as stored (section 49; P9-18).

    A NULL field is unknown, never filled in: extraction records no `section` and no
    `document_version_id` (`app/extraction/pipeline.py:434`).
    """

    id: str
    subject_kind: str
    subject_id: str
    source_id: str
    document_id: str
    evidence_text: str
    extraction_method: str
    extraction_timestamp: str
    document_version_id: str | None
    segment_id: str | None
    page_number: int | None
    section: str | None
    char_start: int | None
    char_end: int | None
    extraction_run_id: str | None

    @classmethod
    def of(cls, row: Mapping[str, object]) -> "EvidenceRow":
        return cls(**{f.name: row[f.name] for f in fields(cls)})  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class DocumentProvenance:
    """A document behind cited evidence (P9-18, P9-20)."""

    document: Document
    #: Whether the preserved copy exists on disk now - a separate fact from any
    #: source's stored `availability`, and never folded into a derived label.
    preserved_file_present: bool
    #: Declared editions: the `document_version` rows carrying this document's file
    #: hash (ADR 0034 P8-27). Empty means no edition is declared.
    editions: tuple[DocumentVersion, ...] = ()


@dataclass(frozen=True, slots=True)
class Provenance:
    """The source, document and run rows behind every evidence row one section cites.

    Each row once, in numeric identifier order, exactly as stored: a source's
    name, category, authorisation and availability; a run's number, status
    (`PARTIAL` disclosed) and extractor version (P9-6).
    """

    sources: tuple[Source, ...] = ()
    documents: tuple[DocumentProvenance, ...] = ()
    runs: tuple[ExtractionRun, ...] = ()


# ---------------------------------------------------------------------- edges


@dataclass(frozen=True, slots=True)
class EdgeBasis:
    """One recorded basis of an INFERRED edge (ADR 0027), read far enough to follow.

    A basis is not source evidence (P6 section 9): it records that RUDRA read a
    definition and found another concept named in it.
    """

    inference: RelationshipInference
    #: The source occurrence the basis names, as an evidence row in scope.
    occurrence: EvidenceRow | None
    #: The concepts whose definition that occurrence is: the direction of the mention.
    mentioning_concept_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LinkedEdge:
    """A stored edge with what establishes it (section 40; P9-14)."""

    relationship: Relationship
    #: How the connection was established, in words; an INFERRED edge is never
    #: presented as stated.
    label: str
    #: Where a source states it: the edge's relationship occurrences in scope.
    evidence: tuple[EvidenceRow, ...] = ()
    #: Its recorded bases (INFERRED, or promoted from INFERRED).
    bases: tuple[EdgeBasis, ...] = ()

    @property
    def origin(self) -> RelationshipOrigin:
        return self.relationship.origin


@dataclass(frozen=True, slots=True)
class Link:
    """One resolved concept, and the stored edge through which it reached an item."""

    concept_id: str
    concept_end: EdgeEnd
    edge: LinkedEdge


# ---------------------------------------------------------------------- items


@dataclass(frozen=True, slots=True)
class KnowledgeItem:
    """A knowledge object reached through a stored edge (P9-13), listed once.

    `links` names every resolved concept and edge that reached it. Its lifecycle is
    shown as stored: a `SUPERSEDED` object keeps its place, labelled, with the
    stored pointer to its canonical object (P9-16).
    """

    knowledge: KnowledgeObject
    links: tuple[Link, ...]
    #: The object's own evidence in scope.
    evidence: tuple[EvidenceRow, ...]
    #: Its equation, variable, rule and procedure rows (D-37), as stored.
    companions: tuple[object, ...] = ()
    #: Stored assessment records naming it (P9-17).
    assessments: tuple[KnowledgeEquivalence, ...] = ()
    #: The canonical object, from the stored `merge` pointer, when it is superseded.
    superseded_by: str | None = None
    #: Objects `merge` superseded into it.
    superseded_into: tuple[str, ...] = ()
    #: Source occurrences in scope of it and of the objects superseded into it.
    source_occurrences: int = 0
    #: Distinct sources behind those occurrences, as `review` counts them.
    #: Informational only: more sources is not more correct (section 78).
    number_of_sources: int = 0


@dataclass(frozen=True, slots=True)
class EdgeItem:
    """A stored edge between a resolved concept and a concept (P9-14).

    The other concept is named, never expanded: its own knowledge is not pulled in.
    """

    links: tuple[Link, ...]
    #: Ends of the edge that are not resolved concepts.
    neighbours: tuple[Concept, ...] = ()

    @property
    def edge(self) -> LinkedEdge:
        return self.links[0].edge


@dataclass(frozen=True, slots=True)
class ResultGroup:
    """One group of a concept result, in the fixed group order (P9-13, P9-22)."""

    name: str
    items: tuple[KnowledgeItem | EdgeItem, ...] = ()
    #: Stated when the group is empty, and always for the D1 groups: whether nothing
    #: is stored for the concept, or the kind has no stored concept link at all.
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Claim:
    """One side of a stored conflict (P9-15)."""

    knowledge_id: str
    #: None when this claim has no evidence in scope: it is withheld, never shown.
    knowledge: KnowledgeObject | None
    evidence: tuple[EvidenceRow, ...] = ()


@dataclass(frozen=True, slots=True)
class ConflictItem:
    """A stored conflict, both claims shown, neither chosen (sections 46, 229)."""

    conflict: Conflict
    claim_a: Claim
    claim_b: Claim
    resolution: str = "not automatically selected"


# ------------------------------------------------------------------- concepts


@dataclass(frozen=True, slots=True)
class ResolvedConcept:
    """A concept the request resolved, with its own evidence in scope."""

    concept: Concept
    #: The ACTIVE alias whose D-30 form equals the requested name; None when the
    #: concept was named by identifier.
    matched_alias: ConceptAlias | None
    occurrences: tuple[EvidenceRow, ...] = ()
    #: `PARENT_OF` closures over stored edges in scope (D-23; section 42): named,
    #: never expanded.
    ancestors: tuple[Concept, ...] = ()
    descendants: tuple[Concept, ...] = ()
    #: Stored concept-equivalence records naming it, every status (P9-17).
    equivalence_records: tuple[ConceptEquivalence, ...] = ()


@dataclass(frozen=True, slots=True)
class ConceptSection:
    """Resolved concepts and their knowledge, as one attributed list per group."""

    concepts: tuple[ResolvedConcept, ...]
    groups: tuple[ResultGroup, ...]
    conflicts: tuple[ConflictItem, ...] = ()
    provenance: Provenance = Provenance()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PossibleEquivalent:
    """A concept widened to by stored equivalence data (D2, ADR 0035 P9-4).

    Labelled possible, never treated as the same concept, never merged into the
    target's groups, never counted among its sources, never widened again.
    """

    concept: Concept
    #: The resolved concepts it is recorded or stated equivalent to.
    of_concept_ids: tuple[str, ...]
    #: `POSSIBLE_EQUIVALENT` records joining the two, with their basis.
    records: tuple[ConceptEquivalence, ...] = ()
    #: ACTIVE `EXPLICIT` `EQUIVALENT_TO` edges joining the two, with evidence.
    stated_edges: tuple[LinkedEdge, ...] = ()
    label: str = (
        "POSSIBLE equivalent - not established as the same concept; its knowledge is "
        "shown separately and is not counted among the resolved concepts' sources"
    )
    #: True when it answers to the requested name itself, so its knowledge is
    #: already shown above and `section` is None.
    already_resolved: bool = False
    #: Its own knowledge, in its own groups.
    section: ConceptSection | None = None


# ------------------------------------------------------------ exact and pages


@dataclass(frozen=True, slots=True)
class ExactResult:
    """One stored item named by identifier (P9-24). A concept goes to `ConceptSection`."""

    identifier: str
    kind: str
    knowledge: KnowledgeItem | None = None
    relationship: LinkedEdge | None = None
    #: The endpoints of an exact relationship: concepts or knowledge objects.
    endpoints: tuple[Concept | KnowledgeObject, ...] = ()
    document: DocumentProvenance | None = None
    #: A document's extraction runs, every one, each with its status (P9-6).
    runs: tuple[ExtractionRun, ...] = ()
    #: A document's sources in scope.
    sources: tuple[Source, ...] = ()
    conflicts: tuple[ConflictItem, ...] = ()
    provenance: Provenance = Provenance()


@dataclass(frozen=True, slots=True)
class PageOccurrence:
    """One occurrence located on a page, with the stored subject it is evidence for."""

    evidence: EvidenceRow
    subject: KnowledgeObject | Concept | Relationship | None


@dataclass(frozen=True, slots=True)
class PageResult:
    """A page's stored text and the occurrences located on it (P9-21)."""

    document: DocumentProvenance
    page_number: int
    #: The stored segments - the original PDF is never opened.
    segments: tuple[DocumentSegment, ...]
    #: In text order.
    occurrences: tuple[PageOccurrence, ...] = ()
    provenance: Provenance = Provenance()


# --------------------------------------------------------- derived index (keyword)


class IndexState(StrEnum):
    """What the derived index is, as read back (ADR 0037 P9-27, P9-29)."""

    #: Its build marker equals the state marker of `knowledge.db`: usable.
    FRESH = "FRESH"
    #: No index file exists.
    MISSING = "MISSING"
    #: Its build marker differs from the current state of `knowledge.db`.
    STALE = "STALE"
    #: Built by another index format, tokenizer or normalisation: never read.
    INCOMPATIBLE = "INCOMPATIBLE"
    #: No build marker, or its tables disagree with its own counts.
    INCOMPLETE = "INCOMPLETE"
    #: Not readable as a RUDRA index.
    UNREADABLE = "UNREADABLE"


@dataclass(frozen=True, slots=True)
class IndexStatus:
    """The derived index as verified against `knowledge.db`. Only FRESH is ever searched."""

    path: str
    state: IndexState
    reason: str
    format_version: int | None = None
    tokenizer: str | None = None
    normalization_version: int | None = None
    #: Entries per kind, and pages, as the index's own metadata records them.
    entries: tuple[tuple[str, int], ...] = ()
    pages: int | None = None
    #: The build marker stored in the index, and the state marker computed now.
    build_marker: str | None = None
    state_marker: str | None = None


@dataclass(frozen=True, slots=True)
class IndexBuildReport:
    """What one explicit build wrote (P9-28): read back before the index was put in place."""

    path: str
    status: IndexStatus
    #: Stored rows whose text normalises to nothing, so there is nothing to index.
    skipped_empty: int
    #: The section 158 estimate, the space available, and the size written (section 156).
    estimate_bytes: int
    available_bytes: int
    size_bytes: int
    seconds: float


@dataclass(frozen=True, slots=True)
class DocumentIngestion:
    """Whether one document is fully ingested (ADR 0032 P8-6; ADR 0035 P9-10).

    Reported, never stored. All three hold: parse success (the stored processing
    status is not PENDING, PROCESSING or FAILED; P8-5 fixes it as parse and extraction
    health, never completion), an extraction run COMPLETED at extractor version 4 or
    later, and a fresh derived index - which covers every stored row, so every
    document. Each condition is stated separately.
    """

    document_id: str
    filename: str
    processing_status: str
    parsed: bool
    #: COMPLETED runs at extractor version 4 or later, by run number.
    qualifying_runs: tuple[str, ...]
    indexed: bool
    fully_ingested: bool
    reason: str


KEYWORD_LABEL = (
    "KEYWORD HIT - stored text matches the term; this is not a stored link to any "
    "concept and is not presented as knowledge about one (ADR 0035 P9-3)"
)


@dataclass(frozen=True, slots=True)
class KeywordHit:
    """A knowledge object whose stored text matches, re-read from `knowledge.db` (P9-25)."""

    knowledge: KnowledgeObject
    #: Whether the term matched the object's statement.
    statement_matched: bool
    #: The source occurrences in scope whose text matched.
    matched_occurrence_ids: tuple[str, ...]
    #: The object's evidence in scope.
    evidence: tuple[EvidenceRow, ...]
    label: str = KEYWORD_LABEL


@dataclass(frozen=True, slots=True)
class ConceptNameHit:
    """A concept one of whose ACTIVE aliases matches. A name match resolves nothing."""

    concept: Concept
    aliases: tuple[ConceptAlias, ...]
    #: The concept's own evidence in scope.
    occurrences: tuple[EvidenceRow, ...]
    label: str = KEYWORD_LABEL


@dataclass(frozen=True, slots=True)
class PageHit:
    """A stored page segment whose text matches (P9-25: "reported as page hits")."""

    segment: DocumentSegment
    document: DocumentProvenance


@dataclass(frozen=True, slots=True)
class KeywordResult:
    """Keyword mode's hits, each re-read from `knowledge.db` and put through the scope."""

    term: str
    normalized_term: str
    prefix: bool
    #: The FTS5 expression actually searched: auditable, never raw user input.
    expression: str
    index: IndexStatus
    knowledge: tuple[KeywordHit, ...] = ()
    concepts: tuple[ConceptNameHit, ...] = ()
    pages: tuple[PageHit, ...] = ()
    provenance: Provenance = Provenance()


# ---------------------------------------------------------------------- trace


@dataclass(frozen=True, slots=True)
class Withheld:
    """What was not returned, and why, counted (P9-5; sections 67, 228).

    Evidence counts are distinct evidence rows; item counts are distinct concepts,
    knowledge objects and edges.
    """

    #: Rows whose source is NOT_AUTHORIZED or UNAUTHORIZED_SOURCE: never returned.
    unauthorized_evidence: int = 0
    #: Authorised rows outside the requested scope.
    out_of_scope_evidence: int = 0
    #: Rows in scope removed by the request's evidence filters.
    filtered_evidence: int = 0
    #: Items with stored evidence, none of it authorised and in scope.
    items_without_authorized_evidence: int = 0
    #: Items with no stored evidence at all.
    items_without_evidence: int = 0
    #: Items excluded by the request's filters or the lifecycle default.
    items_filtered_out: int = 0


@dataclass(frozen=True, slots=True)
class DatabaseState:
    """The database a result was read from, as read from the connection."""

    path: str
    schema_version: int
    read_only: bool


@dataclass(frozen=True, slots=True)
class TraceConcept:
    """A resolved concept, and the alias that matched it (None for an identifier)."""

    concept_id: str
    alias_id: str | None = None
    alias: str | None = None
    normalized_alias: str | None = None


@dataclass(frozen=True, slots=True)
class TraceLink:
    """One step of an item's link path (P9-19)."""

    item_id: str
    concept_id: str
    relationship_id: str
    relation_type: RelationType
    origin: RelationshipOrigin
    concept_end: EdgeEnd


@dataclass(frozen=True, slots=True)
class AnswerTrace:
    """How a result was produced (section 66; ADR 0036 P9-19).

    Returned with the result and **never persisted**: no audit or explanation layer
    exists yet, and persisting would make every query a write.
    """

    request: QueryRequest
    database: DatabaseState
    #: What the derived index contributed: nothing, except in keyword mode, which
    #: reads it only when verified fresh (ADR 0037 P9-29).
    index: str
    resolved: tuple[TraceConcept, ...] = ()
    links: tuple[TraceLink, ...] = ()
    #: Every evidence row the result cites, by id.
    evidence_ids: tuple[str, ...] = ()
    #: Concepts reached by one widening step (D2).
    widened: tuple[str, ...] = ()
    withheld: Withheld = Withheld()
    persisted: bool = False


@dataclass(frozen=True, slots=True)
class QueryResult:
    """The answer to one request. Exactly one of the mode parts is filled."""

    request: QueryRequest
    status: AnswerStatus
    #: The answer in words, stating absence and insufficiency plainly.
    message: str
    trace: AnswerTrace
    concept: ConceptSection | None = None
    possible_equivalents: tuple[PossibleEquivalent, ...] = ()
    #: Stated when widening found nothing stored (P9-17).
    equivalence_note: str | None = None
    exact: ExactResult | None = None
    page: PageResult | None = None
    keyword: KeywordResult | None = None
    withheld: Withheld = Withheld()
    notes: tuple[str, ...] = ()


# -------------------------------------------------------------- serialisation


def as_plain(value: object) -> object:
    """Plain JSON values for any result: dataclasses to dicts, enums to their values.

    Field names are kept as they are, so an entity serialises to its column names.
    """
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: as_plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (tuple, list)):
        return [as_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): as_plain(item) for key, item in value.items()}
    if isinstance(value, PurePath):
        return str(value)
    return value


def to_json(result: object) -> str:
    """The canonical JSON of a result: sorted keys, so equal results are byte-identical."""
    return json.dumps(as_plain(result), sort_keys=True, ensure_ascii=False, indent=2)
