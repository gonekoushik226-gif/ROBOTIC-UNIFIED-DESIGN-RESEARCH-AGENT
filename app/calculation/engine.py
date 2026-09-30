"""The Phase 11 calculation engine (ADRs 0041-0043; Part 5 sections 202-203).

**What it calculates with.** Only what the request states (ADR 0041 P11-4, P11-5): its
formulas, its input values (`USER_INPUT`) and assumptions (`ASSUMPTION`), and **at most
one** stored equation it explicitly admits by identifier (N2 = (b); `app.calculation.
admission`). Stored knowledge is never used merely because it exists, and no value is
read out of stored text.

**Resolution** (ADR 0042 P11-17) is target-directed and backward. A **method** is one
candidate formula whose left-hand symbol is the symbol being resolved; the candidates are
the request's formulas in request order, then the admitted equation. Each method's
right-hand symbols are resolved first. Every symbol reached carries one of section 84's
states:

    AVAILABLE    the request supplies it (an input or an assumption)
    DERIVED      not supplied; at least one method is complete, and all complete
                 methods agree exactly, in the same dimension
    MISSING      not supplied, and no candidate formula defines it
    BLOCKED      formulas define it, but none is complete (each method's reason is
                 reported); or it is on a formula cycle and not supplied
    CONFLICTING  its complete methods disagree, or one disagrees with the supplied value;
                 no value is asserted, and every method using it is BLOCKED

**Cycles** are found as strongly connected components and never looped (the P10-20
rule). A supplied symbol on a cycle stays AVAILABLE; every other symbol on the cycle is
BLOCKED (the ADR 0039 Amendment 1 rule, P11-17). A method whose formula uses a symbol of
its own cycle is reported ON_CYCLE and not evaluated.

**Methods** (P11-18): every method of every symbol reached is evaluated separately and
reported; none is selected, preferred or ranked. A supplied symbol is the direct answer,
and its formulas are still evaluated: a disagreement makes it CONFLICTING, never hidden.

**Evaluation** is exact (`app.calculation.evaluate`): rationals, SI dimensions, and a
dimensional check on every `+` and `-` (P11-16). A mismatch or an evaluation error is
the method's outcome; no number is produced from it.

**What it returns** (P11-22): the returned trace in `app.calculation.results`. Nothing is
written (N5 = (a)); without an admission, no database is touched at all (P11-29). Every
result carries the verification status `PENDING` (P11-7). No hidden chain-of-thought
exists to expose.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from app.calculation.admission import AdmissionOutcome, read_admission
from app.calculation.evaluate import EvaluationError, EvaluationProblem, evaluate
from app.calculation.formulas import (
    GRAMMAR_NAME,
    GRAMMAR_VERSION,
    Expression,
    Formula,
    FormulaError,
    Negate,
    Number,
    Power,
    Product,
    Sum,
    Symbol,
    parse_formula,
)
from app.calculation.numbers import DISPLAY_RULE
from app.calculation.requests import CalculationRequest, CheckedRequest, refuse
from app.calculation.results import (
    AnswerStatus,
    AssumptionEffect,
    CalculationResult,
    FormulaEntry,
    MethodResult,
    MethodState,
    MissingSymbol,
    Need,
    Origin,
    RefusalReason,
    Step,
    StepInput,
    SuppliedValue,
    SymbolResult,
    SymbolState,
    Value,
    Versions,
    Withheld,
    VERIFICATION_PENDING,
)
from app.calculation.units import UNIT_TABLE_NAME, UNIT_TABLE_VERSION, Quantity
from app.core.errors import StorageError
from app.storage import CODE_SCHEMA_VERSION, queries, schema_version
from app.storage.repository import Repository

#: The engine's name and version, carried in every trace (P6 §11; ADR 0042 P11-24).
ENGINE_NAME = "RUDRA calculation engine"
ENGINE_VERSION = "1"

WRITES_NOTHING_NOTE = (
    "Nothing was written: the result and its trace are returned only, never stored "
    "(ADR 0041 P11-6, N5 = (a))."
)
PENDING_NOTE = (
    "Verification status PENDING: this result is not independently verified; "
    "verification is Phase 12 (ADR 0041 P11-7). The dimensional checks are checks, not "
    "verification."
)
NOT_A_FACT_NOTE = (
    "A calculated value is a calculation result, never a sourced fact (section 94)."
)


# ------------------------------------------------------------------- candidates


@dataclass(frozen=True, slots=True)
class _Candidate:
    entry: FormulaEntry
    formula: Formula | None
    #: Why an admitted equation cannot be used; None for a usable formula.
    not_usable: str | None = None
    conflicting_support: str | None = None


@dataclass(slots=True)
class _Symbol:
    """A symbol's working record while the calculation runs."""

    name: str
    state: SymbolState | None = None
    origins: tuple[Origin, ...] = ()
    quantity: Quantity | None = None
    methods: list[MethodResult] = field(default_factory=list)
    cycle: tuple[str, ...] = ()
    conflicting: tuple[Value, ...] = ()
    conditional_on: tuple[str, ...] = ()
    uncertain: bool = False
    from_step: int | None = None


