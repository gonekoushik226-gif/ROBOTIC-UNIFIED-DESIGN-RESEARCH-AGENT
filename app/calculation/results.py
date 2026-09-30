"""The typed read models of a Phase 11 calculation (ADR 0042 P11-17 ... P11-24).

Read models, not entities: nothing here is persisted (N5 = (a), ADR 0041 P11-6). Every
collection is a tuple and every model frozen (ADR 0005). Exact values are carried as the
text of the exact rational (`1/3`), never as binary floating point (P11-13).

This module holds:

- the vocabularies: symbol states (section 84, applied to symbols), origins, method
  states (section 90) and answers (sections 89, 228-230);
- the evidence and provenance of the one admitted stored equation, with Phase 9's P9-18
  fields. They are re-applied here because `app.calculation` imports neither
  `app.query` nor `app.reasoning` (ADR 0043 P11-26);
- the returned trace (P11-22): the request as received, the admission, every symbol,
  every method with its formula source, every evaluation step with its substitution and
  intermediate result, the missing symbols, the cycles, the assumptions' effect, the
  conflicts, the verification status and the engine, grammar and unit-table versions.

A calculated value is a calculation result, never a source fact (section 94; P6 §9).
`as_plain` and `to_json` give the one canonical serialisation: equal results give
byte-identical JSON (section 9).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from fractions import Fraction
from pathlib import PurePath

from app.calculation.numbers import display
from app.calculation.requests import CalculationRequest
from app.calculation.units import Quantity
from app.models.entities import (
    Conflict,
    Document,
    DocumentVersion,
    Equation,
    ExtractionRun,
    KnowledgeObject,
    Relationship,
    Source,
)


class SymbolState(StrEnum):
    """Section 84's five states, applied to symbols (ADR 0042, *Symbol states*)."""

    AVAILABLE = "AVAILABLE"
    DERIVED = "DERIVED"
    MISSING = "MISSING"
    BLOCKED = "BLOCKED"
    CONFLICTING = "CONFLICTING"


class Origin(StrEnum):
    """Where a value or a formula comes from (P11-20). The four stay distinct."""

    #: Supplied by the request: an input value, or a formula the request states.
    USER_INPUT = "USER_INPUT"
    #: A value the request assumes (P11-21); every result using it is conditional.
    ASSUMPTION = "ASSUMPTION"
    #: The one stored equation the request admits (N2; P11-19). Formulas only.
    ADMITTED_STORED_ITEM = "ADMITTED_STORED_ITEM"
    #: Calculated by this engine from a complete method. Never a sourced fact.
    DERIVED = "DERIVED"


class MethodState(StrEnum):
    """One formula for one symbol, evaluated on its own (section 90; P11-18)."""

    #: Every input established; the formula evaluated to a value.
    COMPLETE = "COMPLETE"
    #: An input symbol is MISSING: not supplied, and no formula defines it.
    MISSING_INPUT = "MISSING_INPUT"
    #: An input symbol is BLOCKED or CONFLICTING.
    BLOCKED = "BLOCKED"
    #: The formula is on a cycle and would need its own result (P11-17).
    ON_CYCLE = "ON_CYCLE"
    #: An admitted equation outside the grammar: unparseable or an unsupported form.
    NOT_USABLE = "NOT_USABLE"
    #: `+`/`-` between different dimensions: no number is produced (P11-16).
    DIMENSIONALLY_INCONSISTENT = "DIMENSIONALLY_INCONSISTENT"
    #: Division by zero, zero to a negative power, or a value beyond the size guard.
    EVALUATION_ERROR = "EVALUATION_ERROR"
    #: Stored conflict information names the admitted equation (P11-19).
    CONFLICTING_SUPPORT = "CONFLICTING_SUPPORT"


class AnswerStatus(StrEnum):
    """The answer. Every one is a successful answer (section 230), even without a value."""

    #: The target has a value: DERIVED, or AVAILABLE as a direct answer.
    CALCULATED = "CALCULATED"
    #: The target is MISSING or BLOCKED; everything missing is named, nothing invented.
    CANNOT_DETERMINE = "CANNOT_DETERMINE"
    #: The target's complete methods disagree, or disagree with a supplied value.
    CONFLICTING = "CONFLICTING"
    #: The target cannot be determined and the request's admission was withheld under
    #: P9-5: the equation it named has no evidence in the requested scope (exit code 3).
    INSUFFICIENT_AUTHORIZED_INFORMATION = "INSUFFICIENT_AUTHORIZED_INFORMATION"


class RefusalReason(StrEnum):
    """Why the admitted equation was not admitted (P11-19, the P10-27 order)."""

    NO_EVIDENCE = "NO_EVIDENCE"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    EXCLUDED_BY_LIFECYCLE = "EXCLUDED_BY_LIFECYCLE"


#: Every result carries this until Phase 12's verification exists (ADR 0041 P11-7).
VERIFICATION_PENDING = "PENDING"

