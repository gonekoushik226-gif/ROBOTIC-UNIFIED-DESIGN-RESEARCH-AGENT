"""The typed read models of Phase 10 reasoning (ADR 0039 P10-11, P10-21, P10-23).

Read models, not entities: nothing here is persisted (U5(a), ADR 0039 P10-21). Every
collection is a tuple and every model frozen (ADR 0005).

This module holds the node-state and origin vocabularies, the evidence and provenance
of a stored item (Phase 9's P9-18 fields, re-applied here because `app.reasoning` does
not import `app.query`, ADR 0040 P10-26), the request's **initial availability** -
what the request itself makes available, before any reasoning - and the engine's
result: each node's state and why, the derivation steps, the paths, the methods, the
conflicts, the missing dependencies and what was withheld (ADR 0039 P10-11 ... P10-24).
A derived result names the rule and the stored relationships it rests on; it is a
reasoning result, never a source fact (section 94; P6 section 9).

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
    Conflict,
    Document,
    DocumentVersion,
    ExtractionRun,
    KnowledgeObject,
    Relationship,
    Source,
)
from app.reasoning.requests import ReasoningRequest


class NodeState(StrEnum):
    """Section 84's five node states (ADR 0039 P10-11)."""

    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    DERIVED = "DERIVED"
    BLOCKED = "BLOCKED"
    CONFLICTING = "CONFLICTING"


class Origin(StrEnum):
    """Where a node's availability comes from (ADR 0039 P10-23). Always distinct.

    An admitted stored item is never labelled `USER_INPUT`, and a `USER_INPUT` never
    carries stored provenance.
    """

    #: The request supplies the node as given; its value, if any, verbatim.
    USER_INPUT = "USER_INPUT"
    #: The request admits one stored knowledge object for the node (OI-1 = Option B).
    ADMITTED_STORED_ITEM = "ADMITTED_STORED_ITEM"
    #: The request assumes the node available (U7(b)); a result using it is conditional.
    ASSUMPTION = "ASSUMPTION"
    #: Phase 10 reasoning derived it (ADR 0039 P10-12). Never an initial availability.
    DERIVED = "DERIVED"


class RefusalReason(StrEnum):
    """Why an admitted item was not admitted (ADR 0040 P10-27). Always reported."""

    #: The item has no stored evidence at all (P9-5).
    NO_EVIDENCE = "NO_EVIDENCE"
    #: None of its evidence comes from an authorised source (P9-5; sections 49-50).
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    #: It has authorised evidence, none of it in the requested scope (P9-5).
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    #: The item is stored `DELETED` or `ARCHIVED` (P9-23).
    EXCLUDED_BY_LIFECYCLE = "EXCLUDED_BY_LIFECYCLE"


