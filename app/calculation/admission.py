"""The one stored equation a calculation request may admit (N2 = (b); ADR 0042 P11-19).

A request may admit **at most one** stored equation, by its knowledge-object identifier
(`CalculationRequest.check` refuses two before any database is opened). This module
reads that one object and judges it, in the order of ADR 0040 P10-27:

1. **The request is invalid** (`InvalidInputError`, exit code 2) unless the identifier
   names an existing knowledge object of type `EQUATION` with exactly one stored
   `equation` row. Several rows are refused rather than chosen between.
2. **P9-5:** an object without evidence from an authorised source in the requested
   scope is **withheld**: not admitted, counted, reported by identifier only. Its text
   is never read into the result.
3. **P9-23:** an object stored `DELETED` or `ARCHIVED` is **not admitted** and is
   reported. So is one whose `equation` row is stored so: neither row is used. Any
   other status is admitted as stored, `SUPERSEDED` with its stored `merge` pointer.

An admitted equation keeps its evidence in scope and its P9-18 provenance, its
`knowledge_version` (P6 §11) and the label `UNCERTAIN` (I-C). Its text is **document
content**, parsed by the same whitelist grammar as a request formula and **never executed
or repaired** (ADR 0023). A parse failure makes its method *not usable*, with the reason.
Implicit multiplication is judged against every symbol of the calculation, the
equation's own target included (P11-12).

**Conflicting support** (P11-19; the U8a(i) / P10-19 pattern): a stored `conflict`
naming the equation as a claim, unless stored `DELETED` or `ARCHIVED`, or an ACTIVE
`CONTRADICTS` relationship touching it with its own evidence in scope. Either makes its
method CONFLICTING_SUPPORT, never used for a value. Both claims are shown; a claim
without evidence in scope, or stored `DELETED` or `ARCHIVED`, is withheld (identifier
only) and counted.

Everything here reads through `app.storage`; nothing writes.
"""

import sqlite3
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

from app.calculation.formulas import Formula, FormulaError, is_symbol, parse_formula
from app.calculation.requests import CalculationScope, refuse
from app.calculation.results import (
    AdmittedEquation,
    Claim,
    ConflictItem,
    ContradictionItem,
    DocumentProvenance,
    EvidenceRow,
    Provenance,
    RefusalReason,
    RefusedAdmission,
    Withheld,
)
from app.calculation.scope import Verdict, counter, evidence_key, lifecycle_excluded, source_verdict
from app.models.entities import KnowledgeObject, Relationship, Source
from app.models.enums import KnowledgeEquivalenceOutcome, KnowledgeType, LifecycleStatus, RelationType
from app.storage import queries
from app.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class AdmissionOutcome:
    """What reading the admission found: admitted, or refused, with what was withheld."""

    admitted: AdmittedEquation | None
    refused: RefusedAdmission | None
    #: The parsed formula of an admitted equation that is within the grammar.
    formula: Formula | None
    #: The left-hand symbol of an admitted equation, even one that does not parse, when
    #: its text names one; None otherwise.
    target: str | None
    withheld: Withheld


# ------------------------------------------------------------ evidence and provenance


def evidence_of(connection: sqlite3.Connection, subject_id: str) -> tuple[EvidenceRow, ...]:
    """Every stored evidence row of one subject, in evidence order (P9-22)."""
    rows = (EvidenceRow.of(row) for row in queries.evidence_for(connection, subject_id))
    return tuple(sorted(rows, key=evidence_key))


def provenance_of(connection: sqlite3.Connection, rows: Iterable[EvidenceRow]) -> Provenance:
    """The stored source, document, run and edition rows these evidence rows name."""
    rows = tuple(rows)
    if not rows:
        return Provenance()
    stored = queries.evidence_context(
        connection,
        (
            {
                "source_id": row.source_id,
                "document_id": row.document_id,
                "extraction_run_id": row.extraction_run_id,
            }
            for row in rows
        ),
    )
    return Provenance(
        sources=stored.sources,
        documents=tuple(
            DocumentProvenance(
                document=document,
                preserved_file_present=Path(document.file_path).is_file(),
                editions=tuple(v for v in stored.editions if v.file_hash == document.file_hash),
            )
            for document in stored.documents
        ),
        runs=stored.runs,
    )


