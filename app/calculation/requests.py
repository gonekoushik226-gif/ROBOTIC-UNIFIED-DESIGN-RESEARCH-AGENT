"""The structured calculation request (ADR 0043 P11-28; ADR 0041 P11-4, P11-5; N2, N12).

Phase 11 parses no natural language (section 4; I-4): a calculation takes a typed request.
It states:

- the **target symbol**;
- zero or more **formulas**, each `SYMBOL = EXPRESSION` in the grammar of
  `app.calculation.formulas`;
- zero or more **inputs** (`USER_INPUT`) and **assumptions** (labelled `ASSUMPTION`,
  the U7(b) pattern), each a symbol with a value such as `10 Ω`;
- **at most one admission**: the identifier of one stored equation (`K-...`). N2 is
  recorded strictly: a request admitting more than one stored equation is invalid;
- the **source scope** (ADR 0035 P9-5).

`check` refuses what can be judged **without the database**, and returns the request's
parsed parts. That covers:

- a malformed field;
- a request formula outside the grammar, including a symbol the request's own symbols
  spell (`IR` with `I` and `R` supplied: implicit multiplication, P11-12);
- a value that is not a number with at most one known unit;
- a symbol supplied twice;
- two or more admissions;
- an admission identifier that is malformed or of the wrong kind.

The admitted equation's own target is known only once the equation is read, so the
later batch that reads it applies the same check again with that target added.
Whether the admitted object exists, is an `EQUATION`, and passes P9-5 and P9-23 is judged
against the database in a later batch. Every refusal is an `InvalidInputError`: an
invalid request, exit code 2 at the command line (ADR 0043 P11-29).
"""

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum

from app.calculation.formulas import Formula, FormulaError, is_symbol, parse_formula
from app.calculation.numbers import NumberError
from app.calculation.units import Quantity, UnitError, parse_quantity
from app.core.errors import InvalidInputError
from app.models.identifiers import EntityKind, is_valid_id


class CalculationScope(StrEnum):
    """Which sources' evidence an admitted equation may have: ADR 0035 P9-5, unchanged."""

    #: `USER_PROVIDED_SOURCE` or `LOCAL_SOURCE`, and `AUTHORIZED` (section 51).
    MY_BOOKS = "MY_BOOKS"
    #: Every `AUTHORIZED` source.
    AUTHORIZED = "AUTHORIZED"


#: At most one stored equation per request (N2; ADR 0041 P11-4).
MAX_ADMISSIONS = 1

_STAGE = "calculation.request"


def refuse(summary: str, reason: str) -> InvalidInputError:
    """An invalid calculation request (exit code 2); nothing was read or changed."""
    return InvalidInputError.of(
        summary,
        reason,
        stage=_STAGE,
        data_changed=False,
        retry_safe=True,
        next_options=(
            'CalculationRequest("I", formulas=("Rtotal = R1 + R2", "I = V / Rtotal"), '
            'inputs=(CalculationInput("R1", "10 Ω"), CalculationInput("R2", "20 Ω"), '
            'CalculationInput("V", "10 V")))',
            'CalculationRequest("I", formulas=("Rtotal = R1 + R2",), admissions=("K-00000001",), ...)',
        ),
    )


def _check_symbol(symbol: object, what: str) -> str:
    if not is_symbol(symbol):
        raise refuse(
            f"The {what} {symbol!r} is not a symbol.",
            "A symbol is a letter followed by letters, digits or '_', such as R1 or Rtotal.",
        )
    return symbol  # type: ignore[return-value]


def _check_value(symbol: str, value: object, what: str) -> Quantity:
    if not isinstance(value, str):
        raise refuse(f"The value of {what} {symbol!r} is not text.", f"Got {value!r}.")
    try:
        return parse_quantity(value)
    except UnitError as exc:
        raise refuse(f"The value of {what} {symbol!r} has an unusable unit ({exc.problem}).", str(exc)) from exc
    except NumberError as exc:
        raise refuse(f"The value of {what} {symbol!r} is not a number with a unit.", str(exc)) from exc


@dataclass(frozen=True, slots=True)
class CalculationInput:
    """A symbol the request supplies as given (`USER_INPUT`), with its value text.

    A `USER_INPUT` never becomes sourced knowledge; nothing is written (ADR 0041 P11-6).
    """

    symbol: str
    value: str

    def check(self) -> Quantity:
        return _check_value(_check_symbol(self.symbol, "input"), self.value, "input")


@dataclass(frozen=True, slots=True)
class CalculationAssumption:
    """A symbol the calculation proceeds as if it had this value (ADR 0042 P11-21).

    Labelled `ASSUMPTION`; every dependent result is conditional on it; never stored.
    """

    symbol: str
    value: str

    def check(self) -> Quantity:
        return _check_value(_check_symbol(self.symbol, "assumption"), self.value, "assumption")