# ------------------------------------------------------------------- provenance


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    """One row of the `evidence` view, exactly as stored (section 49; P9-18).

    A NULL field is unknown, never filled in.
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
    #: Whether the preserved copy exists on disk now: a separate fact from any
    #: source's stored `availability`, never folded into a derived label.
    preserved_file_present: bool
    #: Declared editions: the `document_version` rows carrying this document's file
    #: hash (ADR 0034 P8-27). Empty means no edition is declared.
    editions: tuple[DocumentVersion, ...] = ()


@dataclass(frozen=True, slots=True)
class Provenance:
    """The stored source, document and run rows behind a set of evidence rows (P9-18)."""

    sources: tuple[Source, ...] = ()
    documents: tuple[DocumentProvenance, ...] = ()
    runs: tuple[ExtractionRun, ...] = ()


# ---------------------------------------------------------- initial availability


@dataclass(frozen=True, slots=True)
class SuppliedInput:
    """A node the request supplies as `USER_INPUT`. No stored provenance, by design."""

    #: How the request named the node: a concept identifier or an exact name.
    reference: str
    concept: Concept
    #: Carried verbatim; never parsed or evaluated (ADR 0038 P10-6).
    value: str | None

    @property
    def origin(self) -> Origin:
        return Origin.USER_INPUT


@dataclass(frozen=True, slots=True)
class AdmittedItem:
    """A stored knowledge object the request admitted for a node (OI-1 = Option B)."""

    reference: str
    concept: Concept
    knowledge: KnowledgeObject
    #: The item's evidence in the requested scope - never a row from a source that
    #: is not authorised or out of scope (P9-5) - and its provenance (P9-18).
    evidence: tuple[EvidenceRow, ...]
    provenance: Provenance
    #: For a `SUPERSEDED` item, the canonical object its stored `merge` pointer names
    #: (ADR 0033 P8-19); None otherwise, or when no pointer is stored.
    superseded_by: str | None

    @property
    def origin(self) -> Origin:
        return Origin.ADMITTED_STORED_ITEM


@dataclass(frozen=True, slots=True)
class RefusedAdmission:
    """An admission that was not admitted, and why. Reported; never used."""

    reference: str
    concept: Concept
    knowledge: KnowledgeObject
    reason: RefusalReason


@dataclass(frozen=True, slots=True)
class StatedAssumption:
    """A node the request assumes available (U7(b)). Labelled; never stored."""

    reference: str
    concept: Concept
    statement: str | None

    @property
    def origin(self) -> Origin:
        return Origin.ASSUMPTION


@dataclass(frozen=True, slots=True)
class Availability:
    """One node the request makes available, with where that comes from."""

    concept_id: str
    origin: Origin
    #: The admitted knowledge object, for `ADMITTED_STORED_ITEM`; otherwise None.
    knowledge_id: str | None = None


@dataclass(frozen=True, slots=True)
class InitialAvailability:
    """What the request itself makes available, before any reasoning (OI-1 = Option B).

    Stored knowledge appears here only as an item the request admitted by
    identifier; nothing is available merely because it is stored. Each tuple is in
    numeric identifier order, whatever order the request gave (ADR 0039 P10-24).
    """

    inputs: tuple[SuppliedInput, ...] = ()
    admitted: tuple[AdmittedItem, ...] = ()
    refused: tuple[RefusedAdmission, ...] = ()
    assumptions: tuple[StatedAssumption, ...] = ()
    #: Every node made available and by what, one entry per origin and item.
    available: tuple[Availability, ...] = ()

    def is_available(self, concept_id: str) -> bool:
        return any(entry.concept_id == concept_id for entry in self.available)

    def origins_of(self, concept_id: str) -> tuple[Origin, ...]:
        return tuple(entry.origin for entry in self.available if entry.concept_id == concept_id)

    @property
    def withheld(self) -> int:
        """Admissions withheld by authorisation or scope (P9-5), counted."""
        return sum(
            1 for refusal in self.refused if refusal.reason is not RefusalReason.EXCLUDED_BY_LIFECYCLE
        )


# ------------------------------------------------------------------ the engine


class AnswerStatus(StrEnum):
    """The answer to a request. Every one of them is a successful answer (section 230)."""

    #: TARGET: at least one method's target is AVAILABLE or DERIVED; none is chosen.
    #: FORWARD: at least one node was derived.
    DETERMINED = "DETERMINED"
    #: Sections 89 and 228: what is missing, blocked or conflicting is named.
    CANNOT_DETERMINE = "CANNOT_DETERMINE"
    #: TARGET: no concept answers to the target (exit code 3 at the command line).
    NOT_FOUND = "NOT_FOUND"
    #: TARGET: concepts answer to it, but none has evidence in the requested scope (P9-5).
    INSUFFICIENT_AUTHORIZED_INFORMATION = "INSUFFICIENT_AUTHORIZED_INFORMATION"


class BlockReason(StrEnum):
    """Why a node with a stored requirement set is BLOCKED (ADR 0039 P10-11, P10-20)."""

    #: A requirement is MISSING: not made available, and nothing it could be derived from.
    MISSING_REQUIREMENT = "MISSING_REQUIREMENT"
    #: A requirement is itself BLOCKED.
    BLOCKED_REQUIREMENT = "BLOCKED_REQUIREMENT"
    #: A requirement is CONFLICTING (U8a(i)).
    CONFLICTING_REQUIREMENT = "CONFLICTING_REQUIREMENT"
    #: A requirement's relationship or concept has no evidence in the requested scope
    #: (P9-5): the node is never derived from the rest of its set. Counted; not shown.
    WITHHELD_REQUIREMENT = "WITHHELD_REQUIREMENT"
    #: A requirement's concept is DELETED or ARCHIVED (P9-23). Counted; not shown.
    EXCLUDED_REQUIREMENT = "EXCLUDED_REQUIREMENT"
    #: The node is on a stored dependency cycle and the request does not make it
    #: available: a cycle is reported, never looped (ADR 0039 P10-20).
    ON_CYCLE = "ON_CYCLE"


@dataclass(frozen=True, slots=True)
class Block:
    """One reason a node is BLOCKED. `concept_id` names the requirement concerned,
    or is None for a withheld or excluded requirement and for a cycle."""

    reason: BlockReason
    concept_id: str | None = None
    relationship_id: str | None = None


@dataclass(frozen=True, slots=True)
class RequirementLink:
    """One stored requirement of a node, usable in the requested scope, as stored.

    `X REQUIRES A` and `X DEPENDS_ON A` both mean X requires A (ADR 0038 P10-3). Its
    evidence in scope and provenance travel with it (P10-23).
    """

    relationship: Relationship
    required_id: str
    evidence: tuple[EvidenceRow, ...]
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class Claim:
    """One side of a stored conflict (P9-15).

    Shown with its evidence in scope; or, when it has none, or is stored `DELETED` or
    `ARCHIVED`, **withheld** - its identifier kept, its content, evidence and
    provenance never shown (P9-5, P9-23), as Phase 9 withholds a claim.
    """

    knowledge_id: str
    #: None when the claim is withheld.
    knowledge: KnowledgeObject | None = None
    evidence: tuple[EvidenceRow, ...] = ()
    provenance: Provenance = Provenance()
    #: For a `SUPERSEDED` claim, the canonical object its `merge` pointer names.
    superseded_by: str | None = None


@dataclass(frozen=True, slots=True)
class ConflictItem:
    """A stored conflict attached to a node: both claims shown, neither chosen (sections 46, 229)."""

    conflict: Conflict
    claim_a: Claim
    claim_b: Claim
    resolution: str = "not automatically selected"


@dataclass(frozen=True, slots=True)
class ContradictionItem:
    """A stored ACTIVE `CONTRADICTS` relationship touching a node or its admitted item."""

    relationship: Relationship
    evidence: tuple[EvidenceRow, ...]
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class NodeResult:
    """One node of the dependency graph: its state and everything that decided it."""

    concept: Concept
    state: NodeState
    #: AVAILABLE: every origin the request gave it, in Origin order. DERIVED: DERIVED.
    #: Otherwise empty.
    origins: tuple[Origin, ...] = ()
    #: The stored knowledge objects the request admitted for it (OI-1 = Option B).
    admitted: tuple[str, ...] = ()
    #: Its stored requirements usable in scope, by numeric counter. Empty for a leaf.
    requirements: tuple[RequirementLink, ...] = ()
    #: Stored requirements that could not be used, counted and never shown (P9-5, P9-23).
    withheld_requirements: int = 0
    excluded_requirements: int = 0
    #: Why it is BLOCKED; empty in every other state.
    blocks: tuple[Block, ...] = ()
    #: The stored dependency cycle it is on, by numeric counter; empty when none.
    cycle: tuple[str, ...] = ()
    conflicts: tuple[ConflictItem, ...] = ()
    contradictions: tuple[ContradictionItem, ...] = ()
    #: The assumptions an AVAILABLE or DERIVED node rests on; non-empty means conditional.
    conditional_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DerivationStep:
    """One application of the requirement-set rule (ADR 0039 P10-12).

    Formed by RUDRA at reasoning time from source-stated relationships; never a rule
    a source stated, and never a source fact (section 94).
    """

    number: int
    rule: str
    rule_version: str
    concept_id: str
    #: The complete stored requirement set it applied to, and the relationships it read.
    requirements: tuple[str, ...]
    relationships: tuple[str, ...]
    conditional_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PathNode:
    concept_id: str
    name: str
    state: NodeState


@dataclass(frozen=True, slots=True)
class ReasoningPath:
    """A chain of stored requirements from an AVAILABLE node to the target, inputs first."""

    nodes: tuple[PathNode, ...]


@dataclass(frozen=True, slots=True)
class RequiredBy:
    concept_id: str
    relationship_id: str


@dataclass(frozen=True, slots=True)
class MissingDependency:
    """A MISSING node and every stored requirement that needs it (sections 89, 228)."""

    concept: Concept
    required_by: tuple[RequiredBy, ...]


@dataclass(frozen=True, slots=True)
class Method:
    """One target concept and its requirement set, evaluated on its own (ADR 0039 P10-17).

    Several methods for one requested target are all kept and reported; none is
    selected or ranked.
    """

    target: Concept
    state: NodeState
    #: Every node the target's stored requirements reach in scope, by numeric counter.
    nodes: tuple[NodeResult, ...]
    steps: tuple[DerivationStep, ...]
    paths: tuple[ReasoningPath, ...]
    #: False when the path listing reached its limit; the rest of the result is whole.
    paths_complete: bool
    missing: tuple[MissingDependency, ...]
    cycles: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class ForwardResult:
    """Everything that follows from what the request made available (ADR 0039 P10-14)."""

    #: The request-available nodes reasoning started from, in scope.
    seeds: tuple[str, ...]
    #: The seeds, every node that depends on them in scope, and those nodes' requirements.
    nodes: tuple[NodeResult, ...]
    steps: tuple[DerivationStep, ...]
    derived: tuple[str, ...]
    missing: tuple[MissingDependency, ...]
    cycles: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class Withheld:
    """What was not used, and why, counted (P9-5, P9-23; sections 67, 228)."""

    #: Evidence rows from a source that is NOT_AUTHORIZED or UNAUTHORIZED_SOURCE.
    unauthorized_evidence: int = 0
    #: Authorised evidence rows outside the requested scope.
    out_of_scope_evidence: int = 0
    #: Concepts, relationships and knowledge objects with no evidence in scope.
    concepts: int = 0
    relationships: int = 0
    knowledge: int = 0
    #: Concepts and knowledge objects stored DELETED or ARCHIVED.
    excluded_by_lifecycle: int = 0


@dataclass(frozen=True, slots=True)
class ReasoningResult:
    """The whole answer to one request, with its trace. Returned, never stored (U5(a))."""

    request: ReasoningRequest
    status: AnswerStatus
    message: str
    #: What the request itself made available (OI-1 = Option B).
    initial: InitialAvailability
    #: TARGET mode: one method per concept answering to the target.
    methods: tuple[Method, ...]
    #: FORWARD mode only.
    forward: ForwardResult | None
    withheld: Withheld
    notes: tuple[str, ...]
    rule: str
    rule_version: str
    #: Read from the connection: whether it refused writes.
    read_only_connection: bool


# -------------------------------------------------------------- serialisation


def as_plain(value: object) -> object:
    """Plain JSON values for any read model: dataclasses to dicts, enums to values.

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
    """The canonical JSON of a read model: sorted keys, so equal models are byte-identical."""
    return json.dumps(as_plain(result), sort_keys=True, ensure_ascii=False, indent=2)