#: The certainty label of an admitted stored equation (I-C; P11-19).
ADMITTED_FORMULA_LABEL = "UNCERTAIN"


# ------------------------------------------------------------------------ values


@dataclass(frozen=True, slots=True)
class Value:
    """An exact value with its display (P11-13, P11-14) and its coherent SI unit."""

    #: The exact rational, as text: `30`, `1/3`, `-7/2`.
    exact: str
    #: The displayed decimal text, and whether it is exact (`=`) or rounded (`≈`).
    displayed: str
    relation: str
    #: The coherent SI unit, `""` when dimensionless.
    unit: str
    #: Exponents over m, kg, s, A, K, mol, cd.
    dimension: tuple[int, ...]

    @classmethod
    def of(cls, quantity: Quantity) -> "Value":
        shown = display(quantity.value)
        return cls(
            exact=str(Fraction(quantity.value)),
            displayed=shown.text,
            relation=shown.relation,
            unit=quantity.unit,
            dimension=tuple(quantity.dimension.exponents),
        )

    @property
    def text(self) -> str:
        """`30 Ω`, `0.333333 A`, `2`: the displayed value with its unit."""
        return f"{self.displayed} {self.unit}".rstrip()


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
    #: source's stored `availability`.
    preserved_file_present: bool
    #: Declared editions carrying this document's file hash (ADR 0034 P8-27).
    editions: tuple[DocumentVersion, ...] = ()


@dataclass(frozen=True, slots=True)
class Provenance:
    """The stored source, document and run rows behind a set of evidence rows (P9-18)."""

    sources: tuple[Source, ...] = ()
    documents: tuple[DocumentProvenance, ...] = ()
    runs: tuple[ExtractionRun, ...] = ()


@dataclass(frozen=True, slots=True)
class Claim:
    """One side of a stored conflict: in full when usable in scope, otherwise withheld.

    A withheld claim keeps its identifier only; its content, evidence and provenance are
    never shown (P9-5, P9-23; ADR 0039 Amendment 1's clarification).
    """

    knowledge_id: str
    knowledge: KnowledgeObject | None = None
    evidence: tuple[EvidenceRow, ...] = ()
    provenance: Provenance = Provenance()
    superseded_by: str | None = None


@dataclass(frozen=True, slots=True)
class ConflictItem:
    """A stored conflict naming the admitted equation: both claims shown, neither chosen."""

    conflict: Conflict
    claim_a: Claim
    claim_b: Claim
    resolution: str = "not automatically selected"


@dataclass(frozen=True, slots=True)
class ContradictionItem:
    """A stored ACTIVE `CONTRADICTS` relationship in scope touching the admitted equation."""

    relationship: Relationship
    evidence: tuple[EvidenceRow, ...]
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class AdmittedEquation:
    """The one stored equation the request admitted (N2; P11-19), with its provenance.

    Its text is document content: parsed by the whitelist grammar, never executed or
    repaired (ADR 0023). It is labelled `UNCERTAIN` (I-C) whatever else is stored.
    """

    knowledge: KnowledgeObject
    equation: Equation
    #: Its evidence in the requested scope, never a row from a source that is not
    #: authorised or is out of scope (P9-5), and that evidence's provenance (P9-18).
    evidence: tuple[EvidenceRow, ...]
    provenance: Provenance
    #: For a `SUPERSEDED` object, the canonical object its stored `merge` pointer names.
    superseded_by: str | None
    conflicts: tuple[ConflictItem, ...] = ()
    contradictions: tuple[ContradictionItem, ...] = ()
    #: Why its method is not usable (parse failure, unsupported form); None when it parses.
    not_usable: str | None = None
    label: str = ADMITTED_FORMULA_LABEL
    origin: Origin = Origin.ADMITTED_STORED_ITEM

    @property
    def has_conflicting_support(self) -> bool:
        return bool(self.conflicts or self.contradictions)


@dataclass(frozen=True, slots=True)
class RefusedAdmission:
    """The admitted equation, not admitted, and why. Reported; never used.

    A withheld equation's text is never shown: only its identifier (P9-5).
    """

    knowledge_id: str
    reason: RefusalReason
    #: The stored lifecycle of an object excluded by lifecycle; None when withheld.
    lifecycle_status: str | None = None


# ------------------------------------------------------------------- the trace


@dataclass(frozen=True, slots=True)
class SuppliedValue:
    """An input or an assumption, verbatim and parsed (P11-20, P11-21)."""

    symbol: str
    origin: Origin
    #: The value text exactly as the request gave it.
    text: str
    value: Value


@dataclass(frozen=True, slots=True)
class FormulaEntry:
    """One candidate formula, in the fixed order: request formulas, then the admission."""

    #: 1-based position among the candidates.
    number: int
    text: str
    #: The left-hand symbol; None only for an admitted equation that does not parse
    #: and whose target cannot be read.
    target: str | None
    #: `USER_INPUT` for a request formula, `ADMITTED_STORED_ITEM` for the admission.
    origin: Origin
    #: The formula source: "the request" or the stored equation's knowledge object.
    knowledge_id: str | None = None
    #: The symbols its right-hand side uses, in order of first appearance.
    uses: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Need:
    """An input symbol of a method, and its state when the method was judged."""

    symbol: str
    state: SymbolState


