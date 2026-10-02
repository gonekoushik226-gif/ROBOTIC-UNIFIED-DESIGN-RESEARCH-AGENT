"""Answering "calculate X given A and B" from the equations the documents state.

`solve` is what a person means by asking RUDRA to calculate: they name what they want and
what they know; RUDRA finds the equations, in the right order, from the knowledge base.

1. **Names become quantities.** What the question writes as a symbol (`V`, `R1`) is used as
   written. What it writes as a name ("the voltage") is matched against the symbols the
   documents gave a meaning ("where V is the voltage") - exactly, never by guesswork; a name
   no document explains is reported, with the quantities that are explained.
2. **The planner finds the equations** (`app.solving.planner`): which stored equations
   connect what is known to what is asked, in what order, rearranged where an equation must
   be turned around. Equations stated exactly by the document are preferred over ones read
   from a flattened PDF text layer.
3. **The exact engine calculates** (`app.calculation`) - rational arithmetic with SI units,
   every step shown, a dimension check on every sum.
4. **Every alternative is calculated too.** Other short routes through the stored equations
   must reach the same value; they are the independent confirmation. If they disagree, the
   answer is *conflicting*: both are shown and neither is chosen.
5. **The answer is verified** by an independent evaluator (`app.verification`).

What cannot be established is said so, naming what is missing. Nothing is ever invented: not
a formula, not a value, not the meaning of a symbol. Reads only; nothing is written.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.calculation import (
    AnswerStatus,
    CalculationEngine,
    CalculationInput,
    CalculationRequest,
    CalculationScope,
    NumberError,
    UnitError,
    Value,
    parse_quantity,
)
from app.calculation.results import EvidenceRow, as_plain
from app.core.errors import InvalidInputError
from app.solving import library as stored
from app.solving.library import Library, StoredVariable
from app.solving.planner import Form, Plan, Search, forms_of, search
from app.storage.repository import Repository
from app.verification.answers import AnswerKind, RecordedAnswer
from app.verification.calculation import verify_calculation

#: Plans this many equations longer than the shortest are still calculated, as confirmation.
EXTRA_STEPS = 2
MAX_PLANS = 8
#: Routes that lean on equations read from a PDF text layer are kept short: each added equation
#: that may have lost a fraction makes the whole chain less trustworthy.
MIXED_STEPS = 3
_SYMBOL_WORD = re.compile(r"[^\W\d_]\w*", re.UNICODE)


class Status(StrEnum):
    #: The asked quantity was calculated.
    CALCULATED = "CALCULATED"
    #: The documents and the question do not establish it; what is missing is named.
    CANNOT_DETERMINE = "CANNOT_DETERMINE"
    #: Routes through the stored equations give different values; none is chosen.
    CONFLICTING = "CONFLICTING"


@dataclass(frozen=True, slots=True)
class Given:
    """One thing the question states: a quantity (a symbol or a name) and its value."""

    quantity: str
    value: str


@dataclass(frozen=True, slots=True)
class SolveRequest:
    target: str
    givens: tuple[Given, ...] = ()
    scope: CalculationScope = CalculationScope.MY_BOOKS


@dataclass(frozen=True, slots=True)
class Binding:
    """What a word of the question stands for."""

    asked: str
    symbol: str | None
    #: "symbol" (written as one), "stored name" (a document explains it), "unknown" or "ambiguous".
    how: str
    #: The document's own name for the symbol, when it has one.
    name: str | None = None
    #: Why it could not be bound, or how it was.
    detail: str = ""
    knowledge_id: str | None = None


@dataclass(frozen=True, slots=True)
class GivenReport:
    asked: str
    symbol: str | None
    value: str
    how: str
    name: str | None = None
    used: bool = False


@dataclass(frozen=True, slots=True)
class StepReport:
    number: int
    symbol: str
    #: The equation as applied, in the formula grammar.
    formula: str
    #: The equation as the document printed it.
    stored_text: str
    knowledge_id: str
    rearranged: bool
    substitution: str
    result: Value
    inputs: tuple[tuple[str, str, str | None], ...]
    #: False when the equation was read from a flattened PDF text layer.
    stated_by_source: bool
    notes: tuple[str, ...] = ()
    evidence: tuple[EvidenceRow, ...] = ()


@dataclass(frozen=True, slots=True)
class Route:
    """Another way through the stored equations, and the value it gives."""

    equations: tuple[str, ...]
    value: Value | None
    #: Why the route gave no value (for example a dimension mismatch); empty when it did.
    problem: str = ""
    agrees: bool = False


@dataclass(frozen=True, slots=True)
class Missing:
    """A stored equation that would give the quantity, and what it still needs."""

    formula: str
    stored_text: str
    knowledge_id: str
    needs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LibrarySummary:
    equations: int
    usable: int
    unreadable: int
    withheld: int


@dataclass(frozen=True, slots=True)
class SolveResult:
    status: Status
    message: str
    target: Binding
    givens: tuple[GivenReport, ...]
    result: Value | None = None
    steps: tuple[StepReport, ...] = ()
    #: The quantities the answer involved, each with the document's name for it.
    quantities: tuple[tuple[str, str | None], ...] = ()
    routes: tuple[Route, ...] = ()
    missing: tuple[Missing, ...] = ()
    #: Quantities RUDRA does not know the meaning of: asked about, but no document explains them.
    unknown: tuple[str, ...] = ()
    #: VERIFIED, FAILED, INCONCLUSIVE, or NOT_RUN when there was nothing to verify.
    verification: str = "NOT_RUN"
    verification_detail: str = ""
    uncertain: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    library: LibrarySummary = LibrarySummary(0, 0, 0, 0)
    truncated: bool = False
    #: The exact engine's whole trace for the chosen route, for re-checking and for sources.
    engine: dict | None = None
    #: Quantities the question gave that no step needed.
    unused: tuple[str, ...] = ()
    #: Spellings the documents use instead of the question's (for example `i` for `I`), offered
    #: as a question to the user, never applied.
    suggestions: tuple[str, ...] = ()
    known_quantities: tuple[tuple[str, str], ...] = field(default=())


def refuse(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="solving.request", data_changed=False, retry_safe=True,
        next_options=('python -m app solve I --input "V=10 V" --input "R=5 Ω"',
                      "Ask in plain English: Calculate the current when V = 10 V and R = 5 Ω."),
    )


# ------------------------------------------------------------------ names


def _bind(token: str, library: Library, *, wanted: bool) -> Binding:
    """What a word of the question stands for - in the documents' own terms, or not at all."""
    text = " ".join(token.split()).strip(" .,;:")
    if not text:
        return Binding(token, None, "unknown", detail="nothing was named")
    explained = {v.symbol: v for v in library.variables if v.name}
    if text in explained:
        variable = explained[text]
        return Binding(token, text, "symbol", variable.name, knowledge_id=variable.knowledge_id)
    key = stored.name_key(text)
    matches: dict[str, StoredVariable] = {}
    for variable in library.variables:
        if variable.name and stored.name_key(variable.name) in (key, stored.singular(key)):
            matches.setdefault(variable.symbol, variable)
    if len(matches) == 1:
        (variable,) = matches.values()
        return Binding(token, variable.symbol, "stored name", variable.name,
                       f"your documents say {variable.symbol} is the {variable.name}", variable.knowledge_id)
    if text in library.symbols:
        return Binding(token, text, "symbol")
    if len(matches) > 1:
        shown = ", ".join(sorted(matches))
        return Binding(token, None, "ambiguous",
                       detail=f"your documents give more than one symbol for '{text}' ({shown}); say which one")
    folded = [s for s in library.symbols if s.casefold() == text.casefold()]
    if len(folded) == 1 and _SYMBOL_WORD.fullmatch(text):
        return Binding(token, None, "unknown",
                       detail=f"no symbol '{text}' appears in your documents' equations; they use '{folded[0]}'")
    if _SYMBOL_WORD.fullmatch(text) and len(text) <= 12 and not wanted:
        # A symbol the documents never use: harmless as something the question simply states.
        return Binding(token, text, "symbol")
    if _SYMBOL_WORD.fullmatch(text) and len(text) <= 3:  # a letter or two: a symbol, not a word
        return Binding(token, None, "unknown",
                       detail=f"no stored equation uses '{text}', and no document says what it stands for")
    return Binding(token, None, "unknown", detail=f"no document says which symbol stands for '{text}'")


