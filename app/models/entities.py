"""The 22 foundational entities of Phase 2 (master specification Part 5 section 184).

Field depth is deliberately minimal. Part 5 section 184 says: "Do not implement
every advanced field immediately if the underlying architecture has not been
validated. Start with the minimum viable schema while preserving extensibility."
So every entity carries the fields the specification names as required, and
nothing speculative. Later phases add columns through migrations.

Note the absence of dictionary-shaped fields such as `Intent.parameters` or
`Action.parameters`. Their structure belongs to Phase 13 and 14; guessing at it
now would be the placeholder architecture Part 1 section 22 forbids.

Every entity has `id`, `created_at` and `updated_at`, and a `validate()` that
returns `Result[None]` listing every problem at once (ADR 0005).

Annotations here are intentionally *not* postponed with
`from __future__ import annotations`: the storage mapper reads real type objects
from `dataclasses.fields()` to convert rows back into models.
"""

from dataclasses import dataclass
from typing import ClassVar

from app.core.result import Result
from app.models.base import Problems, ok, validation_failure
from app.models.enums import (
    Authorization,
    CertaintyState,
    ConceptEquivalenceBasis,
    ConceptEquivalenceStatus,
    ConflictCause,
    DocumentKind,
    DocumentProcessingStatus,
    ExtractionIssueType,
    ExtractionRunStatus,
    ExtractionTrigger,
    KnowledgeEquivalenceOutcome,
    KnowledgeType,
    LifecycleStatus,
    MemoryCategory,
    ProcedureDocumentationStatus,
    RelationshipOrigin,
    RelationType,
    RiskLevel,
    SourceAvailability,
    SourceCategory,
    StructureOrigin,
    TaskClass,
    TextOrigin,
    VerificationStatus,
)
from app.models.identifiers import EntityKind
from app.models.naming import normalize_alias


def _common(problems: Problems, entity: object) -> None:
    """Checks every entity shares: a well-formed identifier and timestamps."""
    kind = type(entity).KIND  # type: ignore[attr-defined]
    problems.require_id(entity.id, "id", kind)  # type: ignore[attr-defined]
    problems.require_timestamp(entity.created_at, "created_at")  # type: ignore[attr-defined]
    problems.require_timestamp(entity.updated_at, "updated_at")  # type: ignore[attr-defined]


def _result(entity: object, problems: Problems) -> Result[None]:
    if problems:
        return validation_failure(type(entity).__name__, entity.id, problems)  # type: ignore[attr-defined]
    return ok()


def _endpoint(concept_id: str | None, knowledge_id: str | None, side: str) -> str:
    """The one endpoint identifier that is set (decision D-22, ADR 0009).

    Raises rather than returning None or an empty string: an edge with no endpoint,
    or with two, has no answer to "what does this point at?", and inventing one
    would be the silent-wrong-answer behaviour Part 1 section 6 prohibits. A
    Relationship that came from the database always has exactly one, because the
    schema triggers refuse anything else.
    """
    present = [value for value in (concept_id, knowledge_id) if value is not None]
    if len(present) != 1:
        raise ValueError(
            f"relationship has {len(present)} {side} endpoints set, expected exactly 1; "
            f"{side}_concept_id={concept_id!r}, {side}_knowledge_id={knowledge_id!r}"
        )
    return present[0]


def _occurrence_location(problems: Problems, occurrence: object) -> None:
    """The optional where-in-the-document fields shared by the Phase 3 occurrences.

    Part 3 section 75 lists these as optional; each is checked only when present,
    so "unknown" stays unknown rather than becoming a validation failure.
    `SourceOccurrence` deliberately keeps its own copy of these checks: Phase 3
    does not touch it (ADR 0008).
    """
    version_id = occurrence.document_version_id  # type: ignore[attr-defined]
    segment_id = occurrence.segment_id  # type: ignore[attr-defined]
    page_number = occurrence.page_number  # type: ignore[attr-defined]
    if version_id is not None:
        problems.require_id(
            version_id, "document_version_id", EntityKind.DOCUMENT_VERSION
        )
    if segment_id is not None:
        problems.require_id(segment_id, "segment_id", EntityKind.DOCUMENT_SEGMENT)
    if page_number is not None:
        problems.require_non_negative(page_number, "page_number")


def _endpoint_problems(
    problems: Problems, concept_id: str | None, knowledge_id: str | None, side: str
) -> None:
    """Report, rather than raise, on the exactly-one endpoint rule."""
    present = [value for value in (concept_id, knowledge_id) if value is not None]
    problems.require(
        len(present) == 1,
        f"exactly one of {side}_concept_id and {side}_knowledge_id must be set, "
        f"found {len(present)}",
    )
    if concept_id is not None:
        problems.require_id(concept_id, f"{side}_concept_id", EntityKind.CONCEPT)
    if knowledge_id is not None:
        problems.require_id(
            knowledge_id, f"{side}_knowledge_id", EntityKind.KNOWLEDGE_OBJECT
        )


# --------------------------------------------------------------------- documents


