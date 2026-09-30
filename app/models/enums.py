"""Enumerated vocabularies taken from the master specification.

Only the vocabularies the Phase 2 entities actually use are defined here. The
remaining ones listed in `docs/ARCHITECTURE.md` section 6.7 arrive with the phases
that need them; defining them now would be a claim that something uses them.

Every value below is spelled exactly as the specification spells it. Values are
stored in the database as TEXT and re-checked there by `CHECK` constraints, so the
vocabulary is enforced in both places.
"""

from enum import StrEnum


class DocumentProcessingStatus(StrEnum):
    """Where a document stands in the ingestion pipeline (Part 2 section 61)."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    PARTIALLY_PROCESSED = "PARTIALLY_PROCESSED"
    FAILED = "FAILED"


class TextOrigin(StrEnum):
    """How a text segment was obtained (Part 2 section 33)."""

    NATIVE_TEXT = "NATIVE_TEXT"
    OCR = "OCR"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class SourceCategory(StrEnum):
    """Where information came from (Part 2 section 49)."""

    USER_PROVIDED_SOURCE = "USER_PROVIDED_SOURCE"
    LOCAL_SOURCE = "LOCAL_SOURCE"
    AUTHORIZED_EXTERNAL_SOURCE = "AUTHORIZED_EXTERNAL_SOURCE"
    SYSTEM_DERIVED = "SYSTEM_DERIVED"
    USER_STATED = "USER_STATED"
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    UNAUTHORIZED_SOURCE = "UNAUTHORIZED_SOURCE"


class Authorization(StrEnum):
    """Whether the user has authorised a source (Part 2 section 50)."""

    AUTHORIZED = "AUTHORIZED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


class SourceAvailability(StrEnum):
    """Whether the physical source can still be reached (Part 7 section 8).

    Independent of the knowledge lifecycle: a deleted file does not mean deleted
    knowledge (Part 7 section 9).
    """

    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    DELETED_BY_USER = "DELETED_BY_USER"
    ARCHIVED = "ARCHIVED"
    CORRUPTED = "CORRUPTED"
    UNREADABLE = "UNREADABLE"
    EXTERNAL_ONLY = "EXTERNAL_ONLY"
    NOT_RETRIEVABLE = "NOT_RETRIEVABLE"


class LifecycleStatus(StrEnum):
    """Status of a stored record (Part 2 section 59, Part 6 section 7, Part 7 section 9).

    Soft deletion uses `DELETED`; nothing is removed from the database without an
    explicit, dependency-checked hard delete.
    """

    ACTIVE = "ACTIVE"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"
    CONFLICTED = "CONFLICTED"
    UNVERIFIED = "UNVERIFIED"
    INVALID = "INVALID"
    ARCHIVED = "ARCHIVED"
    DEPRECATED = "DEPRECATED"
    UNCERTAIN = "UNCERTAIN"
    DELETED = "DELETED"


class KnowledgeType(StrEnum):
    """Kind of knowledge held by a canonical object (Part 2 section 38)."""

    CONCEPT = "CONCEPT"
    DEFINITION = "DEFINITION"
    PROPERTY = "PROPERTY"
    EQUATION = "EQUATION"
    VARIABLE = "VARIABLE"
    UNIT = "UNIT"
    RULE = "RULE"
    CONSTRAINT = "CONSTRAINT"
    PROCEDURE = "PROCEDURE"
    ALGORITHM = "ALGORITHM"
    EXAMPLE = "EXAMPLE"
    COUNTEREXAMPLE = "COUNTEREXAMPLE"
    APPLICATION = "APPLICATION"
    ASSUMPTION = "ASSUMPTION"
    CONDITION = "CONDITION"
    LIMITATION = "LIMITATION"
    OBSERVATION = "OBSERVATION"
    CLAIM = "CLAIM"
    RELATIONSHIP = "RELATIONSHIP"
    DERIVATION = "DERIVATION"


class CertaintyState(StrEnum):
    """How sure RUDRA is, kept separate from provenance (Part 6 sections 12-13)."""

    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    UNCERTAIN = "UNCERTAIN"
    INFERRED = "INFERRED"
    CALCULATED = "CALCULATED"
    ESTIMATED = "ESTIMATED"
    REPORTED_BY_SOURCE = "REPORTED_BY_SOURCE"
    CONFLICTING = "CONFLICTING"
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"


class RelationType(StrEnum):
    """Relationship kinds (Part 2 sections 39-40)."""

    PARENT_OF = "PARENT_OF"
    CHILD_OF = "CHILD_OF"
    PREREQUISITE_OF = "PREREQUISITE_OF"
    DEPENDS_ON = "DEPENDS_ON"
    RELATED_TO = "RELATED_TO"
    PART_OF = "PART_OF"
    COMPOSED_OF = "COMPOSED_OF"
    INSTANCE_OF = "INSTANCE_OF"
    USES = "USES"
    APPLIES_TO = "APPLIES_TO"
    DERIVED_FROM = "DERIVED_FROM"
    DEFINED_BY = "DEFINED_BY"
    CONTRADICTS = "CONTRADICTS"
    EQUIVALENT_TO = "EQUIVALENT_TO"
    SIMILAR_TO = "SIMILAR_TO"
    ALTERNATIVE_TO = "ALTERNATIVE_TO"
    REQUIRES = "REQUIRES"
    PRODUCES = "PRODUCES"
    CONSTRAINS = "CONSTRAINS"
    #: Phase 8 (decision P8-24, ADR 0034): concept -> a PROPERTY knowledge object the
    #: source states as a property of that concept. One direction only; no inverse is
    #: stored (D-23). Section 39 introduces its list as "Examples".
    HAS_PROPERTY = "HAS_PROPERTY"


class RelationshipOrigin(StrEnum):
    """Mandatory distinction of Part 2 section 40.

    An inferred relationship must never be presented as one the source stated.
    """

    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"


class ConflictCause(StrEnum):
    """Why two sources disagree (Part 2 section 46).

    `UNDETERMINED` is the default. The cause is never invented.
    """

    DIFFERENT_ASSUMPTIONS = "DIFFERENT_ASSUMPTIONS"
    DIFFERENT_DEFINITIONS = "DIFFERENT_DEFINITIONS"
    DIFFERENT_CONVENTIONS = "DIFFERENT_CONVENTIONS"
    DIFFERENT_UNITS = "DIFFERENT_UNITS"
    DIFFERENT_OPERATING_CONDITIONS = "DIFFERENT_OPERATING_CONDITIONS"
    DIFFERENT_EDITIONS = "DIFFERENT_EDITIONS"
    DIFFERENT_CONTEXTS = "DIFFERENT_CONTEXTS"
    ACTUAL_CONTRADICTION = "ACTUAL_CONTRADICTION"
    UNDETERMINED = "UNDETERMINED"


class ProcedureDocumentationStatus(StrEnum):
    """Where a procedure came from (Part 3 section 111).

    A guessed workflow must never be presented as documented.
    """

    DOCUMENTED_PROCEDURE = "DOCUMENTED_PROCEDURE"
    INFERRED_PROCEDURE = "INFERRED_PROCEDURE"
    VERIFIED_PROCEDURE = "VERIFIED_PROCEDURE"


class RiskLevel(StrEnum):
    """Risk category of an action (Part 3 section 106)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TaskClass(StrEnum):
    """Classification of a user request (Part 3 section 95)."""

    KNOWLEDGE_QUERY = "KNOWLEDGE_QUERY"
    CALCULATION = "CALCULATION"
    EXPLANATION = "EXPLANATION"
    SOURCE_QUERY = "SOURCE_QUERY"
    COMPARISON = "COMPARISON"
    DOCUMENT_QUERY = "DOCUMENT_QUERY"
    FILE_OPERATION = "FILE_OPERATION"
    APPLICATION_CONTROL = "APPLICATION_CONTROL"
    WEB_SEARCH = "WEB_SEARCH"
    DOCUMENT_CREATION = "DOCUMENT_CREATION"
    IMAGE_REQUEST = "IMAGE_REQUEST"
    PROCEDURE_EXECUTION = "PROCEDURE_EXECUTION"
    SYSTEM_QUERY = "SYSTEM_QUERY"


