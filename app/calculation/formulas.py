"""The formula grammar and its whitelist parser (ADR 0042 P11-12; ADR 0041 P11-3; N1, N7).

A formula is **one symbol, `=`, and an expression**, the only form Phase 11 evaluates
(no symbolic solving, N1). An expression contains symbols, numeric literals, `+`, `-`,
`*`, `/`, parentheses, unary minus and integer-literal powers. The Unicode forms `−`,
`×`, `·` and `÷` are accepted and read as `-`, `*`, `*` and `/`.

    formula    := SYMBOL "=" expression
    expression := term (("+" | "-") term)*
    term       := unary (("*" | "/") unary)*
    unary      := "-" unary | power
    power      := primary ("^" ["-"] DIGITS)?
    primary    := NUMBER | SYMBOL | "(" expression ")"

The text is **parsed, never executed**: there is no `eval`, `exec`, `compile` or Python
`ast` here (`tests/unit/test_code_rules.py`). A stored equation's text is document
content (ADR 0023), and it goes through exactly the same whitelist.

**Refused, never guessed** (`FormulaError`, each with its `FormulaProblem`):

- implicit multiplication: two operands side by side (`2 V`, `2V`, `I R`, `2(a + b)`,
  `(a)(b)`), because `I R` could be a product or a typing slip (section 97);
- a symbol followed by `(`: there are no functions (`sqrt(x)`);
- `**`, unary `+`, any other operator or character;
- a relation other than one `=` (`≈`, `≅`, `∝`, `<`, `a = b = c`);
- a left-hand side that is not one symbol (`V / I = R`), and a target that also appears
  on the right (`x = 2 * x + 1`), because both would need solving;
- an exponent that is not an integer literal (`x^0.5`, `x^y`), and chained powers
  (`x^2^3`), whose grouping is ambiguous.

**A run of letters and digits is read as one symbol token**, such as `Rtotal` or `R1`.
The text alone cannot tell `IR` the symbol from `I × R`: every multi-letter name can be
split into letters. `Rtotal` shows that multi-letter symbols are real (section 203).
The ambiguity is therefore judged **against the symbols of the calculation**, passed as
`known_symbols`. A right-hand-side symbol that two or more known symbols, side by side,
spell exactly is refused as `IMPLICIT_MULTIPLICATION`: with `I` and `R` known, `V = IR` is
refused. It is refused **even when `IR` is itself known**, because it then has two
readings (section 97; ADR 0042 P11-12). A symbol the known symbols cannot compose is one
symbol. If it has no value, no number is ever produced from it.

**Numeric literals in a formula are dimensionless.** Units belong to input values, so a
letter in a formula is always a symbol, never a unit: `V` is a voltage, not the volt.

Guards, reported when reached and never silent (section 173): a formula of at most
`MAX_FORMULA_LENGTH` characters, at most `MAX_DEPTH` levels of parentheses and unary
minus, and exponents of magnitude at most `MAX_EXPONENT`.
"""

import re
from collections import deque
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import Union

from app.calculation.numbers import NUMBER_PATTERN, parse_number

#: The grammar's name and version, recorded in every trace (P6 §11; ADR 0042 P11-24).
GRAMMAR_NAME = "RUDRA formula grammar"
GRAMMAR_VERSION = "1"

MAX_FORMULA_LENGTH = 2000
MAX_DEPTH = 100
MAX_EXPONENT = 100


class FormulaProblem(StrEnum):
    MALFORMED = "MALFORMED"
    UNSUPPORTED_FORM = "UNSUPPORTED_FORM"
    IMPLICIT_MULTIPLICATION = "IMPLICIT_MULTIPLICATION"
    FUNCTION_CALL = "FUNCTION_CALL"
    UNSUPPORTED_OPERATOR = "UNSUPPORTED_OPERATOR"
    UNSUPPORTED_CHARACTER = "UNSUPPORTED_CHARACTER"
    NON_INTEGER_EXPONENT = "NON_INTEGER_EXPONENT"
    TOO_COMPLEX = "TOO_COMPLEX"


class FormulaError(ValueError):
    """Text outside the grammar, with the problem that makes it so."""

    def __init__(self, problem: FormulaProblem, detail: str) -> None:
        super().__init__(detail)
        self.problem = problem


# ------------------------------------------------------------------- the syntax tree