@dataclass(frozen=True, slots=True)
class Document:
    """An imported source document (Part 2 section 29).

    The fields below are the ones section 29 lists as the minimum. Optional
    bibliographic metadata stays `None` when it is not known: section 29 is
    explicit that unknown metadata must remain unknown rather than be invented.
    """

    KIND: ClassVar[EntityKind] = EntityKind.DOCUMENT
    TABLE: ClassVar[str] = "document"

    id: str
    created_at: str
    updated_at: str
    filename: str
    original_filename: str
    source_type: str
    file_path: str
    file_hash: str
    file_size: int
    mime_type: str
    ingested_at: str
    processing_status: DocumentProcessingStatus
    processing_version: int
    document_title: str | None = None
    author: str | None = None
    publisher: str | None = None
    edition: str | None = None
    publication_date: str | None = None
    language: str | None = None
    page_count: int | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.filename, "filename")
        p.require_text(self.original_filename, "original_filename")
        p.require_text(self.source_type, "source_type")
        p.require_text(self.file_path, "file_path")
        p.require_text(self.file_hash, "file_hash")
        p.require_text(self.mime_type, "mime_type")
        p.require_non_negative(self.file_size, "file_size")
        p.require_non_negative(self.processing_version, "processing_version")
        p.require_timestamp(self.ingested_at, "ingested_at")
        if self.page_count is not None:
            p.require_non_negative(self.page_count, "page_count")
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    """One version of a document (Part 2 section 48, Part 3 section 81).

    Two editions of the same work are different versions, never the same document.
    """

    KIND: ClassVar[EntityKind] = EntityKind.DOCUMENT_VERSION
    TABLE: ClassVar[str] = "document_version"

    id: str
    created_at: str
    updated_at: str
    document_id: str
    version_label: str
    file_hash: str
    ingested_at: str
    notes: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(self.document_id, "document_id", EntityKind.DOCUMENT)
        p.require_text(self.version_label, "version_label")
        p.require_text(self.file_hash, "file_hash")
        p.require_timestamp(self.ingested_at, "ingested_at")
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class DocumentSegment:
    """A page-bounded piece of extracted text (Part 2 section 32).

    `text_origin` keeps native text and OCR output distinguishable, which
    Part 2 section 33 requires.
    """

    KIND: ClassVar[EntityKind] = EntityKind.DOCUMENT_SEGMENT
    TABLE: ClassVar[str] = "document_segment"

    id: str
    created_at: str
    updated_at: str
    document_id: str
    page_number: int
    ordinal: int
    text: str
    extraction_method: str
    text_origin: TextOrigin
    document_version_id: str | None = None
    confidence: float | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(self.document_id, "document_id", EntityKind.DOCUMENT)
        p.require_non_negative(self.page_number, "page_number")
        p.require_non_negative(self.ordinal, "ordinal")
        p.require(isinstance(self.text, str), "text must be a string")
        p.require_text(self.extraction_method, "extraction_method")
        if self.document_version_id is not None:
            p.require_id(
                self.document_version_id,
                "document_version_id",
                EntityKind.DOCUMENT_VERSION,
            )
        if self.confidence is not None:
            p.require(
                isinstance(self.confidence, (int, float))
                and not isinstance(self.confidence, bool)
                and 0.0 <= float(self.confidence) <= 1.0,
                "confidence must be between 0.0 and 1.0",
            )
        return _result(self, p)


# ----------------------------------------------------------------- provenance


@dataclass(frozen=True, slots=True)
class DocumentStructure:
    """One structural element of a document (Part 2 section 35, ADR 0015).

    Section 35's only requirement in the imperative is that **structure detection
    must be adaptive**: a textbook may number itself `Chapter 5 / 5.1 / 5.2.1`,
    another `Unit III / Module 2 / Topic A`, and a third may have no hierarchy at
    all. Two choices follow directly from that sentence:

    * `label` is **free text**. Parsing it into a numeric path would work for the
      first document and fail for the second.
    * `parent_id` points at this same table, so depth is whatever the document has
      rather than a fixed chapter/section/subsection triple.

    `origin` carries section 35's mandatory distinction: a heading the document
    printed (`EXPLICIT_STRUCTURE`) is a different claim from one RUDRA inferred
    (`INFERRED_STRUCTURE`), and Part 1 section 5 forbids presenting the second as
    the first.

    There is deliberately no confidence score (Part 6 section 12 - confidence must
    not stand in for evidence) and no bounding box (Part 2 section 34 says "where
    practical", and with `pypdf` it is not).
    """

    KIND: ClassVar[EntityKind] = EntityKind.DOCUMENT_STRUCTURE
    TABLE: ClassVar[str] = "document_structure"

    id: str
    created_at: str
    updated_at: str
    document_id: str
    kind: DocumentKind
    ordinal: int
    origin: StructureOrigin
    parent_id: str | None = None
    label: str | None = None
    title: str | None = None
    page_start: int | None = None
    page_end: int | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(self.document_id, "document_id", EntityKind.DOCUMENT)
        p.require_non_negative(self.ordinal, "ordinal")
        if self.parent_id is not None:
            p.require_id(self.parent_id, "parent_id", EntityKind.DOCUMENT_STRUCTURE)
            p.require(
                self.parent_id != self.id,
                "a structure element may not be its own parent",
            )
        if self.page_start is not None:
            p.require_non_negative(self.page_start, "page_start")
        if self.page_end is not None:
            p.require_non_negative(self.page_end, "page_end")
        if isinstance(self.page_start, int) and isinstance(self.page_end, int):
            p.require(
                self.page_start <= self.page_end,
                "page_start must not be after page_end",
            )
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class Source:
    """An authorised information source (Part 2 sections 49-50, Part 7 section 8).

    `availability` describes the *file*; it is deliberately independent of the
    knowledge lifecycle. Deleting a file marks this `DELETED_BY_USER` and touches
    no knowledge at all (Part 7 sections 4 and 9). `file_hash` is preserved after
    deletion and is never recreated (Part 7 section 6).
    """

    KIND: ClassVar[EntityKind] = EntityKind.SOURCE
    TABLE: ClassVar[str] = "source"

    id: str
    created_at: str
    updated_at: str
    name: str
    source_category: SourceCategory
    authorization: Authorization
    availability: SourceAvailability
    document_id: str | None = None
    url: str | None = None
    file_hash: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.name, "name")
        if self.document_id is not None:
            p.require_id(self.document_id, "document_id", EntityKind.DOCUMENT)
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class SourceOccurrence:
    """Evidence: one appearance of a piece of knowledge in a document.

    Fields follow Part 3 section 75. An occurrence is *evidence*, not knowledge:
    many occurrences may point at one canonical `KnowledgeObject`, which is how
    Part 3 sections 69-71 avoid duplicating knowledge while preserving every
    source. `original_text` keeps the source's own wording, so deduplication can
    never erase it.
    """

    KIND: ClassVar[EntityKind] = EntityKind.SOURCE_OCCURRENCE
    TABLE: ClassVar[str] = "source_occurrence"

    id: str
    created_at: str
    updated_at: str
    knowledge_id: str
    source_id: str
    document_id: str
    original_text: str
    extraction_method: str
    extraction_timestamp: str
    document_version_id: str | None = None
    segment_id: str | None = None
    page_number: int | None = None
    section: str | None = None
    #: Part 3 section 75's optional `text_span`, as two offsets into the text of
    #: the segment named by `segment_id` (decision D-40, ADR 0019). Both stay None
    #: unless the extractor genuinely knows them; a span is never recomputed by
    #: re-searching the page, because a repeated sentence would yield a fabricated
    #: location - Part 1 section 5 applied to coordinates.
    char_start: int | None = None
    char_end: int | None = None
    #: Which extraction run produced this evidence (decision D-35, ADR 0018).
    #: On the occurrence, never on the canonical object: Part 3 sections 69-70
    #: forbid asserting that canonical knowledge belongs to one run.
    extraction_run_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(self.knowledge_id, "knowledge_id", EntityKind.KNOWLEDGE_OBJECT)
        p.require_id(self.source_id, "source_id", EntityKind.SOURCE)
        p.require_id(self.document_id, "document_id", EntityKind.DOCUMENT)
        p.require_text(self.original_text, "original_text")
        p.require_text(self.extraction_method, "extraction_method")
        p.require_timestamp(self.extraction_timestamp, "extraction_timestamp")
        if self.document_version_id is not None:
            p.require_id(
                self.document_version_id,
                "document_version_id",
                EntityKind.DOCUMENT_VERSION,
            )
        if self.segment_id is not None:
            p.require_id(self.segment_id, "segment_id", EntityKind.DOCUMENT_SEGMENT)
        if self.page_number is not None:
            p.require_non_negative(self.page_number, "page_number")
        return _result(self, p)