@dataclass(frozen=True, slots=True)
class MethodResult:
    """One formula for one symbol, evaluated separately and reported (P11-18)."""

    symbol: str
    formula: int
    text: str
    origin: Origin
    knowledge_id: str | None
    state: MethodState
    #: Why it is not COMPLETE; empty when it is.
    reason: str
    needs: tuple[Need, ...] = ()
    #: For a COMPLETE method: its value and the number of its evaluation step.
    value: Value | None = None
    step: int | None = None
    #: The assumptions this method's value rests on; non-empty means conditional.
    conditional_on: tuple[str, ...] = ()
    #: Whether this method rests on the admitted `UNCERTAIN` equation, here or upstream.
    uncertain_formula: bool = False


@dataclass(frozen=True, slots=True)
class StepInput:
    """A value substituted into a step, with its input source (section 203)."""

    symbol: str
    origin: Origin
    value: Value
    #: For a DERIVED input, the step that calculated it.
    from_step: int | None = None


@dataclass(frozen=True, slots=True)
class Step:
    """One evaluation, in evaluation order (sections 9, 93, 203)."""

    number: int
    symbol: str
    formula: int
    #: The formula as written, the formula with its values substituted, and the result.
    text: str
    substitution: str
    result: Value
    formula_origin: Origin
    knowledge_id: str | None
    inputs: tuple[StepInput, ...]
    #: The dimensional check performed and its outcome (P11-16).
    dimension_check: str
    conditional_on: tuple[str, ...] = ()
    uncertain_formula: bool = False


@dataclass(frozen=True, slots=True)
class SymbolResult:
    """One symbol reached from the target: its state and everything that decided it."""

    symbol: str
    state: SymbolState
    #: AVAILABLE: `USER_INPUT` or `ASSUMPTION`. DERIVED: `DERIVED`. Otherwise empty.
    origins: tuple[Origin, ...] = ()
    #: Its value when AVAILABLE or DERIVED; None otherwise, and when CONFLICTING.
    value: Value | None = None
    methods: tuple[MethodResult, ...] = ()
    #: The formula cycle it is on, when it is on one.
    cycle: tuple[str, ...] = ()
    #: Why a CONFLICTING symbol is so: every disagreeing value, none selected.
    conflicting_values: tuple[Value, ...] = ()
    conditional_on: tuple[str, ...] = ()
    uncertain_formula: bool = False


@dataclass(frozen=True, slots=True)
class MissingSymbol:
    """A MISSING symbol and every formula that requires it (sections 89, 228)."""

    symbol: str
    #: The texts of the formulas needing it; empty when it is the target itself.
    required_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssumptionEffect:
    """An assumption, and every symbol and method that depends on it (P11-21)."""

    symbol: str
    value: Value
    symbols: tuple[str, ...]
    formulas: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Withheld:
    """What was not used, and why, counted (P9-5, P9-23; sections 67, 228)."""

    unauthorized_evidence: int = 0
    out_of_scope_evidence: int = 0
    knowledge: int = 0
    relationships: int = 0
    excluded_by_lifecycle: int = 0


@dataclass(frozen=True, slots=True)
class Versions:
    """The engine, grammar and unit table that produced the result (P6 §11; P11-24)."""

    engine: str
    engine_version: str
    grammar: str
    grammar_version: str
    unit_table: str
    unit_table_version: str
    display_rule: str


@dataclass(frozen=True, slots=True)
class CalculationResult:
    """The whole answer to one request, with its trace. Returned, never stored (N5)."""

    request: CalculationRequest
    status: AnswerStatus
    message: str
    target: str
    #: The target's value when CALCULATED; None otherwise.
    result: Value | None
    inputs: tuple[SuppliedValue, ...]
    assumptions: tuple[SuppliedValue, ...]
    formulas: tuple[FormulaEntry, ...]
    admitted: AdmittedEquation | None
    refused: RefusedAdmission | None
    #: Every symbol reached from the target, in first-reached order.
    symbols: tuple[SymbolResult, ...]
    steps: tuple[Step, ...]
    missing: tuple[MissingSymbol, ...]
    cycles: tuple[tuple[str, ...], ...]
    effects: tuple[AssumptionEffect, ...]
    withheld: Withheld
    verification_status: str
    versions: Versions
    notes: tuple[str, ...]
    #: Whether `knowledge.db` was opened at all: only for an admission (P11-29).
    database_opened: bool
    #: When it was opened: whether the connection refused writes (read from it).
    read_only_connection: bool | None


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
    if isinstance(value, Fraction):
        return str(value)
    return value


def to_json(result: object) -> str:
    """The canonical JSON of a read model: sorted keys, so equal models are byte-identical."""
    return json.dumps(as_plain(result), sort_keys=True, ensure_ascii=False, indent=2)
