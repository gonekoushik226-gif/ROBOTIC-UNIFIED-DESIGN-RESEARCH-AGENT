"""The read models of Phase 12 provenance (ADR 0044 P12-5 ... P12-8).

Read models, not entities: nothing here is persisted (P12-3). Every collection is a tuple
and every model frozen (ADR 0005). Stored rows appear exactly as stored; a NULL field is
unknown, never filled in.

- `ProvenanceStatus`: whether provenance can be shown. `UNAVAILABLE` is the answer
  *"Provenance unavailable."*, verbatim (section 205); `NOT_FOUND` means no stored item has
  the identifier; `EXCLUDED` means the item is stored `DELETED` or `ARCHIVED` (P9-23).
- `CheckStatus`: the outcome of a verification check (section 18): `VERIFIED`, `FAILED`,
  or `INCONCLUSIVE`, which is never shown as success (section 137).
- `Citation`: one evidence row with its source, run, declared version and the quote check.
- `ItemProvenance`: everything section 49 asks of one stored item.

`as_plain` and `to_json` give the one canonical serialisation: equal results give
byte-identical JSON.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from pathlib import PurePath

from app.models.entities import (
    Document,
    DocumentVersion,
    ExtractionRun,
    RelationshipInference,
    Source,
)

#: Section 205's answer, verbatim, whenever no evidence can be shown (P12-5).
UNAVAILABLE_TEXT = "Provenance unavailable."


class ProvenanceStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    EXCLUDED = "EXCLUDED"
    NOT_FOUND = "NOT_FOUND"


class CheckStatus(StrEnum):
    """A verification outcome (section 18). `INCONCLUSIVE` is never success (section 137)."""

    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


class CheckKind(StrEnum):
    #: The stored segment's text at the recorded span equals the quoted evidence text.
    QUOTE = "QUOTE"
    #: The preserved file is present and its SHA-256 equals the stored hash.
    FILE = "FILE"


@dataclass(frozen=True, slots=True)
class Check:
    """One verification check and its outcome, with what it found."""

    kind: str
    subject_id: str
    status: CheckStatus
    detail: str


def combined(checks: tuple["Check", ...]) -> CheckStatus:
    """FAILED if any failed; INCONCLUSIVE if any was, or there was nothing to check."""
    statuses = {check.status for check in checks}
    if CheckStatus.FAILED in statuses:
        return CheckStatus.FAILED
    if not checks or CheckStatus.INCONCLUSIVE in statuses:
        return CheckStatus.INCONCLUSIVE
    return CheckStatus.VERIFIED


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    """One row of the `evidence` view, exactly as stored (section 49; P9-18)."""

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
class Citation:
    """One piece of evidence in scope, with everything section 49 names about it."""

    evidence: EvidenceRow
    source: Source
    #: The extraction run the evidence came from, and its extractor version; None when the
    #: row names no run (manual or pre-Phase-5 evidence).
    run: ExtractionRun | None
    #: The `document_version` row this evidence names, when it names one.
    document_version: DocumentVersion | None
    quote: Check


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """A document behind shown evidence: its identity, editions and the file check."""

    document: Document
    #: Whether the preserved copy exists now: a fact separate from stored availability.
    preserved_file_present: bool
    #: Declared editions carrying this document's file hash (ADR 0034 P8-27).
    editions: tuple[DocumentVersion, ...]
    file: Check


@dataclass(frozen=True, slots=True)
class Basis:
    """One recorded basis of an INFERRED relationship (ADR 0027): the rule and the
    occurrence it rests on. A basis whose occurrence is out of scope is withheld."""

    inference: RelationshipInference
    citation: Citation | None


@dataclass(frozen=True, slots=True)
class History:
    """Section 49's transformation history, as stored."""

    lifecycle_status: str | None
    knowledge_version: int | None = None
    certainty: str | None = None
    #: Stage-15 and `merge` assessments naming the object (ADR 0033), or the concept
    #: equivalence records naming the concept (P8-16): identifiers and outcomes only.
    assessments: tuple[tuple[str, str, str], ...] = ()
    #: For a `SUPERSEDED` object, the canonical object its stored merge pointer names.
    superseded_by: str | None = None


@dataclass(frozen=True, slots=True)
class Withheld:
    """What was not shown, and why, counted (P9-5, P9-23)."""

    unauthorized_evidence: int = 0
    out_of_scope_evidence: int = 0
    bases: int = 0
    claims: int = 0


@dataclass(frozen=True, slots=True)
class ItemProvenance:
    """Where a stored item came from (sections 49, 204-205), with its checks.

    `item` is the stored row only when provenance is AVAILABLE: an item whose evidence is
    all withheld, or that has none, is identified but its content is not shown (P9-5).
    """

    identifier: str
    kind: str
    status: ProvenanceStatus
    message: str
    scope: str
    item: object | None = None
    #: The knowledge object, concept or relationship whose evidence is cited.
    subject_id: str | None = None
    citations: tuple[Citation, ...] = ()
    documents: tuple[DocumentRecord, ...] = ()
    #: For identity-level items (a document, source, run, segment or declared edition):
    #: the sources in scope and the document's extraction runs.
    sources: tuple[Source, ...] = ()
    runs: tuple[ExtractionRun, ...] = ()
    #: For a relationship: EXPLICIT or INFERRED, and an INFERRED edge's bases.
    origin: str | None = None
    bases: tuple[Basis, ...] = ()
    history: History | None = None
    #: For a conflict: each claim's provenance.
    claims: tuple["ItemProvenance", ...] = ()
    checks: tuple[Check, ...] = ()
    verification: CheckStatus = CheckStatus.INCONCLUSIVE
    withheld: Withheld = Withheld()
    notes: tuple[str, ...] = ()

    @property
    def pages(self) -> tuple[str, ...]:
        """`DOC-… p.N` for every cited page, in citation order, each once."""
        seen: dict[str, None] = {}
        for citation in self.citations:
            row = citation.evidence
            page = "no page recorded" if row.page_number is None else f"p.{row.page_number}"
            seen.setdefault(f"{row.document_id} {page}", None)
        return tuple(seen)


# -------------------------------------------------------------- serialisation


def as_plain(value: object) -> object:
    """Plain JSON values for any read model: dataclasses to dicts, enums to values."""
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