# ------------------------------------------------------------------- knowledge


@dataclass(frozen=True, slots=True)
class KnowledgeObject:
    """One canonical piece of knowledge (Part 2 section 37, Part 3 sections 69-74).

    Minimum viable: identity, type, statement, normalised form and status.
    The richer structure of section 37 - aliases, equations, prerequisites and so
    on - is represented by separate entities and relationships rather than by
    columns here, so each part stays queryable and traceable.

    `normalized_hash` exists for Phase 8 deduplication. It is stored now because
    the canonical/evidence split it supports is structural, but nothing computes
    or compares it yet.
    """

    KIND: ClassVar[EntityKind] = EntityKind.KNOWLEDGE_OBJECT
    TABLE: ClassVar[str] = "knowledge_object"

    id: str
    created_at: str
    updated_at: str
    knowledge_type: KnowledgeType
    canonical_name: str
    statement: str
    lifecycle_status: LifecycleStatus
    certainty: CertaintyState
    knowledge_version: int
    normalized_hash: str | None = None
    confidence: float | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.canonical_name, "canonical_name")
        p.require_text(self.statement, "statement")
        p.require(
            isinstance(self.knowledge_version, int)
            and not isinstance(self.knowledge_version, bool)
            and self.knowledge_version >= 1,
            "knowledge_version must be 1 or more",
        )
        if self.confidence is not None:
            p.require(
                isinstance(self.confidence, (int, float))
                and not isinstance(self.confidence, bool)
                and 0.0 <= float(self.confidence) <= 1.0,
                "confidence must be between 0.0 and 1.0",
            )
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class Concept:
    """A named concept, the navigational face of the knowledge graph (Part 2 section 42).

    `context` is the disambiguator Part 6 section 46 requires: "Gain" in a voltage
    amplifier, a current amplifier and a control system are three concepts, and
    "Do not merge them simply because they share the same word." Free text
    (decision D-25, ADR 0010). A `context_concept_id` successor was expected in
    Phase 6 and was not taken up: one foreign key cannot hold Part 2 section 42's
    multiple contexts (ADR 0026, P6-12).

    `description` is a **non-authoritative navigational gloss** (decision D-31). It
    is never presented as sourced knowledge and is never read by reasoning: a
    description carries no provenance, and Part 1 section 5 forbids presenting
    unsupported information as fact. Definitions are `KnowledgeObject`s.
    """

    KIND: ClassVar[EntityKind] = EntityKind.CONCEPT
    TABLE: ClassVar[str] = "concept"

    id: str
    created_at: str
    updated_at: str
    canonical_name: str
    lifecycle_status: LifecycleStatus
    description: str | None = None
    context: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.canonical_name, "canonical_name")
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class ConceptAlias:
    """One curated name for a concept (Part 2 section 44, decisions Q-1/D-26).

    Section 44 requires that "The system should retain the original terminology".
    This table is the **curated name set RUDRA matches on** - a decision. The term
    a source actually printed is a fact, and lives in
    `ConceptOccurrence.surface_form`. Neither is generated from the other:
    promoting a surface form to an alias automatically would merge concepts on name
    similarity, which Part 3 section 73 forbids outright.

    The concept's own `canonical_name` also gets a row here, so every lookup takes
    one index path.

    `normalized_alias` is unique **within one concept only**. It is deliberately
    not globally unique: Part 6 section 46 requires "Gain" to belong to three
    different concepts at once.
    """

    KIND: ClassVar[EntityKind] = EntityKind.CONCEPT_ALIAS
    TABLE: ClassVar[str] = "concept_alias"

    id: str
    created_at: str
    updated_at: str
    concept_id: str
    alias: str
    normalized_alias: str
    lifecycle_status: LifecycleStatus
    #: The source that motivated recording this name. Nullable so a user-stated
    #: alias can still answer Part 2 section 49's "Where did this come from?"
    #: without a document. Page-level evidence lives in ConceptOccurrence.
    source_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(self.concept_id, "concept_id", EntityKind.CONCEPT)
        p.require_text(self.alias, "alias")
        p.require_text(self.normalized_alias, "normalized_alias")
        if self.source_id is not None:
            p.require_id(self.source_id, "source_id", EntityKind.SOURCE)
        # The database cannot check this - nothing in SQLite can call the
        # normalisation function (ADR 0010) - so the model is the only place it can
        # be caught at all.
        if isinstance(self.alias, str) and isinstance(self.normalized_alias, str):
            try:
                expected = normalize_alias(self.alias)
            except (TypeError, ValueError):
                expected = None
            p.require(
                expected is not None and self.normalized_alias == expected,
                f"normalized_alias must be normalize_alias(alias) = {expected!r}, "
                f"got {self.normalized_alias!r}",
            )
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class ConceptOccurrence:
    """Evidence: one appearance of a concept's name in a document (decision D-19).

    Modelled on Part 3 section 75's field set, with `surface_form` in place of
    `original_text`: the term **as the source printed it**, which is what Part 2
    section 44's "retain the original terminology" asks for.

    `document_id` is NOT NULL (decision D-33), mirroring section 75 and Phase 2's
    `SourceOccurrence`. A user-stated concept therefore has no occurrence path in
    Phase 3; its *name* still has provenance through `ConceptAlias.source_id`.
    """

    KIND: ClassVar[EntityKind] = EntityKind.CONCEPT_OCCURRENCE
    TABLE: ClassVar[str] = "concept_occurrence"

    id: str
    created_at: str
    updated_at: str
    concept_id: str
    source_id: str
    document_id: str
    surface_form: str
    extraction_method: str
    extraction_timestamp: str
    document_version_id: str | None = None
    segment_id: str | None = None
    page_number: int | None = None
    section: str | None = None
    #: Part 3 section 75's optional `text_span`, as two offsets into the text of
    #: the segment named by `segment_id` (decision D-40, ADR 0019). Both stay None
    #: unless the extractor genuinely knows them; a span is never recomputed by
    #: re-searching the page, because a repeated sentence would yield a fabricated
    #: location - Part 1 section 5 applied to coordinates.
    char_start: int | None = None
    char_end: int | None = None
    #: Which extraction run produced this evidence (decision D-35, ADR 0018).
    #: On the occurrence, never on the canonical object: Part 3 sections 69-70
    #: forbid asserting that canonical knowledge belongs to one run.
    extraction_run_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(self.concept_id, "concept_id", EntityKind.CONCEPT)
        p.require_id(self.source_id, "source_id", EntityKind.SOURCE)
        p.require_id(self.document_id, "document_id", EntityKind.DOCUMENT)
        p.require_text(self.surface_form, "surface_form")
        p.require_text(self.extraction_method, "extraction_method")
        p.require_timestamp(self.extraction_timestamp, "extraction_timestamp")
        _occurrence_location(p, self)
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class Relationship:
    """A first-class edge between two entities (Part 2 sections 39-40).

    `origin` is mandatory and carries the distinction Part 2 section 40 requires:
    a relationship RUDRA inferred must never be presented as one a source stated.

    **Endpoints are typed foreign keys** (decision D-22, ADR 0009). Exactly one
    `from_*` column and exactly one `to_*` column is set; the rest are NULL. The
    database enforces existence with a real `REFERENCES … ON DELETE RESTRICT` on
    each column, and enforces the exactly-one and no-self-edge rules with triggers
    rather than table `CHECK`s - a `CHECK` cannot be widened in place, a trigger
    can, which is what makes a later endpoint kind additive.

    `from_id` and `to_id` survive as **read-only properties** so code that only
    reads an endpoint is unaffected by the change. `dataclasses.fields()` does not
    see properties, so they are never persisted.
    """

    KIND: ClassVar[EntityKind] = EntityKind.RELATIONSHIP
    TABLE: ClassVar[str] = "relationship"

    id: str
    created_at: str
    updated_at: str
    relation_type: RelationType
    origin: RelationshipOrigin
    lifecycle_status: LifecycleStatus
    from_concept_id: str | None = None
    from_knowledge_id: str | None = None
    to_concept_id: str | None = None
    to_knowledge_id: str | None = None

    @property
    def from_id(self) -> str:
        """The identifier of the source endpoint, whichever column holds it."""
        return _endpoint(self.from_concept_id, self.from_knowledge_id, "from")

    @property
    def to_id(self) -> str:
        """The identifier of the target endpoint, whichever column holds it."""
        return _endpoint(self.to_concept_id, self.to_knowledge_id, "to")

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        _endpoint_problems(p, self.from_concept_id, self.from_knowledge_id, "from")
        _endpoint_problems(p, self.to_concept_id, self.to_knowledge_id, "to")
        # Compared per column rather than through the properties, because the
        # properties raise on a malformed edge and validate() must report, not raise.
        same_concept = (
            self.from_concept_id is not None
            and self.from_concept_id == self.to_concept_id
        )
        same_knowledge = (
            self.from_knowledge_id is not None
            and self.from_knowledge_id == self.to_knowledge_id
        )
        p.require(
            not (same_concept or same_knowledge),
            "an edge may not point at itself: the from and to endpoints must differ",
        )
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class RelationshipOccurrence:
    """Evidence: one statement of a relationship in a document (decision D-20).

    This is what makes Part 2 section 40 enforceable rather than decorative.
    Marking an edge `EXPLICIT` asserts that a source stated it; without somewhere
    to point, that assertion has nothing behind it, which is Part 1 section 5's
    "UNSUPPORTED INFORMATION MUST NOT BE PRESENTED AS FACT" in storage form.

    `original_text` is the sentence that states the relation.
    """

    KIND: ClassVar[EntityKind] = EntityKind.RELATIONSHIP_OCCURRENCE
    TABLE: ClassVar[str] = "relationship_occurrence"

    id: str
    created_at: str
    updated_at: str
    relationship_id: str
    source_id: str
    document_id: str
    original_text: str
    extraction_method: str
    extraction_timestamp: str
    document_version_id: str | None = None
    segment_id: str | None = None
    page_number: int | None = None
    section: str | None = None
    #: Part 3 section 75's optional `text_span`, as two offsets into the text of
    #: the segment named by `segment_id` (decision D-40, ADR 0019). Both stay None
    #: unless the extractor genuinely knows them; a span is never recomputed by
    #: re-searching the page, because a repeated sentence would yield a fabricated
    #: location - Part 1 section 5 applied to coordinates.
    char_start: int | None = None
    char_end: int | None = None
    #: Which extraction run produced this evidence (decision D-35, ADR 0018).
    #: On the occurrence, never on the canonical object: Part 3 sections 69-70
    #: forbid asserting that canonical knowledge belongs to one run.
    extraction_run_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(self.relationship_id, "relationship_id", EntityKind.RELATIONSHIP)
        p.require_id(self.source_id, "source_id", EntityKind.SOURCE)
        p.require_id(self.document_id, "document_id", EntityKind.DOCUMENT)
        p.require_text(self.original_text, "original_text")
        p.require_text(self.extraction_method, "extraction_method")
        p.require_timestamp(self.extraction_timestamp, "extraction_timestamp")
        _occurrence_location(p, self)
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class Equation:
    """An equation as written, plus room for a canonical parsed form (Part 2 section 36)."""

    KIND: ClassVar[EntityKind] = EntityKind.EQUATION
    TABLE: ClassVar[str] = "equation"

    id: str
    created_at: str
    updated_at: str
    expression: str
    lifecycle_status: LifecycleStatus
    canonical_form: str | None = None
    knowledge_id: str | None = None
    description: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.expression, "expression")
        if self.knowledge_id is not None:
            p.require_id(self.knowledge_id, "knowledge_id", EntityKind.KNOWLEDGE_OBJECT)
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class Variable:
    """A named quantity with an optional unit (Part 2 section 36)."""

    KIND: ClassVar[EntityKind] = EntityKind.VARIABLE
    TABLE: ClassVar[str] = "variable"

    id: str
    created_at: str
    updated_at: str
    symbol: str
    lifecycle_status: LifecycleStatus
    name: str | None = None
    unit: str | None = None
    description: str | None = None
    #: Decision D-37 (ADR 0019). The variable's route to its evidence: Part 3
    #: section 75 keys the occurrence model on `knowledge_id`, so a variable
    #: attached to a VARIABLE knowledge object inherits a complete section 34 and
    #: section 49 provenance path without a table of its own. Mirrors `Equation`.
    knowledge_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.symbol, "symbol")
        if self.knowledge_id is not None:
            p.require_id(self.knowledge_id, "knowledge_id", EntityKind.KNOWLEDGE_OBJECT)
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class Rule:
    """A structurally represented rule (Part 3 section 87).

    Preconditions and outputs are single text fields for now; their structured
    form belongs to Phase 10, where the reasoning engine actually applies them.
    """

    KIND: ClassVar[EntityKind] = EntityKind.RULE
    TABLE: ClassVar[str] = "rule"

    id: str
    created_at: str
    updated_at: str
    name: str
    statement: str
    lifecycle_status: LifecycleStatus
    preconditions: str | None = None
    output: str | None = None
    #: Decision D-37 (ADR 0019). Part 3 section 87 puts `SOURCE: Document X,
    #: Page Y` inside a rule's own conceptual representation; this is how the
    #: rule answers it, through the occurrence on its knowledge object.
    knowledge_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.name, "name")
        p.require_text(self.statement, "statement")
        if self.knowledge_id is not None:
            p.require_id(self.knowledge_id, "knowledge_id", EntityKind.KNOWLEDGE_OBJECT)
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class Procedure:
    """A reusable parameterised procedure (Part 2 section 56, Part 3 section 113).

    `documentation_status` keeps documented and guessed procedures apart, which
    Part 3 section 111 requires. Execution counts are stored from the start
    because Part 3 section 113 makes verification history part of a procedure's
    meaning, but nothing executes procedures yet.
    """

    KIND: ClassVar[EntityKind] = EntityKind.PROCEDURE
    TABLE: ClassVar[str] = "procedure"

    id: str
    created_at: str
    updated_at: str
    name: str
    documentation_status: ProcedureDocumentationStatus
    lifecycle_status: LifecycleStatus
    execution_count: int
    successful_executions: int
    failed_executions: int
    last_verified: str | None = None
    known_application_version: str | None = None
    limitations: str | None = None
    #: Decision D-37 (ADR 0019). Part 3 section 113 lists `source` among the
    #: fields a procedure maintains; this is how it answers, through the
    #: occurrence on its knowledge object.
    knowledge_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.name, "name")
        if self.knowledge_id is not None:
            p.require_id(self.knowledge_id, "knowledge_id", EntityKind.KNOWLEDGE_OBJECT)
        p.require_non_negative(self.execution_count, "execution_count")
        p.require_non_negative(self.successful_executions, "successful_executions")
        p.require_non_negative(self.failed_executions, "failed_executions")
        if (
            isinstance(self.execution_count, int)
            and isinstance(self.successful_executions, int)
            and isinstance(self.failed_executions, int)
        ):
            p.require(
                self.successful_executions + self.failed_executions
                <= self.execution_count,
                "successful_executions + failed_executions cannot exceed execution_count",
            )
        if self.last_verified is not None:
            p.require_timestamp(self.last_verified, "last_verified")
        return _result(self, p)