def _known_list(library: Library) -> tuple[tuple[str, str], ...]:
    seen, listed = set(), []
    for variable in library.variables:
        if variable.name and (variable.symbol, variable.name) not in seen:
            seen.add((variable.symbol, variable.name))
            listed.append((variable.symbol, variable.name))
    return tuple(listed)


# ------------------------------------------------------------------ the engine


@dataclass(frozen=True, slots=True)
class _Run:
    plan: Plan
    result: object | None
    problem: str = ""


def _run(plan: Plan, target: str, values: dict[str, str], scope: CalculationScope) -> _Run:
    used = {symbol for step in plan.steps for symbol in step.inputs if symbol in values}
    try:
        request = CalculationRequest(
            target,
            formulas=tuple(step.formula.text for step in plan.steps),
            inputs=tuple(CalculationInput(symbol, values[symbol]) for symbol in sorted(used)),
            scope=scope,
        )
        result = CalculationEngine().calculate(request)
    except InvalidInputError as error:
        return _Run(plan, None, error.report.summary)
    if result.status is not AnswerStatus.CALCULATED:
        return _Run(plan, result, _why_not(result))
    return _Run(plan, result)


def _why_not(result) -> str:
    """The root cause of an engine answer without a value: the deepest method that failed."""
    from app.calculation import MethodState

    root = (MethodState.DIMENSIONALLY_INCONSISTENT, MethodState.EVALUATION_ERROR)
    methods = [m for symbol in result.symbols for m in symbol.methods if m.reason]
    for method in methods:
        if method.state in root:
            return f"{method.text}: {method.reason}"
    for method in methods:
        return f"{method.text}: {method.reason}"
    return result.message