class CalculationEngine:
    """Answers a `CalculationRequest` (module docstring). Reads at most one stored equation.

    `repository` is needed only when the request admits a stored equation; without an
    admission the engine touches no database (ADR 0043 P11-29).
    """

    def __init__(self, repository: Repository | None = None) -> None:
        self.repository = repository

    def calculate(self, request: CalculationRequest) -> CalculationResult:
        """Raises `InvalidInputError` for an invalid request and `StorageError` for a
        database this build cannot read; otherwise always answers."""
        checked = request.check()  # before any database is read
        admission: AdmissionOutcome | None = None
        if checked.admission is not None:
            if self.repository is None:
                raise ValueError("A request that admits a stored equation needs a repository.")
            self._require_schema()
            admission = read_admission(
                self.repository, checked.admission, request.scope, _request_symbols(checked)
            )
        formulas = _recheck_formulas(checked, admission)
        return _Calculation(checked, formulas, admission, self.repository).run()

    def _require_schema(self) -> None:
        """Refuse a database whose schema is not this build's; never migrate (P11-29)."""
        assert self.repository is not None
        version = schema_version(self.repository.connection)
        if version != CODE_SCHEMA_VERSION:
            raise StorageError.of(
                "The knowledge database's schema version is not the one this build reads; "
                "calculation never migrates.",
                f"Its schema version is {version}; this build reads version {CODE_SCHEMA_VERSION}.",
                stage="calculation.schema",
                data_changed=False,
                retry_safe=True,
                next_options=(
                    "Check that the project root names the intended project.",
                    "Migrate explicitly with: python -m app db  (this writes to the database; "
                    "for the live database make a fresh D-15 backup first).",
                ),
            )


def _request_symbols(checked: CheckedRequest) -> set[str]:
    """The request's own symbols: target, inputs, assumptions and formula targets."""
    return {
        checked.request.target,
        *(symbol for symbol, _ in checked.inputs),
        *(symbol for symbol, _ in checked.assumptions),
        *(formula.target for formula in checked.formulas),
    }


def _recheck_formulas(checked: CheckedRequest, admission: AdmissionOutcome | None) -> tuple[Formula, ...]:
    """Request formulas, re-judged with the admitted equation's target known (P11-12).

    The admitted target joins the calculation's symbols, so a request formula it and the
    others spell side by side is implicit multiplication: an invalid request.
    """
    if admission is None or admission.target is None or admission.admitted is None:
        return checked.formulas
    known = _request_symbols(checked) | {admission.target}
    formulas = []
    for formula in checked.formulas:
        try:
            formulas.append(parse_formula(formula.text, known))
        except FormulaError as exc:
            raise refuse(
                f"The formula {formula.text!r} is outside the formula grammar ({exc.problem}).",
                f"{exc} The admitted equation's target {admission.target!r} is one of the "
                "calculation's symbols.",
            ) from exc
    return tuple(formulas)


# ------------------------------------------------------------------ one calculation