# ---------------------------------------------------- reasoning and calculation


@dataclass(frozen=True, slots=True)
class Calculation:
    """A record of a real calculation (Part 3 section 93).

    Part 1 section 9 requires calculations to be reproducible and auditable, so
    the record keeps the formula and the result together with a verification
    status. No calculation engine exists yet; this is the shape it will write to.
    """

    KIND: ClassVar[EntityKind] = EntityKind.CALCULATION
    TABLE: ClassVar[str] = "calculation"

    id: str
    created_at: str
    updated_at: str
    formula: str
    verification_status: VerificationStatus
    result_value: str | None = None
    result_unit: str | None = None
    derivation_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.formula, "formula")
        if self.derivation_id is not None:
            p.require_id(self.derivation_id, "derivation_id", EntityKind.DERIVATION)
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class Derivation:
    """How a derived result was obtained (Part 3 section 94, Part 6 sections 10-11).

    Part 6 section 11 is explicit that this holds an auditable derivation, not
    hidden model reasoning.
    """

    KIND: ClassVar[EntityKind] = EntityKind.DERIVATION
    TABLE: ClassVar[str] = "derivation"

    id: str
    created_at: str
    updated_at: str
    method: str
    summary: str
    target_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.method, "method")
        p.require_text(self.summary, "summary")
        if self.target_id is not None:
            p.require_id(self.target_id, "target_id")
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class Conflict:
    """Two authorised claims that disagree (Part 2 sections 46-47).

    Both claims are preserved. `cause` defaults to `UNDETERMINED` because
    section 46 forbids inventing an explanation for a disagreement.
    """

    KIND: ClassVar[EntityKind] = EntityKind.CONFLICT
    TABLE: ClassVar[str] = "conflict"

    id: str
    created_at: str
    updated_at: str
    claim_a_id: str
    claim_b_id: str
    cause: ConflictCause
    lifecycle_status: LifecycleStatus
    context: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(self.claim_a_id, "claim_a_id", EntityKind.KNOWLEDGE_OBJECT)
        p.require_id(self.claim_b_id, "claim_b_id", EntityKind.KNOWLEDGE_OBJECT)
        p.require(self.claim_a_id != self.claim_b_id, "a conflict needs two different claims")
        return _result(self, p)


