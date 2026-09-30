"""The read models of answer verification (ADR 0044 P12-9 ... P12-11).

`Exposure` is section 204's list for one answer: source, page, relevant knowledge,
derivation, calculation, assumptions and verification status. `AnswerVerification` is
the whole report: the verification outcome, every check with what it found, and the
exposure. Nothing here is persisted (P12-3).
"""

from dataclasses import dataclass

from app.provenance import Check, CheckStatus, ItemProvenance, ProvenanceStatus


@dataclass(frozen=True, slots=True)
class Exposure:
    """Section 204's seven exposures for one answer."""

    #: AVAILABLE when stored evidence backs the answer; UNAVAILABLE ("Provenance
    #: unavailable.") when it used no stored source, or none can be shown.
    source_status: ProvenanceStatus
    source_message: str
    #: The provenance of every stored item the answer used, re-read from the database.
    sources: tuple[ItemProvenance, ...]
    pages: tuple[str, ...]
    relevant_knowledge: tuple[str, ...]
    derivation: tuple[str, ...]
    calculation: tuple[str, ...]
    assumptions: tuple[str, ...]
    verification_status: CheckStatus


@dataclass(frozen=True, slots=True)
class AnswerVerification:
    """The verification of one recorded answer. Returned, never stored."""

    #: CALCULATION or REASONING.
    kind: str
    status: CheckStatus
    message: str
    #: The recorded answer's own status (for example CALCULATED), as recorded.
    answer_status: str
    scope: str
    checks: tuple[Check, ...]
    exposure: Exposure
    #: Whether knowledge.db was opened: only when re-running the answer needs it.
    database_opened: bool
    notes: tuple[str, ...]