def superseded_pointer(connection: sqlite3.Connection, knowledge_id: str) -> str | None:
    """The canonical object a superseded item's stored `merge` pointer names, or None.

    `merge` records the pointer as an `EXACT_DUPLICATE` assessment naming the duplicate
    as the other object, with no extraction run (ADR 0033 P8-19). Read as stored.
    """
    records = queries.equivalences_of_knowledge(connection, knowledge_id)
    for record in sorted(records, key=lambda found: counter(found.id)):
        if (
            record.outcome is KnowledgeEquivalenceOutcome.EXACT_DUPLICATE
            and record.other_knowledge_id == knowledge_id
            and record.extraction_run_id is None
        ):
            return record.canonical_knowledge_id
    return None


# ------------------------------------------------------------------- the reading


class _Reader:
    """One admission's reads, verdicts and withheld counts."""

    def __init__(self, repository: Repository, scope: CalculationScope) -> None:
        self.repository = repository
        self.connection = repository.connection
        self.scope = scope
        self._sources: dict[str, Source | None] = {}
        self._verdicts: dict[str, Verdict] = {}
        self.knowledge_withheld: set[str] = set()
        self.relationships_withheld: set[str] = set()
        self.excluded: set[str] = set()

    def verdict(self, row: EvidenceRow) -> Verdict:
        if row.id not in self._verdicts:
            if row.source_id not in self._sources:
                self._sources[row.source_id] = self.repository.get(Source, row.source_id)
            self._verdicts[row.id] = source_verdict(self._sources[row.source_id], self.scope)
        return self._verdicts[row.id]

    def kept(self, subject_id: str) -> tuple[EvidenceRow, ...]:
        return tuple(row for row in evidence_of(self.connection, subject_id)
                     if self.verdict(row) is Verdict.IN_SCOPE)

    def withheld(self) -> Withheld:
        verdicts = list(self._verdicts.values())
        return Withheld(
            unauthorized_evidence=verdicts.count(Verdict.NOT_AUTHORIZED),
            out_of_scope_evidence=verdicts.count(Verdict.OUT_OF_SCOPE),
            knowledge=len(self.knowledge_withheld),
            relationships=len(self.relationships_withheld),
            excluded_by_lifecycle=len(self.excluded),
        )

    def claim(self, knowledge_id: str) -> Claim:
        """One side of a conflict: in full when usable in scope, otherwise withheld."""
        knowledge = self.repository.get(KnowledgeObject, knowledge_id)
        if knowledge is None:
            return Claim(knowledge_id=knowledge_id)
        if lifecycle_excluded(knowledge.lifecycle_status):
            self.excluded.add(knowledge_id)
            return Claim(knowledge_id=knowledge_id)
        evidence = self.kept(knowledge_id)
        if not evidence:
            self.knowledge_withheld.add(knowledge_id)
            return Claim(knowledge_id=knowledge_id)
        pointer = (
            superseded_pointer(self.connection, knowledge_id)
            if knowledge.lifecycle_status is LifecycleStatus.SUPERSEDED
            else None
        )
        return Claim(
            knowledge_id=knowledge_id,
            knowledge=knowledge,
            evidence=evidence,
            provenance=provenance_of(self.connection, evidence),
            superseded_by=pointer,
        )

    def conflicts(self, knowledge_id: str) -> tuple[ConflictItem, ...]:
        items = []
        for conflict in sorted(queries.conflicts_of_knowledge(self.connection, knowledge_id),
                               key=lambda c: counter(c.id)):
            if lifecycle_excluded(conflict.lifecycle_status):
                continue
            items.append(ConflictItem(conflict=conflict, claim_a=self.claim(conflict.claim_a_id),
                                      claim_b=self.claim(conflict.claim_b_id)))
        return tuple(items)

    def contradictions(self, knowledge_id: str) -> tuple[ContradictionItem, ...]:
        items = []
        found: Iterable[Relationship] = queries.relationships_touching_knowledge(
            self.connection, knowledge_id, RelationType.CONTRADICTS
        )
        for relationship in found:
            evidence = self.kept(relationship.id)
            if not evidence:
                self.relationships_withheld.add(relationship.id)
                continue
            items.append(ContradictionItem(relationship=relationship, evidence=evidence,
                                           provenance=provenance_of(self.connection, evidence)))
        return tuple(items)