def _report_steps(run: _Run, library: Library) -> tuple[StepReport, ...]:
    by_text = {form.formula.text: form for form in run.plan.steps}
    reports = []
    for step in run.result.steps:
        form = by_text.get(step.text)
        if form is None:  # pragma: no cover - the engine evaluates only the plan's formulas
            continue
        reports.append(StepReport(
            number=step.number, symbol=step.symbol, formula=step.text, stored_text=form.equation.stored_text,
            knowledge_id=form.equation.knowledge_id, rearranged=form.rearranged, substitution=step.substitution,
            result=step.result,
            inputs=tuple((i.symbol, i.value.text, None if i.from_step is None else f"step {i.from_step}")
                         for i in step.inputs),
            stated_by_source=form.equation.stated_by_source,
            notes=form.reading.notes, evidence=form.equation.evidence[:3],
        ))
    return tuple(reports)


def _verify(run: _Run) -> tuple[str, str]:
    recorded = as_plain(run.result)
    answer = RecordedAnswer(AnswerKind.CALCULATION, run.result.request, recorded)
    verdict = verify_calculation(answer, None)
    return verdict.status.value, verdict.message


# ------------------------------------------------------------------ what is missing


def _spelling_hints(forms: list[Form], library: Library, known: set[str], goal: str) -> tuple[str, ...]:
    """Where the documents write a quantity in another case (`i` for `I`), and a route would exist.

    Case matters to RUDRA (`V` is not `v`), so the question is never silently changed; but a
    question that fails only because of capitals deserves to be told so.
    """
    spellings: dict[str, str] = {}
    for symbol in (goal, *sorted(known)):
        others = sorted(s for s in library.symbols if s != symbol and s.casefold() == symbol.casefold())
        if len(others) == 1:
            spellings[symbol] = others[0]
    if not spellings:
        return ()
    hints = []
    options = [dict([item]) for item in spellings.items()] + ([dict(spellings)] if len(spellings) > 1 else [])
    for change in options:
        asked = {change.get(symbol, symbol) for symbol in known}
        wanted = change.get(goal, goal)
        found = search(forms, asked, wanted)
        for plan in found.plans:
            mentioned = [step for step in plan.steps
                         if any(symbol in change.values() for symbol in (step.output, *step.inputs))]
            if mentioned and all(any(new in (step.output, *step.inputs) for step in plan.steps)
                                 for new in change.values()):
                rewritten = ", ".join(f"{old} as {new}" for old, new in change.items())
                step = mentioned[-1]
                hints.append(f"Your documents write {rewritten}; for example {step.formula.text} "
                             f"({step.equation.knowledge_id}). If that is the same quantity, ask again using "
                             "their spelling.")
                break
        if hints:
            break
    return tuple(hints)