class _Calculation:
    """One request's resolution, evaluation and trace."""

    def __init__(
        self,
        checked: CheckedRequest,
        formulas: tuple[Formula, ...],
        admission: AdmissionOutcome | None,
        repository: Repository | None,
    ) -> None:
        self.checked = checked
        self.request = checked.request
        self.admission = admission
        self.repository = repository
        self.supplied: dict[str, tuple[Origin, Quantity, str]] = {}
        texts = {**{i.symbol: i.value for i in self.request.inputs},
                 **{a.symbol: a.value for a in self.request.assumptions}}
        for symbol, quantity in checked.inputs:
            self.supplied[symbol] = (Origin.USER_INPUT, quantity, texts[symbol])
        for symbol, quantity in checked.assumptions:
            self.supplied[symbol] = (Origin.ASSUMPTION, quantity, texts[symbol])
        self.assumption_order = [symbol for symbol, _ in checked.assumptions]
        self.candidates = self._candidates(formulas)
        self.methods_of: dict[str, list[_Candidate]] = {}
        for candidate in self.candidates:
            if candidate.entry.target is not None:
                self.methods_of.setdefault(candidate.entry.target, []).append(candidate)
        self.symbols: dict[str, _Symbol] = {}
        self.steps: list[Step] = []
        #: The exact quantity each evaluation step produced, by step number.
        self.quantities: dict[int, Quantity] = {}

    # ------------------------------------------------------------ the candidates

    def _candidates(self, formulas: tuple[Formula, ...]) -> tuple[_Candidate, ...]:
        found = [
            _Candidate(
                FormulaEntry(number, formula.text, formula.target, Origin.USER_INPUT, None, formula.symbols),
                formula,
            )
            for number, formula in enumerate(formulas, start=1)
        ]
        admitted = None if self.admission is None else self.admission.admitted
        if admitted is not None:
            formula = self.admission.formula
            support = None
            if admitted.has_conflicting_support:
                named = [item.conflict.id for item in admitted.conflicts] + [
                    item.relationship.id for item in admitted.contradictions
                ]
                support = (
                    "stored conflict information names the admitted equation "
                    f"({', '.join(named)}); both claims are shown, neither is selected (P11-19)"
                )
            found.append(
                _Candidate(
                    FormulaEntry(
                        len(found) + 1,
                        admitted.equation.expression,
                        self.admission.target,
                        Origin.ADMITTED_STORED_ITEM,
                        admitted.knowledge.id,
                        () if formula is None else formula.symbols,
                    ),
                    formula,
                    admitted.not_usable,
                    support,
                )
            )
        return tuple(found)

    # ------------------------------------------------------------ the graph

    def _successors(self, symbol: str) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for candidate in self.methods_of.get(symbol, ()):
            if candidate.formula is not None:
                for used in candidate.formula.symbols:
                    seen.setdefault(used, None)
        return tuple(seen)

    def run(self) -> CalculationResult:
        target = self.request.target
        order, components = _strongly_connected(target, self._successors)
        position = {name: i for i, name in enumerate(order)}
        for name in order:
            self.symbols[name] = _Symbol(name)
        cycles: list[tuple[str, ...]] = []
        for component in components:
            members = sorted(component, key=position.__getitem__)
            if len(members) > 1:
                cycle = tuple(members)
                cycles.append(cycle)
                self._settle_cycle(cycle)
            else:
                self._settle(members[0])
        return self._result(order, tuple(sorted(cycles, key=lambda c: position[c[0]])))

    # ------------------------------------------------------------ settling symbols

    def _supply(self, record: _Symbol) -> None:
        origin, quantity, _ = self.supplied[record.name]
        record.state = SymbolState.AVAILABLE
        record.origins = (origin,)
        record.quantity = quantity
        record.conditional_on = (record.name,) if origin is Origin.ASSUMPTION else ()

    def _settle_cycle(self, cycle: tuple[str, ...]) -> None:
        """A formula cycle: never looped (P11-17; the ADR 0039 Amendment 1 rule)."""
        members = set(cycle)
        for name in cycle:
            record = self.symbols[name]
            record.cycle = cycle
            if name in self.supplied:
                self._supply(record)
            else:
                record.state = SymbolState.BLOCKED
        for name in cycle:
            record = self.symbols[name]
            for candidate in self.methods_of.get(name, ()):
                if candidate.formula is not None and members & set(candidate.formula.symbols):
                    uses = [s for s in candidate.formula.symbols if s in members]
                    record.methods.append(self._method(
                        candidate, MethodState.ON_CYCLE,
                        f"it uses {', '.join(uses)}, on the formula cycle {' -> '.join(cycle)}; "
                        "a cycle is reported, never looped (P11-17)",
                    ))
                elif name not in self.supplied:
                    record.methods.append(self._method(
                        candidate, MethodState.ON_CYCLE,
                        f"{name} is on the formula cycle {' -> '.join(cycle)} and is not supplied; "
                        "a symbol on a cycle is not calculated (P11-17)",
                    ))
                else:
                    record.methods.append(self._evaluate(name, candidate))
            if name in self.supplied:
                self._check_supplied(record)

    def _settle(self, name: str) -> None:
        record = self.symbols[name]
        candidates = self.methods_of.get(name, ())
        if name in self.supplied:
            self._supply(record)
            record.methods = [self._evaluate(name, c) for c in candidates]
            self._check_supplied(record)
            return
        if not candidates:
            record.state = SymbolState.MISSING
            return
        record.methods = [self._evaluate(name, c) for c in candidates]
        complete = [m for m in record.methods if m.state is MethodState.COMPLETE]
        if not complete:
            record.state = SymbolState.BLOCKED
            return
        values = _distinct(m.value for m in complete)
        if len(values) > 1:
            record.state = SymbolState.CONFLICTING
            record.conflicting = values
            return
        first = complete[0]
        record.state = SymbolState.DERIVED
        record.origins = (Origin.DERIVED,)
        record.quantity = self.quantities[first.step]
        record.from_step = first.step
        record.conditional_on = self._ordered_assumptions(c for m in complete for c in m.conditional_on)
        record.uncertain = any(m.uncertain_formula for m in complete)

    def _check_supplied(self, record: _Symbol) -> None:
        """A complete method disagreeing with the supplied value: CONFLICTING (P11-18)."""
        supplied = Value.of(record.quantity)
        values = _distinct([supplied, *(m.value for m in record.methods if m.state is MethodState.COMPLETE)])
        if len(values) > 1:
            record.state = SymbolState.CONFLICTING
            record.conflicting = values
            record.origins = ()
            record.quantity = None
            record.conditional_on = ()

    # ------------------------------------------------------------ one method

    def _method(self, candidate: _Candidate, state: MethodState, reason: str, **extra) -> MethodResult:
        """A method's report. Every parsed formula lists its input symbols with their
        states, evaluated or not, so a MISSING symbol is named with every formula that
        needs it (section 89)."""
        entry = candidate.entry
        if "needs" not in extra and candidate.formula is not None:
            extra["needs"] = tuple(Need(s, self.symbols[s].state) for s in candidate.formula.symbols)
        return MethodResult(
            symbol=entry.target or "",
            formula=entry.number,
            text=entry.text,
            origin=entry.origin,
            knowledge_id=entry.knowledge_id,
            state=state,
            reason=reason,
            **extra,
        )

    def _evaluate(self, name: str, candidate: _Candidate) -> MethodResult:
        if candidate.formula is None:
            return self._method(candidate, MethodState.NOT_USABLE, candidate.not_usable or "not usable")
        if candidate.conflicting_support is not None:
            return self._method(candidate, MethodState.CONFLICTING_SUPPORT, candidate.conflicting_support)
        formula = candidate.formula
        needs = tuple(Need(s, self.symbols[s].state) for s in formula.symbols)
        missing = [n.symbol for n in needs if n.state is SymbolState.MISSING]
        if missing:
            return self._method(
                candidate, MethodState.MISSING_INPUT,
                f"it needs {', '.join(missing)}, which no input supplies and no formula defines",
                needs=needs,
            )
        unsettled = [n for n in needs if n.state not in (SymbolState.AVAILABLE, SymbolState.DERIVED)]
        if unsettled:
            return self._method(
                candidate, MethodState.BLOCKED,
                "it needs " + ", ".join(f"{n.symbol} ({n.state.value})" for n in unsettled),
                needs=needs,
            )
        bindings = {s: self.symbols[s].quantity for s in formula.symbols}
        try:
            quantity = evaluate(formula.expression, bindings)  # type: ignore[arg-type]
        except EvaluationError as exc:
            state = (
                MethodState.DIMENSIONALLY_INCONSISTENT
                if exc.problem is EvaluationProblem.DIMENSION_MISMATCH
                else MethodState.EVALUATION_ERROR
            )
            return self._method(candidate, state, f"{exc.problem.value}: {exc}", needs=needs)
        conditional = self._ordered_assumptions(
            c for s in formula.symbols for c in self.symbols[s].conditional_on
        )
        uncertain = candidate.entry.origin is Origin.ADMITTED_STORED_ITEM or any(
            self.symbols[s].uncertain for s in formula.symbols
        )
        number = len(self.steps) + 1
        value = Value.of(quantity)
        self.quantities[number] = quantity
        self.steps.append(Step(
            number=number,
            symbol=name,
            formula=candidate.entry.number,
            text=formula.text,
            substitution=f"{name} = {_render(formula.expression, lambda s: Value.of(bindings[s]))}",
            result=value,
            formula_origin=candidate.entry.origin,
            knowledge_id=candidate.entry.knowledge_id,
            inputs=tuple(
                StepInput(s, self._origin_of(s), Value.of(bindings[s]), self.symbols[s].from_step)
                for s in formula.symbols
            ),
            dimension_check=_dimension_check(formula.expression, value),
            conditional_on=conditional,
            uncertain_formula=uncertain,
        ))
        return self._method(
            candidate, MethodState.COMPLETE, "", needs=needs, value=value, step=number,
            conditional_on=conditional, uncertain_formula=uncertain,
        )

    def _origin_of(self, symbol: str) -> Origin:
        record = self.symbols[symbol]
        return record.origins[0] if record.origins else Origin.DERIVED

    def _ordered_assumptions(self, names: Iterable[str]) -> tuple[str, ...]:
        wanted = set(names)
        return tuple(name for name in self.assumption_order if name in wanted)

    # ------------------------------------------------------------ the result

    def _result(self, order: list[str], cycles: tuple[tuple[str, ...], ...]) -> CalculationResult:
        target = self.symbols[self.request.target]
        results = tuple(
            SymbolResult(
                symbol=record.name,
                state=record.state,  # type: ignore[arg-type]
                origins=record.origins,
                value=None if record.quantity is None else Value.of(record.quantity),
                methods=tuple(record.methods),
                cycle=record.cycle,
                conflicting_values=record.conflicting,
                conditional_on=record.conditional_on,
                uncertain_formula=record.uncertain,
            )
            for record in (self.symbols[name] for name in order)
        )
        missing = self._missing(order)
        status = self._status(target)
        value = None if target.quantity is None or status is not AnswerStatus.CALCULATED else Value.of(target.quantity)
        admitted = None if self.admission is None else self.admission.admitted
        refused = None if self.admission is None else self.admission.refused
        withheld = Withheld() if self.admission is None else self.admission.withheld
        return CalculationResult(
            request=self.request,
            status=status,
            message=self._message(status, target, value, missing),
            target=self.request.target,
            result=value,
            inputs=self._supplied(Origin.USER_INPUT),
            assumptions=self._supplied(Origin.ASSUMPTION),
            formulas=tuple(c.entry for c in self.candidates),
            admitted=admitted,
            refused=refused,
            symbols=results,
            steps=tuple(self.steps),
            missing=missing,
            cycles=cycles,
            effects=self._effects(results),
            withheld=withheld,
            verification_status=VERIFICATION_PENDING,
            versions=Versions(
                engine=ENGINE_NAME, engine_version=ENGINE_VERSION,
                grammar=GRAMMAR_NAME, grammar_version=GRAMMAR_VERSION,
                unit_table=UNIT_TABLE_NAME, unit_table_version=UNIT_TABLE_VERSION,
                display_rule=DISPLAY_RULE,
            ),
            notes=self._notes(results, withheld),
            database_opened=self.repository is not None and self.admission is not None,
            read_only_connection=(
                queries.connection_is_read_only(self.repository.connection)
                if self.repository is not None and self.admission is not None else None
            ),
        )

    def _supplied(self, origin: Origin) -> tuple[SuppliedValue, ...]:
        return tuple(
            SuppliedValue(symbol, origin, text, Value.of(quantity))
            for symbol, (given, quantity, text) in self.supplied.items()
            if given is origin
        )

    def _missing(self, order: list[str]) -> tuple[MissingSymbol, ...]:
        gaps = []
        for name in order:
            if self.symbols[name].state is not SymbolState.MISSING:
                continue
            needing = []
            for other in order:
                for method in self.symbols[other].methods:
                    if any(n.symbol == name for n in method.needs) and method.text not in needing:
                        needing.append(method.text)
            gaps.append(MissingSymbol(name, tuple(needing)))
        return tuple(gaps)

    def _status(self, target: _Symbol) -> AnswerStatus:
        if target.state in (SymbolState.AVAILABLE, SymbolState.DERIVED):
            return AnswerStatus.CALCULATED
        if target.state is SymbolState.CONFLICTING:
            return AnswerStatus.CONFLICTING
        refused = None if self.admission is None else self.admission.refused
        if refused is not None and refused.reason is not RefusalReason.EXCLUDED_BY_LIFECYCLE:
            return AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION
        return AnswerStatus.CANNOT_DETERMINE

    def _message(self, status: AnswerStatus, target: _Symbol, value: Value | None,
                 missing: tuple[MissingSymbol, ...]) -> str:
        name = target.name
        if status is AnswerStatus.CALCULATED:
            assert value is not None
            how = "supplied by the request (a direct answer)" if target.state is SymbolState.AVAILABLE \
                else f"calculated (step {target.from_step})"
            text = f"{name} {value.relation} {value.text}".rstrip()
            line = f"{text} - {how}; exact value {value.exact}"
            if target.conditional_on:
                line += f"; conditional on the assumption(s) {', '.join(target.conditional_on)}"
            if target.uncertain:
                line += "; depends on the admitted stored equation, labelled UNCERTAIN"
            return line + f"; verification {VERIFICATION_PENDING}."
        if status is AnswerStatus.CONFLICTING:
            shown = "; ".join(f"{v.relation} {v.text}".strip() for v in target.conflicting)
            return (f"Conflict: the methods for {name} disagree ({shown}). Resolution: not "
                    "automatically selected; no value is asserted.")
        parts = [f"Cannot determine {name} from the information supplied"]
        if status is AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION:
            parts = [f"Insufficient authorized information to determine {name}: the admitted "
                     f"equation {self.admission.refused.knowledge_id} was withheld "
                     f"({self.admission.refused.reason.value})"]
        for gap in missing:
            needed = ", ".join(f"`{t}`" for t in gap.required_by) or "the target itself"
            parts.append(f"Missing: {gap.symbol} — required by {needed}")
        if target.state is SymbolState.BLOCKED:
            reasons = [f"`{m.text}` {m.state.value} ({m.reason})" for m in target.methods]
            if target.cycle:
                reasons.insert(0, f"{name} is on the formula cycle {' -> '.join(target.cycle)}")
            if reasons:
                parts.append("Blocked: " + "; ".join(reasons))
        return ". ".join(parts) + ". Nothing was invented."

    def _effects(self, results: tuple[SymbolResult, ...]) -> tuple[AssumptionEffect, ...]:
        effects = []
        for symbol, (origin, quantity, _) in self.supplied.items():
            if origin is not Origin.ASSUMPTION:
                continue
            effects.append(AssumptionEffect(
                symbol=symbol,
                value=Value.of(quantity),
                symbols=tuple(r.symbol for r in results if symbol in r.conditional_on and r.symbol != symbol),
                formulas=tuple(sorted({m.formula for r in results for m in r.methods if symbol in m.conditional_on})),
            ))
        return tuple(effects)

    def _notes(self, results: tuple[SymbolResult, ...], withheld: Withheld) -> tuple[str, ...]:
        notes = [WRITES_NOTHING_NOTE, PENDING_NOTE, NOT_A_FACT_NOTE]
        if self.admission is None:
            notes.append("knowledge.db was not opened: the request admits no stored equation "
                         "(ADR 0043 P11-29).")
        else:
            admitted, refused = self.admission.admitted, self.admission.refused
            if admitted is not None:
                notes.append(
                    f"The admitted equation {admitted.knowledge.id} is document text labelled "
                    "UNCERTAIN (I-C). Its symbols are bound to the request's symbols by exact "
                    "text only; nothing stored says what they mean (ADR 0042, the weakest point)."
                )
            if refused is not None:
                notes.append(
                    f"The admitted item {refused.knowledge_id} was not admitted: "
                    f"{refused.reason.value}. It was not used (ADR 0042 P11-19)."
                )
        if any(r.conditional_on for r in results):
            used = self._ordered_assumptions(c for r in results for c in r.conditional_on)
            notes.append("Results marked conditional rest on the request's assumption(s) "
                         f"{', '.join(used)} (P11-21); they are not established without them.")
        if withheld != Withheld():
            notes.append(
                "Stored items without evidence from a source that is authorised and in the "
                f"requested scope ({self.request.scope.value}), or stored DELETED or ARCHIVED, "
                "were not used; they are counted under withheld (P9-5, P9-23)."
            )
        return tuple(notes)


