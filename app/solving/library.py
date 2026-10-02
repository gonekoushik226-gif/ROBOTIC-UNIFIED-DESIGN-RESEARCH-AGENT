"""The equations and named quantities the documents state, ready to calculate with.

`load` reads what the knowledge base holds - every ACTIVE equation with evidence from an
authorized source in scope, and every symbol a document gave a meaning - and turns each
stored equation into the formulas it states (`app.solving.normalize`). Three things are
kept apart and counted, never silently dropped:

* **usable** - read into one or more formulas;
* **unreadable** - stored, but not a plain formula (an integral, a derivative, a flattened
  fraction, a sentence); each with its reason;
* **withheld** - no evidence from an authorized source in scope, or a stored conflict names
  it: not used, exactly as `calculate --admit` would not use it.

Reads only.
"""

from dataclasses import dataclass, field

from app.calculation.formulas import Symbol, is_symbol, symbols_of
from app.calculation.requests import CalculationScope
from app.calculation.results import EvidenceRow
from app.calculation.scope import Verdict, lifecycle_excluded, source_verdict
from app.models.entities import Source
from app.models.enums import CertaintyState, RelationType
from app.solving import normalize
from app.solving.normalize import Reading
from app.storage import queries
from app.storage.repository import Repository

#: Certainty states that mean the document's own markup or a settled layout gave the text.
SOURCE_STATED = frozenset({CertaintyState.REPORTED_BY_SOURCE, CertaintyState.KNOWN, CertaintyState.VERIFIED})


@dataclass(frozen=True, slots=True)
class StoredEquation:
    """One stored equation: what the page printed, where, and what it states as formulas."""

    knowledge_id: str
    stored_text: str
    certainty: CertaintyState
    evidence: tuple[EvidenceRow, ...]
    readings: tuple[Reading, ...] = ()
    #: Why no formula could be read from it; None when at least one was.
    unreadable: str | None = None

    @property
    def stated_by_source(self) -> bool:
        return self.certainty in SOURCE_STATED


@dataclass(frozen=True, slots=True)
class StoredVariable:
    """A symbol a document gave a meaning: "where V is the voltage"."""

    knowledge_id: str
    symbol: str
    name: str | None
    unit: str | None
    statement: str
    evidence: tuple[EvidenceRow, ...]


@dataclass(frozen=True, slots=True)
class Withheld:
    """Equations not offered to a calculation, by reason."""

    no_authorized_evidence: int = 0
    stored_conflict: int = 0


@dataclass(frozen=True, slots=True)
class Library:
    equations: tuple[StoredEquation, ...]
    variables: tuple[StoredVariable, ...]
    withheld: Withheld
    #: Every quantity named by a usable formula or a stored variable.
    symbols: frozenset[str] = field(default_factory=frozenset)

    @property
    def usable(self) -> tuple[StoredEquation, ...]:
        return tuple(e for e in self.equations if e.readings)

    @property
    def unreadable(self) -> tuple[StoredEquation, ...]:
        return tuple(e for e in self.equations if not e.readings)

    def variable_for(self, symbol: str) -> StoredVariable | None:
        return next((v for v in self.variables if v.symbol == symbol and v.name), None)


def name_key(text: str) -> str:
    """A quantity's name as questions and documents compare it: lower case, one space, no article."""
    words = " ".join(str(text).casefold().split()).strip(" .,;:")
    for article in ("the ", "a ", "an "):
        if words.startswith(article):
            words = words[len(article):]
    return words


def singular(name: str) -> str:
    """The regular English singular of a name's last word ("resistances" -> "resistance")."""
    words = name.split(" ")
    last = words[-1]
    if len(last) < 4:
        return name
    if last.endswith("ies"):
        stem = last[:-3] + "y"
    elif last.endswith(("sses", "shes", "ches", "xes", "zes")):
        stem = last[:-2]
    elif last.endswith("s") and not last.endswith(("ss", "us", "is")):
        stem = last[:-1]
    else:
        return name
    return " ".join([*words[:-1], stem])


def load(repository: Repository, scope: CalculationScope = CalculationScope.MY_BOOKS,
         named: frozenset[str] = frozenset()) -> Library:
    """The usable equations and named quantities of the knowledge base, within `scope`.

    `named` are the quantities the question itself mentions: they let a product written
    without a sign (`IR`) be read as I times R when the question speaks of both.
    """
    connection = repository.connection
    sources: dict[str, Source | None] = {}

    def in_scope(row: EvidenceRow) -> bool:
        if row.source_id not in sources:
            sources[row.source_id] = repository.get(Source, row.source_id)
        return source_verdict(sources[row.source_id], scope) is Verdict.IN_SCOPE

    pairs = queries.active_equations(connection)
    evidence = queries.evidence_for_many(connection, [knowledge.id for knowledge, _ in pairs])
    kept: list[tuple] = []
    no_evidence = conflicted = 0
    for knowledge, equation in pairs:
        rows = tuple(r for r in (EvidenceRow.of(x) for x in evidence.get(knowledge.id, ())) if in_scope(r))
        if not rows:
            no_evidence += 1
            continue
        stated = [c for c in queries.conflicts_of_knowledge(connection, knowledge.id)
                  if not lifecycle_excluded(c.lifecycle_status)]
        if stated or queries.relationships_touching_knowledge(connection, knowledge.id, RelationType.CONTRADICTS):
            conflicted += 1
            continue
        kept.append((knowledge, equation, rows))

    variables = []
    var_pairs = queries.active_variables(connection)
    var_evidence = queries.evidence_for_many(connection, [k.id for k, _ in var_pairs])
    for knowledge, variable in var_pairs:
        rows = tuple(r for r in (EvidenceRow.of(x) for x in var_evidence.get(knowledge.id, ())) if in_scope(r))
        if rows and is_symbol(variable.symbol):
            variables.append(StoredVariable(knowledge.id, variable.symbol, variable.name, variable.unit,
                                            knowledge.statement, rows))

    # Quantities the documents name on their own: the one-word left-hand sides of equations
    # (`Req`, `I`), the stored variables' symbols and whatever the question names. A product
    # such as `IR` is split only into quantities known this way.
    documented = {v.symbol for v in variables}
    for _, equation, _ in kept:
        text = normalize.plain(equation.expression)
        if isinstance(text, str):
            head = normalize.single_symbol(text.split("=")[0])
            if head:
                documented.add(head)
    atoms = documented | set(named)

    equations = []
    for knowledge, equation, rows in kept:
        readings, problem = normalize.read(equation.expression, atoms)
        reason = None if readings else (problem.reason if problem else "it is not a formula for one quantity")
        if readings and knowledge.certainty not in SOURCE_STATED:
            # A bare quantity on each side ("Y21 = Z") is nearly always a fraction whose second
            # line the PDF text layer lost; as a stored statement it would equate two unrelated
            # quantities. Stated exactly by the source it would be kept.
            whole = [r for r in readings if not isinstance(r.formula.expression, Symbol)]
            if not whole:
                readings, reason = [], ("a bare quantity on each side is usually a fraction whose lower line the "
                                        "PDF text layer lost")
            else:
                readings = whole
        equations.append(StoredEquation(
            knowledge.id, equation.expression, knowledge.certainty, rows, tuple(readings), reason))
    symbols = set(documented)
    for stored in equations:
        for reading in stored.readings:
            symbols.add(reading.formula.target)
            symbols.update(symbols_of(reading.formula.expression))
    return Library(tuple(equations), tuple(variables), Withheld(no_evidence, conflicted), frozenset(symbols))