# ------------------------------------------------------- requests and actions


@dataclass(frozen=True, slots=True)
class Query:
    """A user question, before interpretation (Part 3 section 95)."""

    KIND: ClassVar[EntityKind] = EntityKind.QUERY
    TABLE: ClassVar[str] = "query"

    id: str
    created_at: str
    updated_at: str
    text: str
    task_class: TaskClass | None = None
    source_scope: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.text, "text")
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class Intent:
    """A structured intent (Part 3 section 96).

    Scalar fields only. `entities`, `parameters` and `constraints` are dictionary
    shaped and belong to Phase 13, where the interpreter that fills them is built.
    """

    KIND: ClassVar[EntityKind] = EntityKind.INTENT
    TABLE: ClassVar[str] = "intent"

    id: str
    created_at: str
    updated_at: str
    intent_type: str
    risk_level: RiskLevel
    requires_confirmation: bool
    target: str | None = None
    source_scope: str | None = None
    requested_output: str | None = None
    query_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.intent_type, "intent_type")
        p.require(
            isinstance(self.requires_confirmation, bool),
            "requires_confirmation must be true or false",
        )
        if self.query_id is not None:
            p.require_id(self.query_id, "query_id", EntityKind.QUERY)
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class Action:
    """A reusable parameterised action (Part 3 sections 98-99).

    One implementation per action name, with per-application data held elsewhere.
    Nothing executes actions yet.
    """

    KIND: ClassVar[EntityKind] = EntityKind.ACTION
    TABLE: ClassVar[str] = "action"

    id: str
    created_at: str
    updated_at: str
    name: str
    risk_level: RiskLevel
    description: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.name, "name")
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """A structured plan built from an intent (Part 3 section 103).

    Individual steps become their own entity in Phase 14; `step_count` records
    the shape of the plan without pretending the steps are modelled yet.
    """

    KIND: ClassVar[EntityKind] = EntityKind.EXECUTION_PLAN
    TABLE: ClassVar[str] = "execution_plan"

    id: str
    created_at: str
    updated_at: str
    status: str
    step_count: int
    intent_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.status, "status")
        p.require_non_negative(self.step_count, "step_count")
        if self.intent_id is not None:
            p.require_id(self.intent_id, "intent_id", EntityKind.INTENT)
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class Verification:
    """The result of checking that something actually happened (Part 1 section 18).

    Part 4 section 137 forbids reporting success without this. `INCONCLUSIVE`
    exists so that "could not tell" is never recorded as success.
    """

    KIND: ClassVar[EntityKind] = EntityKind.VERIFICATION
    TABLE: ClassVar[str] = "verification"

    id: str
    created_at: str
    updated_at: str
    subject_id: str
    status: VerificationStatus
    expected: str | None = None
    observed: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(self.subject_id, "subject_id")
        return _result(self, p)