@dataclass(frozen=True, slots=True)
class CheckedRequest:
    """A request that passed `check`, with its parts parsed, in the request's order."""

    request: "CalculationRequest"
    formulas: tuple[Formula, ...]
    inputs: tuple[tuple[str, Quantity], ...]
    assumptions: tuple[tuple[str, Quantity], ...]
    admission: str | None


@dataclass(frozen=True, slots=True)
class CalculationRequest:
    """One structured calculation request, towards one target symbol."""

    target: str
    formulas: tuple[str, ...] = ()
    inputs: tuple[CalculationInput, ...] = ()
    assumptions: tuple[CalculationAssumption, ...] = ()
    admissions: tuple[str, ...] = ()
    scope: CalculationScope = CalculationScope.MY_BOOKS

    def check(self) -> CheckedRequest:
        """Refuse a request that cannot be answered as asked; return its parsed parts."""
        if not isinstance(self.scope, CalculationScope):
            raise refuse("The source scope is not one of MY_BOOKS or AUTHORIZED.", f"Got {self.scope!r}.")
        _check_symbol(self.target, "target")
        inputs = self._values("inputs", CalculationInput)
        assumptions = self._values("assumptions", CalculationAssumption)
        supplied = [symbol for symbol, _ in (*inputs, *assumptions)]
        repeated = sorted({s for s in supplied if supplied.count(s) > 1})
        if repeated:
            raise refuse(
                f"The symbol(s) {', '.join(repeated)} are supplied more than once.",
                "Each symbol takes one value, as an input or as an assumption; RUDRA will not "
                "choose between two.",
            )
        formulas = self._formulas({self.target, *supplied})
        return CheckedRequest(self, formulas, inputs, assumptions, self._admission())

    def _formulas(self, supplied: set[str]) -> tuple[Formula, ...]:
        """Parse every formula twice: first for its target, then against every symbol.

        The second pass is what refuses a symbol the calculation's own symbols spell
        (`IR` with `I` and `R` known): implicit multiplication, never guessed (P11-12).
        """
        if not isinstance(self.formulas, tuple) or not all(isinstance(f, str) for f in self.formulas):
            raise refuse("The formulas are not a tuple of text.", f"Got {self.formulas!r}.")
        targets = {self._parse(text, ()).target for text in self.formulas}
        known = supplied | targets
        parsed: list[Formula] = []
        for text in self.formulas:
            formula = self._parse(text, known)
            if any(f.target == formula.target and f.expression == formula.expression for f in parsed):
                raise refuse(f"The formula {text!r} is given twice.", "Give each formula once.")
            parsed.append(formula)
        return tuple(parsed)

    @staticmethod
    def _parse(text: str, known: Collection[str]) -> Formula:
        try:
            return parse_formula(text, known)
        except FormulaError as exc:
            raise refuse(
                f"The formula {text!r} is outside the formula grammar ({exc.problem}).", str(exc)
            ) from exc

    def _values(self, name: str, kind: type) -> tuple[tuple[str, Quantity], ...]:
        values = getattr(self, name)
        if not isinstance(values, tuple) or not all(isinstance(v, kind) for v in values):
            raise refuse(f"The {name} are not a tuple of {kind.__name__} values.", f"Got {values!r}.")
        return tuple((value.symbol, value.check()) for value in values)

    def _admission(self) -> str | None:
        admissions = self.admissions
        if not isinstance(admissions, tuple) or not all(isinstance(a, str) for a in admissions):
            raise refuse("The admissions are not a tuple of identifiers.", f"Got {admissions!r}.")
        if len(admissions) > MAX_ADMISSIONS:
            raise refuse(
                f"The request admits {len(admissions)} stored equations.",
                "A calculation request may admit at most one stored equation (N2; ADR 0041 "
                "P11-4). Supply every other formula in the request.",
            )
        if not admissions:
            return None
        identifier = admissions[0]
        if not is_valid_id(identifier):
            raise refuse(
                f"The admitted item {identifier!r} is not an identifier.",
                f"An admission names one stored equation's knowledge object, "
                f"{EntityKind.KNOWLEDGE_OBJECT.prefix}-00000001 in form.",
            )
        if not is_valid_id(identifier, kind=EntityKind.KNOWLEDGE_OBJECT):
            raise refuse(
                f"The admitted item {identifier!r} is an identifier of the wrong kind.",
                "Only a knowledge object can be admitted, and it must be a stored equation "
                "(ADR 0042 P11-19).",
            )
        return identifier
