"""Finding which stored equations lead from what is known to what is asked.

The documents state equations; a question states some quantities and asks for another.
The planner decides - with no help from anyone - which equations to use and in what order:

    known B  ->  equation 2 gives X  ->  equation 1 (needs A and X) gives C

An equation `V = I * R` can be used three ways: to find V from I and R, to find I from V and R
(`I = V / R`), or to find R from V and I (`R = V / I`). Those are its *forms*
(`forms_of`); every form has one output quantity and the quantities it needs.

The search is over forms, backwards from the asked quantity:

* a quantity the question gave is a leaf;
* any other quantity is found by one form whose inputs are all found in turn;
* a quantity is never found by a route that already needs it (no cycles), a stated equation
  is used at most once in a plan, and two forms never both produce the same quantity.

Everything that can be found is first computed by a forward pass (`reachable`), so the search
never follows a dead end, and it is bounded (depth, plans kept, total effort) - a bound that
was reached is reported, never hidden. Plans are ranked by fewest equations, then fewest
equations that were not stated exactly by the source (an equation read from a flattened PDF
text layer is *uncertain*), then fewest rearranged forms, then by the order the documents give
the equations - no score, no guess.
"""

from dataclasses import dataclass

from app.calculation.formulas import Formula, symbols_of
from app.models.identifiers import parse_id
from app.solving.library import Library, StoredEquation
from app.solving.normalize import Reading
from app.solving.rearrange import Refusal, isolate

MAX_DEPTH = 6
KEEP = 10
MAX_EFFORT = 60_000


@dataclass(frozen=True, slots=True)
class Form:
    """One stored equation, written to give one of its quantities."""

    equation: StoredEquation
    reading: Reading
    formula: Formula
    output: str
    inputs: tuple[str, ...]
    #: True when the stored equation was rearranged to give this quantity.
    rearranged: bool

    @property
    def identity(self) -> tuple[str, str]:
        return self.equation.knowledge_id, self.formula.text

    @property
    def statement(self) -> tuple[str, str]:
        """The statement this form is written from: one stored equation can state several
        (`P = V I = I^2 R`), and each is used at most once in a plan."""
        return self.equation.knowledge_id, self.reading.formula.text


@dataclass(frozen=True, slots=True)
class Plan:
    """Forms in the order they are applied: each needs only what is known or found before it."""

    steps: tuple[Form, ...]

    @property
    def uncertain(self) -> int:
        return sum(not step.equation.stated_by_source for step in self.steps)

    @property
    def rearranged(self) -> int:
        return sum(step.rearranged for step in self.steps)

    def rank(self) -> tuple:
        return (len(self.steps), self.uncertain, self.rearranged,
                tuple(parse_id(step.equation.knowledge_id)[1] for step in self.steps),
                tuple(step.formula.text for step in self.steps))


@dataclass(frozen=True, slots=True)
class Search:
    plans: tuple[Plan, ...]
    #: Cheapest number of equations by which each findable quantity can be found.
    reachable: dict[str, int]
    #: The search reached its effort bound; there may be plans it did not look for.
    truncated: bool = False


def forms_of(library: Library) -> list[Form]:
    """Every way the library's usable equations can be written to give one quantity."""
    forms: list[Form] = []
    for stored in library.usable:
        for reading in stored.readings:
            written = reading.formula
            needs = symbols_of(written.expression)
            if not needs:
                continue
            forms.append(Form(stored, reading, written, written.target, needs, False))
            for symbol in needs:
                other = isolate(written, symbol)
                if isinstance(other, Refusal):
                    continue
                forms.append(Form(stored, reading, other, symbol, symbols_of(other.expression), True))
    return forms


def reachable(forms: list[Form], known: set[str]) -> dict[str, int]:
    """Every quantity that can be found from `known`, with the fewest equations it takes."""
    cost = {symbol: 0 for symbol in known}
    changed = True
    while changed:
        changed = False
        for form in forms:
            if all(symbol in cost for symbol in form.inputs):
                total = 1 + sum(cost[symbol] for symbol in form.inputs)
                if form.output not in cost or total < cost[form.output]:
                    cost[form.output] = total
                    changed = True
    return cost


class _Effort:
    def __init__(self) -> None:
        self.used = 0

    @property
    def spent(self) -> bool:
        return self.used >= MAX_EFFORT


def _merge(first: tuple[Form, ...], second: tuple[Form, ...]) -> tuple[Form, ...] | None:
    merged = list(first)
    for form in second:
        if any(form.identity == other.identity for other in merged):
            continue
        if any(form.output == other.output for other in merged):
            return None  # two different ways of finding the same quantity
        if any(form.statement == other.statement for other in merged):
            return None  # one statement of an equation, used twice
        merged.append(form)
    return tuple(merged)


def search(forms: list[Form], known: set[str], target: str) -> Search:
    """Every plan that finds `target` from `known`, best first (module docstring)."""
    cost = reachable(forms, known)
    if target not in cost:
        return Search((), cost)
    by_output: dict[str, list[Form]] = {}
    for form in forms:
        if all(symbol in cost for symbol in form.inputs):
            by_output.setdefault(form.output, []).append(form)
    effort = _Effort()
    memo: dict[tuple[str, frozenset[str]], list[tuple[Form, ...]]] = {}

    def find(symbol: str, path: frozenset[str]) -> list[tuple[Form, ...]]:
        if symbol in known:
            return [()]
        if symbol in path or len(path) >= MAX_DEPTH:
            return []
        key = (symbol, path)
        if key in memo:
            return memo[key]
        found: list[tuple[Form, ...]] = []
        for form in by_output.get(symbol, ()):
            if effort.spent:
                break
            effort.used += 1
            if form.output in form.inputs:  # pragma: no cover - the grammar forbids it
                continue
            combos: list[tuple[Form, ...]] = [()]
            for needed in form.inputs:
                options = find(needed, path | {symbol})
                combined = []
                for have in combos:
                    for option in options:
                        merged = _merge(have, option)
                        if merged is not None:
                            combined.append(merged)
                        if len(combined) >= KEEP * 2:
                            break
                    if len(combined) >= KEEP * 2:
                        break
                combos = combined
                if not combos:
                    break
            for have in combos:
                if any(other.statement == form.statement for other in have):
                    continue
                found.append((*have, form))
        found.sort(key=lambda steps: Plan(steps).rank())
        memo[key] = found[:KEEP]
        return memo[key]

    plans = [Plan(steps) for steps in find(target, frozenset())]
    unique = {tuple(step.identity for step in plan.steps): plan for plan in plans}
    ranked = tuple(sorted(unique.values(), key=Plan.rank))
    return Search(ranked, cost, effort.spent)