# ---------------------------------------------------------- memory and audit


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """One item in one of the six memory stores (Part 2 sections 52-59).

    `category` keeps the stores separate, which section 52 requires: user
    preferences, extracted knowledge and observed machine state must never be
    conflated. `observed_at` matters for state memory, which is refreshed rather
    than trusted (Part 6 section 6).
    """

    KIND: ClassVar[EntityKind] = EntityKind.MEMORY_ITEM
    TABLE: ClassVar[str] = "memory_item"

    id: str
    created_at: str
    updated_at: str
    category: MemoryCategory
    key: str
    value: str
    lifecycle_status: LifecycleStatus
    observed_at: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.key, "key")
        p.require(isinstance(self.value, str), "value must be a string")
        if self.observed_at is not None:
            p.require_timestamp(self.observed_at, "observed_at")
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One entry in the audit trail (Part 4 sections 131-132, Part 7 section 18).

    Stored in `knowledge.db` so that an audit record commits in the same
    transaction as the change it describes (ADR 0004).

    `document_id` and `knowledge_id` are plain identifiers with **no foreign
    key** on purpose: an audit record must stay readable after its subject is
    gone, and Part 7 sections 18-19 require deletions themselves to be audited.
    A foreign key would make it impossible to record the removal of the very
    thing being referenced.
    """

    KIND: ClassVar[EntityKind] = EntityKind.AUDIT_EVENT
    TABLE: ClassVar[str] = "audit_event"

    id: str
    created_at: str
    updated_at: str
    event_type: str
    occurred_at: str
    actor: str | None = None
    action: str | None = None
    risk_level: RiskLevel | None = None
    authorization: str | None = None
    execution_status: str | None = None
    verification_status: VerificationStatus | None = None
    result: str | None = None
    error: str | None = None
    document_id: str | None = None
    knowledge_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_text(self.event_type, "event_type")
        p.require_timestamp(self.occurred_at, "occurred_at")
        return _result(self, p)


# ------------------------------------------------------ extraction (Phase 5)


@dataclass(frozen=True, slots=True)
class ExtractionRun:
    """One knowledge-extraction pass over one document (Part 2 section 62).

    Section 62 asks for *versioned extraction runs* so extraction is "reproducible
    and debuggable", and lists six triggers that should cause reprocessing. Four
    of them become live in Phase 5, which is why decision U-3's Phase 4 deferral is
    discharged here (ADR 0018).

    A run owns no knowledge. It is referenced by the *occurrences* it produced,
    never by canonical objects: Part 3 sections 69-70 make a canonical object an
    abstraction that may rest on evidence from several runs, so stamping one run
    onto it would become false the moment Phase 8 links a second occurrence.
    """

    KIND: ClassVar[EntityKind] = EntityKind.EXTRACTION_RUN
    TABLE: ClassVar[str] = "extraction_run"

    id: str
    created_at: str
    updated_at: str
    document_id: str
    #: 1 for a document's first extraction, then 2, 3, ... Unique per document,
    #: enforced by the schema.
    run_number: int
    trigger: ExtractionTrigger
    #: The extractor's own version, so a later run differs from an earlier one by
    #: more than its number (Part 2 section 48's `extraction_version`).
    extractor_version: str
    #: What read the file. Recorded because ADR 0014 keeps a parser swap open.
    parser_name: str
    started_at: str
    status: ExtractionRunStatus
    #: None while RUNNING. Set once the work is done and about to be committed.
    completed_at: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(self.document_id, "document_id", EntityKind.DOCUMENT)
        p.require(self.run_number >= 1, "run_number must be 1 or greater")
        p.require_text(self.extractor_version, "extractor_version")
        p.require_text(self.parser_name, "parser_name")
        p.require_timestamp(self.started_at, "started_at")
        if self.completed_at is not None:
            p.require_timestamp(self.completed_at, "completed_at")
        p.require(
            not (
                self.status is ExtractionRunStatus.RUNNING
                and self.completed_at is not None
            ),
            "a RUNNING run cannot have completed_at set",
        )
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class ExtractionIssue:
    """A problem found while extracting, recorded rather than ignored.

    Part 2 section 60 closes its nine checks with *"Problems should be recorded
    rather than silently ignored"*; section 61 requires a partial failure to
    identify pages affected, extraction method, failure reason and potentially
    incomplete knowledge, and adds *"the user should be able to retry processing
    later"*. That last clause is why these are rows rather than a returned report:
    a report dies with the process (ADR 0022).

    Deliberately absent: a `severity` column, because section 60 assigns none and
    inventing a scale would be an unauthorised epistemic claim; and a `resolved`
    flag, because a retry creates a **new** run with its own issues, and mutating
    the old run's record would destroy the reproducibility section 62 asks for.

    All nine section 60 types are accepted. Until Phase 8 the two stage-15 types
    were refused here; stage 15 now emits them (decision P8-17, ADR 0033).
    """

    KIND: ClassVar[EntityKind] = EntityKind.EXTRACTION_ISSUE
    TABLE: ClassVar[str] = "extraction_issue"

    id: str
    created_at: str
    updated_at: str
    extraction_run_id: str
    document_id: str
    issue_type: ExtractionIssueType
    #: What went wrong, in words a person can act on (Part 6 section 38).
    detail: str
    page_number: int | None = None
    segment_id: str | None = None
    #: The offending text, when there is one. Stored verbatim and never repaired -
    #: Part 1 section 5 forbids presenting a guess as the source's content.
    excerpt: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(
            self.extraction_run_id, "extraction_run_id", EntityKind.EXTRACTION_RUN
        )
        p.require_id(self.document_id, "document_id", EntityKind.DOCUMENT)
        p.require_text(self.detail, "detail")
        if self.page_number is not None:
            p.require_non_negative(self.page_number, "page_number")
        if self.segment_id is not None:
            p.require_id(self.segment_id, "segment_id", EntityKind.DOCUMENT_SEGMENT)
        return _result(self, p)


# ---------------------------------------------------------------------- Phase 6


@dataclass(frozen=True, slots=True)
class RelationshipInference:
    """The recorded basis of one inferred edge (decision P6-2a/P6-2b, ADR 0027).

    An `INFERRED` origin says *that* RUDRA inferred an edge; this row says *from
    what*: the rule, its version, and the evidence it read. Part 2 section 49 asks
    every significant item to answer "Where did this come from?", including its
    "Transformation history" and "Relationship origin"; Part 6 section 12 says a
    label is not a substitute for evidence. Phase 6 writes one of these in the
    same transaction as every edge it infers.

    A basis is **not source evidence** (Part 6 section 9): the source defined a
    concept, and RUDRA observed that the definition names another. That is why
    this is its own table and is not part of the `evidence` view.

    `basis_occurrence_id` is the only basis column in Phase 6 and is nullable on
    purpose: the "at least one basis column" rule lives in triggers, not a table
    `CHECK`, so a later basis kind can be added as another nullable column without
    a table rebuild (the ADR 0009 pattern). For rule R1 it is always set.

    `rule` and `rule_version` are free text, as `extraction_method` is (ADR 0019):
    closed vocabularies have already cost this project a table rebuild.

    For rule R1 (ADR 0028), `basis_occurrence_id` is the source occurrence of the
    *mentioning* concept's DEFINITION, which is how the direction of the mention
    survives although the `RELATED_TO` edge itself is stored once per unordered
    pair. `matched_text` is the concept name as it appears in the basis text after
    decision D-30's normalisation, which is the text R1 matched against.
    """

    KIND: ClassVar[EntityKind] = EntityKind.RELATIONSHIP_INFERENCE
    TABLE: ClassVar[str] = "relationship_inference"

    id: str
    created_at: str
    updated_at: str
    relationship_id: str
    #: Which rule inferred the edge - "R1" in Phase 6.
    rule: str
    #: Which version of that rule, so an edge a later version would not make can
    #: still be found. A later version never retracts an earlier one's edges.
    rule_version: str
    basis_occurrence_id: str | None = None
    matched_text: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(self.relationship_id, "relationship_id", EntityKind.RELATIONSHIP)
        p.require_text(self.rule, "rule")
        p.require_text(self.rule_version, "rule_version")
        # The schema trigger holds the same rule; stating it here means the refusal
        # explains itself instead of surfacing as a bare IntegrityError.
        p.require(
            self.basis_occurrence_id is not None,
            "at least one basis must be recorded: basis_occurrence_id is not set",
        )
        if self.basis_occurrence_id is not None:
            p.require_id(
                self.basis_occurrence_id,
                "basis_occurrence_id",
                EntityKind.SOURCE_OCCURRENCE,
            )
        if self.matched_text is not None:
            p.require_text(self.matched_text, "matched_text")
        return _result(self, p)


# ---------------------------------------------------------------------- Phase 8


@dataclass(frozen=True, slots=True)
class KnowledgeEquivalence:
    """One recorded outcome of comparing knowledge (Part 3 sections 72-73, 79).

    Decision P8-22 (ADR 0034). Section 79 makes the comparison's outcome, and the
    review state of an uncertain case, something that must persist; this is where
    it does. One row per comparison:

    * `canonical_knowledge_id` - the existing (canonical) object;
    * the other side, exactly one of:
      `other_knowledge_id` - the new object of a non-exact comparison, or the object
      `merge` superseded (its pointer, ADR 0033 P8-19); or
      `linked_occurrence_id` - the new source occurrence an exact duplicate was
      linked as at extraction time, when no new object exists (P8-12);
    * `extraction_run_id` - the run in which the comparison happened; `None` for a
      record written by `merge`, which is not a run;
    * `outcome`, `rule`, `rule_version`;
    * `conflict_id` - the conflict a `CONTRADICTORY` outcome created (P8-15).

    Deliberately absent: a score (P8-13 - no similarity measure or threshold has any
    authority) and any evidence (it stays in the occurrence tables). `rule` and
    `rule_version` are free text, as they are for `RelationshipInference`.
    """

    KIND: ClassVar[EntityKind] = EntityKind.KNOWLEDGE_EQUIVALENCE
    TABLE: ClassVar[str] = "knowledge_equivalence"

    id: str
    created_at: str
    updated_at: str
    canonical_knowledge_id: str
    outcome: KnowledgeEquivalenceOutcome
    rule: str
    rule_version: str
    other_knowledge_id: str | None = None
    linked_occurrence_id: str | None = None
    extraction_run_id: str | None = None
    conflict_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(
            self.canonical_knowledge_id, "canonical_knowledge_id", EntityKind.KNOWLEDGE_OBJECT
        )
        p.require_text(self.rule, "rule")
        p.require_text(self.rule_version, "rule_version")
        sides = [v for v in (self.other_knowledge_id, self.linked_occurrence_id) if v is not None]
        p.require(
            len(sides) == 1,
            "exactly one of other_knowledge_id and linked_occurrence_id must be set, "
            f"found {len(sides)}",
        )
        if self.other_knowledge_id is not None:
            p.require_id(self.other_knowledge_id, "other_knowledge_id", EntityKind.KNOWLEDGE_OBJECT)
            p.require(
                self.other_knowledge_id != self.canonical_knowledge_id,
                "a knowledge object is not compared with itself",
            )
        if self.linked_occurrence_id is not None:
            p.require_id(
                self.linked_occurrence_id, "linked_occurrence_id", EntityKind.SOURCE_OCCURRENCE
            )
            p.require(
                self.outcome is KnowledgeEquivalenceOutcome.EXACT_DUPLICATE,
                "a linked source occurrence is recorded only for an EXACT_DUPLICATE",
            )
        if self.extraction_run_id is not None:
            p.require_id(self.extraction_run_id, "extraction_run_id", EntityKind.EXTRACTION_RUN)
        contradictory = self.outcome is KnowledgeEquivalenceOutcome.CONTRADICTORY
        p.require(
            contradictory == (self.conflict_id is not None),
            "conflict_id is set exactly when the outcome is CONTRADICTORY",
        )
        if self.conflict_id is not None:
            p.require_id(self.conflict_id, "conflict_id", EntityKind.CONFLICT)
        return _result(self, p)


@dataclass(frozen=True, slots=True)
class ConceptEquivalence:
    """A recorded equivalence status between two concepts (Part 2 section 44).

    Decision P8-16 (ADR 0033), table intent P8-22 (ADR 0034). One record per
    unordered pair, stored in canonical order - `concept_a_id < concept_b_id` by
    plain string comparison (the P6-4b precedent), which gives the order no meaning.
    Concepts are **never merged**: this row says what is known about the pair, and
    section 44 forbids acting on insufficient evidence.

    `basis` names the evidence that produced it; `shared_name` (the ACTIVE
    normalised alias the two share) or `relationship_id` (the source-stated
    `EQUIVALENT_TO` edge) carries that evidence. `extraction_run_id` is the run that
    produced it, or `None` for a record written by `merge`.
    """

    KIND: ClassVar[EntityKind] = EntityKind.CONCEPT_EQUIVALENCE
    TABLE: ClassVar[str] = "concept_equivalence"

    id: str
    created_at: str
    updated_at: str
    concept_a_id: str
    concept_b_id: str
    status: ConceptEquivalenceStatus
    basis: ConceptEquivalenceBasis
    rule: str
    rule_version: str
    shared_name: str | None = None
    relationship_id: str | None = None
    extraction_run_id: str | None = None

    def validate(self) -> Result[None]:
        p = Problems()
        _common(p, self)
        p.require_id(self.concept_a_id, "concept_a_id", EntityKind.CONCEPT)
        p.require_id(self.concept_b_id, "concept_b_id", EntityKind.CONCEPT)
        p.require(
            isinstance(self.concept_a_id, str)
            and isinstance(self.concept_b_id, str)
            and self.concept_a_id < self.concept_b_id,
            "a concept pair is stored in canonical order: concept_a_id < concept_b_id",
        )
        p.require_text(self.rule, "rule")
        p.require_text(self.rule_version, "rule_version")
        if self.basis is ConceptEquivalenceBasis.STATED_EQUIVALENT_TO:
            p.require(
                self.relationship_id is not None and self.shared_name is None,
                "a STATED_EQUIVALENT_TO basis names its edge and no shared name",
            )
        else:
            p.require(
                self.shared_name is not None and self.relationship_id is None,
                "a shared-name basis names the shared name and no edge",
            )
        if self.shared_name is not None:
            p.require_text(self.shared_name, "shared_name")
        if self.relationship_id is not None:
            p.require_id(self.relationship_id, "relationship_id", EntityKind.RELATIONSHIP)
        if self.extraction_run_id is not None:
            p.require_id(self.extraction_run_id, "extraction_run_id", EntityKind.EXTRACTION_RUN)
        return _result(self, p)


#: Every Phase 2 entity, in the order Part 5 section 184 lists them.
ALL_ENTITIES: tuple[type, ...] = (
    Document,
    DocumentVersion,
    DocumentSegment,
    Source,
    SourceOccurrence,
    KnowledgeObject,
    Concept,
    Relationship,
    Equation,
    Variable,
    Rule,
    Procedure,
    Calculation,
    Derivation,
    Conflict,
    Query,
    Intent,
    Action,
    ExecutionPlan,
    Verification,
    MemoryItem,
    AuditEvent,
)

#: Entities introduced by Phase 3 (ADR 0008, ADR 0010).
#:
#: `ALL_ENTITIES` above is **frozen at the twenty-two of Part 5 section 184** and
#: never grows, so that phase's completeness stays literally verifiable by counting
#: it (decision D-29, ADR 0012). Later phases add their own tuple instead.
PHASE_3_ENTITIES: tuple[type, ...] = (
    ConceptAlias,
    ConceptOccurrence,
    RelationshipOccurrence,
)

#: Entities introduced by Phase 4 (ADR 0015).
PHASE_4_ENTITIES: tuple[type, ...] = (DocumentStructure,)

#: Entities introduced by Phase 5 (ADR 0018, ADR 0022).
PHASE_5_ENTITIES: tuple[type, ...] = (ExtractionRun, ExtractionIssue)

#: Entities introduced by Phase 6 (ADR 0027).
PHASE_6_ENTITIES: tuple[type, ...] = (RelationshipInference,)

#: Entities introduced by Phase 8 (ADR 0034, P8-22).
PHASE_8_ENTITIES: tuple[type, ...] = (KnowledgeEquivalence, ConceptEquivalence)

#: Every persisted entity, for the invariants that must hold of all of them.
#: `ConceptView` is deliberately absent: it is a read model, not an entity - no
#: KIND, no TABLE, never persisted (decision D-32).
ENTITIES: tuple[type, ...] = (
    ALL_ENTITIES
    + PHASE_3_ENTITIES
    + PHASE_4_ENTITIES
    + PHASE_5_ENTITIES
    + PHASE_6_ENTITIES
    + PHASE_8_ENTITIES
)
