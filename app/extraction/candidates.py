"""What a detector produces, before anything is stored (Phase 5, stage 10).

A *candidate* is an observation with a location, not yet a claim. Keeping it
separate from the entities is what lets stage 12 validate and stage 14 attach
provenance as distinct, separately testable steps, which Part 2 section 60's
ordering requires and Part 5 section 187's reasoning ("a step that only exists
inside a larger routine is not separately testable") recommends.

Every candidate carries `char_start` / `char_end` - offsets into the text of the
segment it came from. They are recorded because the detector genuinely knows them,
never recomputed by re-searching the page: the same sentence can appear twice, and
the second match would be a fabricated location, which is Part 1 section 5 applied
to coordinates (decision D-40, ADR 0019).
"""

from dataclasses import dataclass, field

from app.models.enums import RelationType


@dataclass(frozen=True, slots=True)
class Span:
    """Where in a segment something was found."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid span ({self.start}, {self.end})")


@dataclass(frozen=True, slots=True)
class Located:
    """Base facts every candidate shares."""

    page_number: int
    span: Span
    #: The exact text the detector matched, verbatim and unrepaired.
    text: str


@dataclass(frozen=True, slots=True)
class ConceptCandidate(Located):
    """A term the document introduces (Part 5 section 190, category 1)."""

    name: str
    #: The term as the source printed it, which is what Part 2 section 44's
    #: "retain the original terminology" asks for. May differ from `name`.
    surface_form: str


@dataclass(frozen=True, slots=True)
class DefinitionCandidate(Located):
    """A sentence that defines a term (category 2).

    `weak` marks a sentence recognised by a general shape ("X is the ratio of ...")
    rather than an explicit defining verb: it is stored as UNCERTAIN, never as the
    source's definite claim.
    """

    concept_name: str
    statement: str
    weak: bool = False


@dataclass(frozen=True, slots=True)
class EquationCandidate(Located):
    """An equation line as extracted (category 3).

    The expression is kept **exactly as extracted**. Stage 12 decides whether it
    is trustworthy enough to store; nothing anywhere repairs it. Part 1 section 5
    forbids presenting a repaired guess as the source's content, and the supplied
    acceptance document proves the point: `P = dW/dt` arrives as `P = dW` because
    the denominator was laid out on the next line.

    `numeric` marks a substituted value such as `VL = 10 V` from a worked
    solution: a number computed for one problem, not a relation between
    quantities. Those are counted and not stored (ADR 0017, category 3).

    `layout` says whether the equation was rebuilt from a PDF page's glyph layout
    (`app/documents/pdfmath.py`): "certain" when every glyph's place was settled and
    the rebuilt form stands in the page text; "uncertain" when the geometry left a
    doubt - the page text then keeps the flat form, which is the quoted evidence, and
    the rebuilt form is only a tentative reading. None for anything else.
    """

    expression: str
    numeric: bool = False
    layout: str | None = None


@dataclass(frozen=True, slots=True)
class VariableCandidate(Located):
    """A symbol the document introduces, with whatever it says about it (category 4)."""

    symbol: str
    name: str | None = None
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class UnitCandidate(Located):
    """A unit of measurement as printed (category 5).

    Stored as printed. No unit algebra, no dimensional analysis, no conversion:
    every structured unit requirement is assigned to Phase 11 (D-27, ADR 0012).
    """

    unit: str
    quantity: str | None = None


@dataclass(frozen=True, slots=True)
class PropertyCandidate(Located):
    """A stated property of something (category 6).

    `owner` is the concept the sentence states the property *of*, recorded at
    detection (decision P8-24, ADR 0034). It is set only for *"the property of X is
    ..."* sentences, where `subject` is X, the owner. For *"X is the property ..."*
    `subject` is the property itself and no owner is captured, so `owner` stays None
    and no `HAS_PROPERTY` edge can be built from it.
    """

    subject: str
    statement: str
    owner: str | None = None


@dataclass(frozen=True, slots=True)
class RuleCandidate(Located):
    """A stated rule or invariant (category 7, Part 3 section 87).

    `preconditions` and `output` are populated only when the text states them.
    Section 87's structured form belongs to Phase 10, which applies rules.
    """

    name: str
    statement: str
    preconditions: str | None = None
    output: str | None = None


@dataclass(frozen=True, slots=True)
class ExampleCandidate(Located):
    """A worked example (category 8)."""

    label: str
    statement: str


@dataclass(frozen=True, slots=True)
class ProcedureCandidate(Located):
    """An ordered sequence of steps the document states (category 9).

    Always `DOCUMENTED_PROCEDURE` when it comes from a document (Part 3 section
    111). A documented procedure is knowledge, never authorisation to execute
    anything (Part 3 section 109, ADR 0023).
    """

    name: str
    steps: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class PrerequisiteCandidate(Located):
    """A prerequisite the document states (category 10).

    *Stated* only. A prerequisite RUDRA infers from ordering or co-occurrence is
    Phase 6's classification work, not Phase 5's extraction (ADR 0017).
    """

    concept_name: str
    prerequisite_name: str


@dataclass(frozen=True, slots=True)
class RelationshipCandidate(Located):
    """A relation the document states (category 11).

    Phase 5 produces `EXPLICIT` edges only, and every one carries this candidate's
    `text` as its evidence - which is what decision D-28 (ADR 0011) requires at the
    service signature.
    """

    subject: str
    object: str
    relation_type: RelationType


@dataclass(frozen=True, slots=True)
class PageCandidates:
    """Everything one page yielded, by category."""

    page_number: int
    segment_id: str
    concepts: tuple[ConceptCandidate, ...] = ()
    definitions: tuple[DefinitionCandidate, ...] = ()
    equations: tuple[EquationCandidate, ...] = ()
    variables: tuple[VariableCandidate, ...] = ()
    units: tuple[UnitCandidate, ...] = ()
    properties: tuple[PropertyCandidate, ...] = ()
    rules: tuple[RuleCandidate, ...] = ()
    examples: tuple[ExampleCandidate, ...] = ()
    procedures: tuple[ProcedureCandidate, ...] = ()
    prerequisites: tuple[PrerequisiteCandidate, ...] = ()
    relationships: tuple[RelationshipCandidate, ...] = ()

    @property
    def total(self) -> int:
        return sum(
            len(getattr(self, name))
            for name in (
                "concepts", "definitions", "equations", "variables", "units",
                "properties", "rules", "examples", "procedures", "prerequisites",
                "relationships",
            )
        )