class VerificationStatus(StrEnum):
    """Outcome of checking that something actually happened (Part 1 section 18).

    `INCONCLUSIVE` exists so that "we could not tell" is never reported as success.
    """

    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    PENDING = "PENDING"


class MemoryCategory(StrEnum):
    """The six separate memory stores (Part 2 section 52)."""

    SHORT_TERM_MEMORY = "SHORT_TERM_MEMORY"
    LONG_TERM_MEMORY = "LONG_TERM_MEMORY"
    KNOWLEDGE_MEMORY = "KNOWLEDGE_MEMORY"
    PROCEDURAL_MEMORY = "PROCEDURAL_MEMORY"
    STATE_MEMORY = "STATE_MEMORY"
    SOURCE_MEMORY = "SOURCE_MEMORY"


class StructureOrigin(StrEnum):
    """Whether a structural element was read or guessed (Part 2 section 35).

    Section 35 requires the distinction explicitly. A heading the document printed
    is not the same claim as one RUDRA inferred from layout, and Part 1 section 5
    forbids presenting the second as though it were the first.
    """

    EXPLICIT_STRUCTURE = "EXPLICIT_STRUCTURE"
    INFERRED_STRUCTURE = "INFERRED_STRUCTURE"


class ExtractionTrigger(StrEnum):
    """Why an extraction run happened (Part 2 section 62, ADR 0018).

    Section 62's six reprocessing triggers, verbatim, plus `FIRST_EXTRACTION` for a
    run that is not a re-run. Section 62 describes when reprocessing *should be
    supported*; it does not name the first pass, so that value is RUDRA's and is
    recorded as an interpretation in ADR 0018.
    """

    FIRST_EXTRACTION = "FIRST_EXTRACTION"
    EXTRACTION_ENGINE_IMPROVED = "EXTRACTION_ENGINE_IMPROVED"
    OCR_IMPROVED = "OCR_IMPROVED"
    KNOWLEDGE_SCHEMA_CHANGED = "KNOWLEDGE_SCHEMA_CHANGED"
    USER_REQUESTED = "USER_REQUESTED"
    DOCUMENT_METADATA_CHANGED = "DOCUMENT_METADATA_CHANGED"
    PROCESSING_FAILED = "PROCESSING_FAILED"