def _missing(forms: list[Form], target: str, known: set[str]) -> tuple[Missing, ...]:
    """The equations that would give `target`, each with what the question did not supply."""
    options = []
    seen = set()
    for form in forms:
        if form.output != target or form.identity in seen:
            continue
        seen.add(form.identity)
        needs = tuple(symbol for symbol in form.inputs if symbol not in known)
        options.append(Missing(form.formula.text, form.equation.stored_text, form.equation.knowledge_id, needs))
    options.sort(key=lambda m: (len(m.needs), m.knowledge_id, m.formula))
    return tuple(options[:5])


# ------------------------------------------------------------------ solve


def solve(repository: Repository, request: SolveRequest) -> SolveResult:
    """Answer one calculation request from the knowledge base (module docstring)."""
    if not request.target or not request.target.strip():
        raise refuse("A calculation needs something to calculate.", "No quantity was named.")
    values: dict[str, str] = {}
    parsed: list[tuple[Given, str]] = []
    for given in request.givens:
        try:
            parse_quantity(given.value)
        except (NumberError, UnitError) as error:
            raise refuse(f"The value {given.value!r} given for {given.quantity!r} is not usable.", str(error)) from error
        parsed.append((given, given.value))

    words = (" ".join(text.split()) for text in (request.target, *(g.quantity for g in request.givens)))
    named = frozenset(word for word in words if _SYMBOL_WORD.fullmatch(word))
    library = stored.load(repository, request.scope, named)
    summary = LibrarySummary(len(library.equations), len(library.usable), len(library.unreadable),
                             library.withheld.no_authorized_evidence + library.withheld.stored_conflict)
    known_list = _known_list(library)

    target = _bind(request.target, library, wanted=True)
    bound = [(given, _bind(given.quantity, library, wanted=False)) for given, _ in parsed]
    problems = [b.detail for b in (target, *(b for _, b in bound)) if b.symbol is None]
    unknown = tuple(dict.fromkeys(b.asked for b in (target, *(b for _, b in bound)) if b.symbol is None))
    reports = tuple(GivenReport(g.quantity, b.symbol, g.value, b.how, b.name) for g, b in bound)
    if not library.usable:
        reason = ("No equation that can be calculated with has been stored yet - add a document that states some."
                  if not library.equations else
                  f"{len(library.equations)} stored equation(s) were found, but none can be used for a calculation "
                  "(each is an integral, derivative, flattened fraction or other shape that is not plain arithmetic).")
        return SolveResult(Status.CANNOT_DETERMINE, reason, target, reports, unknown=unknown, library=summary,
                           known_quantities=known_list)
    if problems:
        return SolveResult(
            Status.CANNOT_DETERMINE,
            "RUDRA cannot tell which quantities you mean: " + "; ".join(problems) + ".",
            target, reports, unknown=unknown, library=summary, known_quantities=known_list,
            notes=("Write the symbol your documents use for it, for example R = 5 Ω.",) if known_list or
            library.usable else ("No document has been added that states equations.",))

    for given, binding in bound:
        if binding.symbol in values and values[binding.symbol] != given.value:
            raise refuse(f"{binding.symbol} was given two different values ({values[binding.symbol]} and "
                         f"{given.value}).", "RUDRA will not choose between two values for one quantity.")
        values[binding.symbol] = given.value
    goal = target.symbol
    assert goal is not None
    quantities = {goal: target.name}
    for _, binding in bound:
        quantities.setdefault(binding.symbol, binding.name)

    def finish(**fields) -> SolveResult:
        used = set(fields.pop("used", ()))
        marked = tuple(GivenReport(r.asked, r.symbol, r.value, r.how, r.name, r.symbol in used) for r in reports)
        return SolveResult(target=target, givens=marked, library=summary, known_quantities=known_list,
                           unused=tuple(r.symbol for r in marked if not r.used and r.symbol != goal),
                           **fields)

    if goal in values:
        return finish(status=Status.CALCULATED, message=f"{goal} was given: {values[goal]}.",
                      result=_value_of(values[goal]), used={goal}, quantities=_pairs(quantities),
                      verification="INCONCLUSIVE",
                      verification_detail="the value is the question's own; nothing independent confirms it")

    forms = forms_of(library)
    known = set(values)
    first: Search = search([f for f in forms if f.equation.stated_by_source], known, goal)
    found, tier = first, "stated"
    if not found.plans:
        found, tier = search(forms, known, goal), "mixed"
    if not found.plans:
        gaps = _missing(forms, goal, known)
        if gaps:
            message = (f"The stored equations cannot give {_label(goal, quantities)} from what you gave "
                       f"({', '.join(sorted(known)) or 'nothing'}).")
        else:
            message = f"No stored equation gives {_label(goal, quantities)}; your documents do not relate it to anything."
        return finish(status=Status.CANNOT_DETERMINE, message=message, missing=gaps, quantities=_pairs(quantities),
                      truncated=found.truncated, suggestions=_spelling_hints(forms, library, known, goal))

    best = len(found.plans[0].steps)
    longest = best + EXTRA_STEPS if tier == "stated" else min(best + EXTRA_STEPS, MIXED_STEPS)
    candidates = [p for p in found.plans if len(p.steps) <= max(longest, best)][:MAX_PLANS]
    runs = [_run(plan, goal, values, request.scope) for plan in candidates]
    valid = [r for r in runs if r.problem == "" and r.result is not None]
    routes_of = lambda r: Route(  # noqa: E731
        tuple(f"{s.formula.text} ({s.equation.knowledge_id})" for s in r.plan.steps),
        None if r.problem else r.result.result, r.problem)
    if not valid:
        reasons = "; ".join(dict.fromkeys(r.problem for r in runs if r.problem))
        return finish(status=Status.CANNOT_DETERMINE, truncated=found.truncated, quantities=_pairs(quantities),
                      message=f"The stored equations that lead to {_label(goal, quantities)} gave no value: {reasons}.",
                      routes=tuple(routes_of(r) for r in runs))

    distinct: dict[tuple, list[_Run]] = {}
    for run in valid:
        value = run.result.result
        distinct.setdefault((value.exact, tuple(value.dimension)), []).append(run)
    notes = []
    if tier == "mixed":
        notes.append("No route uses only equations the source states exactly; at least one equation was read from "
                     "a PDF page's text layer, which can lose fractions and powers.")
    if len(distinct) > 1:
        groups = list(distinct.values())
        shown = [f"{_label(goal, quantities)} {g[0].result.result.relation} {g[0].result.result.text}" for g in groups]
        return finish(
            status=Status.CONFLICTING, truncated=found.truncated, quantities=_pairs(quantities),
            message=(f"Your documents' equations give different values: {'; '.join(shown)}. "
                     f"No value is asserted and none was chosen."),
            routes=tuple(Route(tuple(f"{s.formula.text} ({s.equation.knowledge_id})" for s in g[0].plan.steps),
                               g[0].result.result, "", False) for g in groups),
            notes=tuple(notes))
    primary = valid[0]
    agreeing = [r for r in valid[1:]]
    routes = tuple(Route(tuple(f"{s.formula.text} ({s.equation.knowledge_id})" for s in r.plan.steps),
                         r.result.result, "", True) for r in agreeing)
    failed = tuple(routes_of(r) for r in runs if r.problem)
    verification, detail = _verify(primary)
    steps = _report_steps(primary, library)
    used = {i for s in primary.plan.steps for i in s.inputs if i in values}
    for step in primary.plan.steps:
        for symbol in (step.output, *step.inputs):
            variable = library.variable_for(symbol)
            quantities.setdefault(symbol, variable.name if variable else None)
    uncertain = tuple(
        f"{s.formula} - read from a PDF page's text layer, which can lose fractions and powers; compare it with "
        f"the source page" for s in steps if not s.stated_by_source)
    if agreeing:
        notes.append(f"{len(agreeing)} other route(s) through the stored equations give the same value.")
    if found.truncated:
        notes.append("The search for routes stopped at its effort limit; there may be routes it did not examine.")
    value = primary.result.result
    return finish(
        status=Status.CALCULATED, message=f"{goal} {value.relation} {value.text}", result=value, steps=steps,
        quantities=_pairs(quantities), routes=routes + failed, verification=verification,
        verification_detail=detail, uncertain=uncertain, notes=tuple(notes), truncated=found.truncated,
        engine=as_plain(primary.result), used=used)


def _pairs(quantities: dict) -> tuple[tuple[str, str | None], ...]:
    return tuple(quantities.items())


def _label(symbol: str, quantities: dict) -> str:
    name = quantities.get(symbol)
    return f"{symbol} ({name})" if name else symbol


def _value_of(text: str) -> Value:
    from app.calculation.units import parse_quantity as quantity_of

    return Value.of(quantity_of(text))