# ---------------------------------------------------------------------- helpers


def _distinct(values: Iterable[Value | None]) -> tuple[Value, ...]:
    """The different values, in order: equal exact value and equal dimension are one."""
    seen: list[Value] = []
    for value in values:
        if value is not None and all((v.exact, v.dimension) != (value.exact, value.dimension) for v in seen):
            seen.append(value)
    return tuple(seen)


def _strongly_connected(
    start: str, successors: Callable[[str], tuple[str, ...]]
) -> tuple[list[str], list[list[str]]]:
    """Every symbol reached from `start` in first-reached order, and the strongly
    connected components in dependency order (each after everything it uses).

    Tarjan's algorithm, iterative, so a long chain of formulas cannot exhaust the stack.
    """
    index: dict[str, int] = {start: 0}
    low: dict[str, int] = {start: 0}
    stack, on_stack, order = [start], {start}, [start]
    work = [(start, iter(successors(start)))]
    components: list[list[str]] = []
    while work:
        node, pending = work[-1]
        advanced = False
        for successor in pending:
            if successor not in index:
                index[successor] = low[successor] = len(index)
                stack.append(successor)
                on_stack.add(successor)
                order.append(successor)
                work.append((successor, iter(successors(successor))))
                advanced = True
                break
            if successor in on_stack:
                low[node] = min(low[node], index[successor])
        if advanced:
            continue
        work.pop()
        if work:
            parent = work[-1][0]
            low[parent] = min(low[parent], low[node])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            components.append(component)
    return order, components