class ExtractionRunStatus(StrEnum):
    """Where one extraction run stands (Part 2 sections 61-62, Part 6 section 19).

    `RUNNING` is written before extraction and advanced only after the caller
    commits, so nothing is presented as extracted before it is. `PARTIAL` is the
    honest answer whenever any page could not be fully processed - section 61's
    obligation applied to the run rather than to the document.
    """

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ExtractionIssueType(StrEnum):
    """Part 2 section 60's nine quality checks, verbatim (ADR 0022).

    All nine are defined so the vocabulary is complete and no future `CHECK`
    widening - which SQLite would make a table rebuild - is ever needed.

    **Phase 5's stage 12 emits seven.** `DUPLICATE_CONCEPT` and
    `POTENTIAL_CONTRADICTION` need the equivalence and conflict machinery ADR 0020
    kept in Phase 8; since Phase 8 they are emitted by stage 15 only (decision
    P8-17, ADR 0033) - one `POTENTIAL_CONTRADICTION` per conflict and one
    `DUPLICATE_CONCEPT` per `POSSIBLE_EQUIVALENT` concept record a run writes.
    Neither discards knowledge, so neither makes a run `PARTIAL`.
    """

    MISSING_SOURCE_REFERENCE = "MISSING_SOURCE_REFERENCE"
    INVALID_EQUATION_STRUCTURE = "INVALID_EQUATION_STRUCTURE"
    UNKNOWN_VARIABLE = "UNKNOWN_VARIABLE"
    UNKNOWN_UNIT = "UNKNOWN_UNIT"
    BROKEN_RELATIONSHIP = "BROKEN_RELATIONSHIP"
    #: Emitted by stage 15 only (Phase 8, P8-17), never by stage 12.
    DUPLICATE_CONCEPT = "DUPLICATE_CONCEPT"
    #: Emitted by stage 15 only (Phase 8, P8-17), never by stage 12.
    POTENTIAL_CONTRADICTION = "POTENTIAL_CONTRADICTION"
    OCR_UNCERTAINTY = "OCR_UNCERTAINTY"
    MALFORMED_METADATA = "MALFORMED_METADATA"