def target_hint(text: str) -> str | None:
    """The left-hand symbol of `SYMBOL = ...` text, read without parsing the rest."""
    left, separator, _ = text.partition("=")
    return left.strip() if separator and is_symbol(left.strip()) else None


def read_admission(
    repository: Repository,
    identifier: str,
    scope: CalculationScope,
    known_symbols: Collection[str],
) -> AdmissionOutcome:
    """Read and judge the one admitted equation (module docstring). Reads only.

    `known_symbols` are the request's own symbols: its target, inputs, assumptions and
    formula targets. Raises `InvalidInputError` for an invalid admission.
    """
    knowledge = repository.get(KnowledgeObject, identifier)
    if knowledge is None:
        raise refuse(
            f"The admitted item {identifier!r} does not exist.",
            "No knowledge object with that identifier is stored.",
        )
    if knowledge.knowledge_type is not KnowledgeType.EQUATION:
        raise refuse(
            f"The admitted item {identifier!r} is not a stored equation.",
            f"It is a {knowledge.knowledge_type.value} knowledge object; only a knowledge object "
            "of type EQUATION can be admitted to a calculation (ADR 0042 P11-19).",
        )
    rows = queries.equations_of_knowledge(repository.connection, identifier)
    if len(rows) != 1:
        raise refuse(
            f"The admitted item {identifier!r} has {len(rows)} stored equation rows.",
            "An admitted equation must have exactly one stored equation row; RUDRA will not "
            "choose between several, and cannot use none (ADR 0042 P11-19).",
        )
    (equation,) = rows
    reader = _Reader(repository, scope)
    evidence = evidence_of(repository.connection, identifier)
    verdicts = [reader.verdict(row) for row in evidence]
    reason: RefusalReason | None = None
    if not evidence:
        reason = RefusalReason.NO_EVIDENCE
    elif Verdict.IN_SCOPE not in verdicts:
        reason = RefusalReason.OUT_OF_SCOPE if Verdict.OUT_OF_SCOPE in verdicts else RefusalReason.NOT_AUTHORIZED
    if reason is not None:
        reader.knowledge_withheld.add(identifier)
        return AdmissionOutcome(None, RefusedAdmission(identifier, reason), None, None, reader.withheld())
    for status in (knowledge.lifecycle_status, equation.lifecycle_status):
        if lifecycle_excluded(status):
            reader.excluded.add(identifier)
            refused = RefusedAdmission(identifier, RefusalReason.EXCLUDED_BY_LIFECYCLE, status.value)
            return AdmissionOutcome(None, refused, None, None, reader.withheld())

    kept = tuple(row for row, verdict in zip(evidence, verdicts) if verdict is Verdict.IN_SCOPE)
    pointer = (
        superseded_pointer(repository.connection, identifier)
        if knowledge.lifecycle_status is LifecycleStatus.SUPERSEDED
        else None
    )
    formula, not_usable = _parse(equation.expression, known_symbols)
    admitted = AdmittedEquation(
        knowledge=knowledge,
        equation=equation,
        evidence=kept,
        provenance=provenance_of(repository.connection, kept),
        superseded_by=pointer,
        conflicts=reader.conflicts(identifier),
        contradictions=reader.contradictions(identifier),
        not_usable=not_usable,
    )
    target = formula.target if formula is not None else target_hint(equation.expression)
    return AdmissionOutcome(admitted, None, formula, target, reader.withheld())


def _parse(text: str, known_symbols: Collection[str]) -> tuple[Formula | None, str | None]:
    """Parse the stored text twice: for its target, then against every symbol (P11-12)."""
    try:
        target = parse_formula(text).target
        return parse_formula(text, {*known_symbols, target}), None
    except FormulaError as exc:
        return None, f"{exc.problem.value}: {exc}"