_PRECEDENCE = {Sum: 1, Product: 2, Negate: 3, Power: 4, Number: 5, Symbol: 5}
#: Operators as the trace shows them.
_SHOWN = {"+": "+", "-": "−", "*": "×", "/": "÷"}


def _render(expression: Expression, value_of: Callable[[str], Value], minimum: int = 0) -> str:
    """The expression with each symbol replaced by its value and unit (section 93)."""
    precedence = _PRECEDENCE[type(expression)]
    if isinstance(expression, Number):
        text = expression.text
    elif isinstance(expression, Symbol):
        value = value_of(expression.name)
        text = value.text
        if value.exact.startswith("-") or (minimum >= 5 and value.unit):
            text = f"({text})"
    elif isinstance(expression, Negate):
        text = "-" + _render(expression.operand, value_of, 4)
    elif isinstance(expression, Power):
        text = f"{_render(expression.base, value_of, 5)}^{expression.exponent}"
    else:
        first = _render(expression.first, value_of, precedence)
        rest = [f" {_SHOWN[op]} {_render(operand, value_of, precedence + 1)}"
                for op, operand in expression.rest]
        text = first + "".join(rest)
    return f"({text})" if precedence < minimum else text


def _has_sum(expression: Expression) -> bool:
    stack = [expression]
    while stack:
        node = stack.pop()
        if isinstance(node, Sum):
            return True
        if isinstance(node, Negate):
            stack.append(node.operand)
        elif isinstance(node, Power):
            stack.append(node.base)
        elif isinstance(node, Product):
            stack.extend([node.first, *(operand for _, operand in node.rest)])
    return False


def _dimension_check(expression: Expression, value: Value) -> str:
    """The dimensional check performed on a complete method (P11-16)."""
    unit = value.unit or "dimensionless"
    if _has_sum(expression):
        return f"PASSED: every + and − joins identical dimensions; the result is in {unit}"
    return f"PASSED: no + or −; × ÷ and powers combine dimensions; the result is in {unit}"


__all__ = ["ENGINE_NAME", "ENGINE_VERSION", "CalculationEngine"]