@dataclass(frozen=True, slots=True)
class Number:
    """A dimensionless numeric literal, exact, with the text it was read from."""

    value: Fraction
    text: str


@dataclass(frozen=True, slots=True)
class Symbol:
    name: str


@dataclass(frozen=True, slots=True)
class Negate:
    operand: "Expression"


@dataclass(frozen=True, slots=True)
class Sum:
    """`first` followed by `(op, term)` pairs, `op` being `+` or `-`, left to right."""

    first: "Expression"
    rest: tuple[tuple[str, "Expression"], ...]


@dataclass(frozen=True, slots=True)
class Product:
    """`first` followed by `(op, factor)` pairs, `op` being `*` or `/`, left to right."""

    first: "Expression"
    rest: tuple[tuple[str, "Expression"], ...]


@dataclass(frozen=True, slots=True)
class Power:
    base: "Expression"
    exponent: int


Expression = Union[Number, Symbol, Negate, Sum, Product, Power]


@dataclass(frozen=True, slots=True)
class Formula:
    """A parsed formula: its target symbol, its expression and the text it came from."""

    target: str
    expression: Expression
    text: str

    @property
    def symbols(self) -> tuple[str, ...]:
        """The symbols the expression uses, each once, in order of first appearance."""
        return symbols_of(self.expression)


def symbols_of(expression: Expression) -> tuple[str, ...]:
    found: dict[str, None] = {}
    stack = [expression]
    while stack:
        node = stack.pop()
        if isinstance(node, Symbol):
            found.setdefault(node.name, None)
        elif isinstance(node, Negate):
            stack.append(node.operand)
        elif isinstance(node, Power):
            stack.append(node.base)
        elif isinstance(node, (Sum, Product)):
            stack.extend(reversed([node.first, *(operand for _, operand in node.rest)]))
    return tuple(found)


# -------------------------------------------------------------------------- tokens

#: Operators and their canonical spelling.
_OPERATORS = {"+": "+", "-": "-", "−": "-", "*": "*", "×": "*", "·": "*", "/": "/",
              "÷": "/", "^": "^", "(": "(", ")": ")", "=": "="}
#: Relations other than `=`: each makes the formula an unsupported form.
_RELATIONS = ("≈", "≅", "∝", "≤", "≥", "≠", "<", ">", "~")
_NUMBER = re.compile(NUMBER_PATTERN)
_DIGITS = re.compile(r"[0-9]+")


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str  # NUMBER, SYMBOL, OP, RELATION, END
    text: str
    position: int


def _is_symbol_start(character: str) -> bool:
    return character.isalpha()


def _is_symbol_part(character: str) -> bool:
    return character.isalnum() or character == "_"


def is_symbol(text: object) -> bool:
    """Whether `text` is exactly one symbol: a letter, then letters, digits or `_`."""
    return (
        isinstance(text, str)
        and bool(text)
        and _is_symbol_start(text[0])
        and all(_is_symbol_part(c) for c in text[1:])
    )