#: The two section 60 checks that only stage 15 emits (ADR 0022; P8-17, ADR 0033).
#: Until Phase 8, `ExtractionIssue.validate()` refused them; it no longer does.
PHASE_8_ONLY_ISSUES: frozenset[ExtractionIssueType] = frozenset(
    {
        ExtractionIssueType.DUPLICATE_CONCEPT,
        ExtractionIssueType.POTENTIAL_CONTRADICTION,
    }
)


class KnowledgeEquivalenceOutcome(StrEnum):
    """Knowledge equivalence (Part 3 sections 72-73), represented in full (ADR 0033).

    Section 72's seven outcomes verbatim, plus section 73's `POSSIBLE_DUPLICATE`.
    **Phase 8 emits three** - `EXACT_DUPLICATE`, `POSSIBLE_DUPLICATE` and
    `CONTRADICTORY`. The other five are never produced: no semantic evaluation
    exists, and `CONTEXT_DEPENDENT` would need context data extraction never stores.
    They are defined because a closed `CHECK` vocabulary can only be widened by
    rebuilding its table (the ADR 0022 precedent).
    """

    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    SEMANTICALLY_EQUIVALENT = "SEMANTICALLY_EQUIVALENT"
    PARTIALLY_OVERLAPPING = "PARTIALLY_OVERLAPPING"
    RELATED_BUT_DISTINCT = "RELATED_BUT_DISTINCT"
    CONTEXT_DEPENDENT = "CONTEXT_DEPENDENT"
    CONTRADICTORY = "CONTRADICTORY"
    UNKNOWN = "UNKNOWN"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"


class ConceptEquivalenceStatus(StrEnum):
    """Concept equivalence statuses (Part 2 section 44), represented in full.

    Phase 8 emits `POSSIBLE_EQUIVALENT` only (P8-16, ADR 0033): confirming or denying
    an equivalence needs an explicit user decision, for which no path exists yet.
    Concepts are never merged.
    """

    CONFIRMED_EQUIVALENT = "CONFIRMED_EQUIVALENT"
    POSSIBLE_EQUIVALENT = "POSSIBLE_EQUIVALENT"
    NOT_EQUIVALENT = "NOT_EQUIVALENT"
    UNKNOWN = "UNKNOWN"


class ConceptEquivalenceBasis(StrEnum):
    """Which evidence produced a concept-equivalence record (P8-16, ADR 0033)."""

    #: The two concepts share an ACTIVE normalised alias, and every occurrence of
    #: both is in one and the same document (re-extraction, section 80).
    SHARED_NAME_SAME_DOCUMENT = "SHARED_NAME_SAME_DOCUMENT"
    #: They share an ACTIVE normalised alias and do not come from one same document.
    SHARED_NAME_OTHER_DOCUMENT = "SHARED_NAME_OTHER_DOCUMENT"
    #: A source states the equivalence: an ACTIVE EXPLICIT `EQUIVALENT_TO` edge.
    STATED_EQUIVALENT_TO = "STATED_EQUIVALENT_TO"


class DocumentKind(StrEnum):
    """Structural elements of a document (Part 2 section 35).

    All 23 values the specification lists, verbatim and in its order. Section 35
    says the system "should attempt to identify" these - an attempt, not a
    guarantee - and insists the detection be adaptive, which is why the element's
    *label* is free text rather than a parsed number.
    """

    TITLE = "TITLE"
    PREFACE = "PREFACE"
    CHAPTER = "CHAPTER"
    SECTION = "SECTION"
    SUBSECTION = "SUBSECTION"
    SUB_SUBSECTION = "SUB_SUBSECTION"
    DEFINITION = "DEFINITION"
    THEOREM = "THEOREM"
    LEMMA = "LEMMA"
    PROOF = "PROOF"
    EXAMPLE = "EXAMPLE"
    EXERCISE = "EXERCISE"
    PROBLEM = "PROBLEM"
    SOLUTION = "SOLUTION"
    EQUATION = "EQUATION"
    TABLE = "TABLE"
    FIGURE = "FIGURE"
    CAPTION = "CAPTION"
    NOTE = "NOTE"
    REMARK = "REMARK"
    PROCEDURE = "PROCEDURE"
    APPENDIX = "APPENDIX"
    REFERENCES = "REFERENCES"