def _tokens(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    while i < len(text):
        character = text[i]
        if character.isspace():
            i += 1
            continue
        number = _NUMBER.match(text, i) if character in "0123456789" else None
        if number is not None:
            tokens.append(_Token("NUMBER", number.group(), i))
            i = number.end()
        elif _is_symbol_start(character):
            j = i + 1
            while j < len(text) and _is_symbol_part(text[j]):
                j += 1
            tokens.append(_Token("SYMBOL", text[i:j], i))
            i = j
        elif text.startswith("**", i):
            raise FormulaError(
                FormulaProblem.UNSUPPORTED_OPERATOR,
                f"'**' at position {i} is not an operator here; powers are written with '^'.",
            )
        elif character in _OPERATORS:
            tokens.append(_Token("OP", _OPERATORS[character], i))
            i += 1
        elif character in _RELATIONS:
            tokens.append(_Token("RELATION", character, i))
            i += 1
        else:
            raise FormulaError(
                FormulaProblem.UNSUPPORTED_CHARACTER,
                f"{character!r} at position {i} is not part of the formula grammar.",
            )
    tokens.append(_Token("END", "", len(text)))
    return tokens


# -------------------------------------------------------------------------- parser


def composition(symbol: str, known_symbols: Collection[str]) -> tuple[str, ...] | None:
    """A reading of `symbol` as two or more known symbols side by side, if one exists.

    Deterministic: the reading with the fewest parts, trying shorter known symbols first
    and ties in text order. `None` when no such reading exists.
    """
    parts = sorted(set(known_symbols), key=lambda s: (len(s), s))
    queue: deque[tuple[int, tuple[str, ...]]] = deque([(0, ())])
    reached = {0}
    while queue:
        position, so_far = queue.popleft()
        for part in parts:
            if not part or not symbol.startswith(part, position):
                continue
            end = position + len(part)
            if end == len(symbol):
                if so_far:  # at least two parts; one part would be the symbol itself
                    return (*so_far, part)
            elif end not in reached:
                reached.add(end)
                queue.append((end, (*so_far, part)))
    return None


def parse_formula(text: str, known_symbols: Collection[str] = ()) -> Formula:
    """Parse `SYMBOL = EXPRESSION`, or raise `FormulaError` naming the problem.

    `known_symbols` are the symbols of the calculation this formula belongs to: its
    target, inputs, assumptions and formula targets. A right-hand-side symbol they
    compose is refused as implicit multiplication (see the module docstring). A caller
    evaluating a request must pass them; with none, no such reading can be found.
    """
    if not isinstance(text, str) or not text.strip():
        raise FormulaError(FormulaProblem.MALFORMED, "A formula needs text: SYMBOL = EXPRESSION.")
    if len(text) > MAX_FORMULA_LENGTH:
        raise FormulaError(
            FormulaProblem.TOO_COMPLEX,
            f"The formula is {len(text)} characters long; the limit is {MAX_FORMULA_LENGTH}.",
        )
    tokens = _tokens(text)
    relation = next((t for t in tokens if t.kind == "RELATION"), None)
    if relation is not None:
        raise FormulaError(
            FormulaProblem.UNSUPPORTED_FORM,
            f"{relation.text!r} is not '='; only an equality SYMBOL = EXPRESSION is evaluated.",
        )
    equals = [t for t in tokens if t.kind == "OP" and t.text == "="]
    if not equals:
        raise FormulaError(FormulaProblem.MALFORMED, "A formula has the form SYMBOL = EXPRESSION; there is no '='.")
    if len(equals) > 1:
        raise FormulaError(
            FormulaProblem.UNSUPPORTED_FORM,
            "A formula has exactly one '='; a chain of equalities is not evaluated.",
        )
    split = tokens.index(equals[0])
    left = tokens[:split]
    if not left:
        raise FormulaError(FormulaProblem.MALFORMED, "Nothing stands before '='; a formula names its target first.")
    if len(left) != 1 or left[0].kind != "SYMBOL":
        raise FormulaError(
            FormulaProblem.UNSUPPORTED_FORM,
            "The left-hand side must be one symbol, the formula's target; rearranging an "
            "equation is symbolic solving, which this grammar does not do.",
        )
    target = left[0].text
    parser = _Parser(tokens[split + 1:])
    expression = parser.formula_right_side()
    if target in symbols_of(expression):
        raise FormulaError(
            FormulaProblem.UNSUPPORTED_FORM,
            f"The target {target!r} also appears on the right-hand side; it is not isolated, "
            "and finding it would need solving.",
        )
    for name in symbols_of(expression):
        parts = composition(name, known_symbols)
        if parts is not None:
            raise FormulaError(
                FormulaProblem.IMPLICIT_MULTIPLICATION,
                f"{name!r} could be read as {' × '.join(parts)}: the calculation's own symbols "
                f"spell it. Implicit multiplication is never guessed; write {' * '.join(parts)}, "
                "or give the symbol a name its parts do not spell.",
            )
    return Formula(target=target, expression=expression, text=text)


class _Parser:
    """Recursive descent over the tokens after `=`.

    Nesting, counted as open parentheses plus stacked unary minus signs, is limited to
    `MAX_DEPTH`; chains of `+`, `-`, `*` and `/` are read in loops and add no depth.
    """

    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._i = 0
        self._depth = 0

    def _peek(self) -> _Token:
        return self._tokens[self._i]

    def _take(self) -> _Token:
        token = self._tokens[self._i]
        self._i += 1
        return token

    def _is_op(self, *texts: str) -> bool:
        token = self._peek()
        return token.kind == "OP" and token.text in texts

    def _enter(self) -> None:
        self._depth += 1
        if self._depth > MAX_DEPTH:
            raise FormulaError(
                FormulaProblem.TOO_COMPLEX, f"The formula nests more than {MAX_DEPTH} levels deep."
            )

    def formula_right_side(self) -> Expression:
        if self._peek().kind == "END":
            raise FormulaError(FormulaProblem.MALFORMED, "The right-hand side is empty.")
        expression = self._expression()
        token = self._peek()
        if token.kind != "END":
            raise self._unexpected(token)
        return expression

    def _expression(self) -> Expression:
        first = self._term()
        rest: list[tuple[str, Expression]] = []
        while self._is_op("+", "-"):
            rest.append((self._take().text, self._term()))
        return first if not rest else Sum(first, tuple(rest))

    def _term(self) -> Expression:
        first = self._unary()
        rest: list[tuple[str, Expression]] = []
        while self._is_op("*", "/"):
            rest.append((self._take().text, self._unary()))
        return first if not rest else Product(first, tuple(rest))

    def _unary(self) -> Expression:
        if self._is_op("-"):
            self._take()
            self._enter()
            try:
                return Negate(self._unary())
            finally:
                self._depth -= 1
        if self._is_op("+"):
            token = self._peek()
            raise FormulaError(
                FormulaProblem.UNSUPPORTED_OPERATOR,
                f"Unary '+' at position {token.position} is not supported; only unary minus is.",
            )
        return self._power()

    def _power(self) -> Expression:
        base = self._primary()
        if not self._is_op("^"):
            return base
        self._take()
        negative = False
        if self._is_op("-"):
            self._take()
            negative = True
        token = self._take()
        if token.kind != "NUMBER" or _DIGITS.fullmatch(token.text) is None:
            raise FormulaError(
                FormulaProblem.NON_INTEGER_EXPONENT,
                f"The exponent at position {token.position} must be an integer literal, such as "
                "2 or -1; roots and symbolic exponents are not supported.",
            )
        exponent = int(token.text)
        if exponent > MAX_EXPONENT:
            raise FormulaError(
                FormulaProblem.TOO_COMPLEX,
                f"The exponent {exponent} exceeds the limit of {MAX_EXPONENT}.",
            )
        if self._is_op("^"):
            raise FormulaError(
                FormulaProblem.UNSUPPORTED_FORM,
                f"Chained powers at position {self._peek().position} are ambiguous; use parentheses.",
            )
        self._no_adjacent_operand()
        return Power(base, -exponent if negative else exponent)

    def _primary(self) -> Expression:
        token = self._take()
        if token.kind == "NUMBER":
            node: Expression = Number(parse_number(token.text), token.text)
        elif token.kind == "SYMBOL":
            if self._is_op("("):
                raise FormulaError(
                    FormulaProblem.FUNCTION_CALL,
                    f"{token.text!r} is followed by '('; functions are not supported, and "
                    "multiplication is written with an explicit operator.",
                )
            node = Symbol(token.text)
        elif token.kind == "OP" and token.text == "(":
            self._enter()
            try:
                if self._is_op(")"):
                    raise FormulaError(FormulaProblem.MALFORMED, f"Empty parentheses at position {token.position}.")
                node = self._expression()
            finally:
                self._depth -= 1
            closing = self._take()
            if not (closing.kind == "OP" and closing.text == ")"):
                raise FormulaError(
                    FormulaProblem.MALFORMED,
                    f"The '(' at position {token.position} is not closed.",
                )
        else:
            raise self._unexpected(token)
        self._no_adjacent_operand()
        return node

    def _no_adjacent_operand(self) -> None:
        """Refuse an operand directly after an operand: implicit multiplication."""
        following = self._peek()
        if following.kind in ("NUMBER", "SYMBOL") or (following.kind == "OP" and following.text == "("):
            raise FormulaError(
                FormulaProblem.IMPLICIT_MULTIPLICATION,
                f"Two operands stand side by side at position {following.position}; write the "
                "operator (for example '*') - implicit multiplication is never guessed.",
            )

    @staticmethod
    def _unexpected(token: _Token) -> FormulaError:
        if token.kind == "END":
            return FormulaError(FormulaProblem.MALFORMED, "The formula ends where an operand was expected.")
        return FormulaError(
            FormulaProblem.MALFORMED, f"Unexpected {token.text!r} at position {token.position}."
        )
