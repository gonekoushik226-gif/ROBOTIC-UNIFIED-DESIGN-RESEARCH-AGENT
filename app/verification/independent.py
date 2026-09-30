"""A second, independent evaluator for calculation steps (ADR 0044 P12-10, check 2).

The calculation engine evaluates a parsed tree with exact rationals and its own
dimension vectors (`app.calculation`). This module re-evaluates each recorded step by a
**separate path**: its own tokenizer, a shunting-yard conversion to postfix, arithmetic in
`decimal.Decimal` at `PRECISION` significant digits, and its own propagation of SI base
exponents. It imports nothing from `app.calculation`, so a fault in one is not silently
shared by the other.

A step agrees when the independent value is within a relative difference of `TOLERANCE`
of the recorded exact value (both zero counts as agreement) and the dimensions are equal.
Anything the evaluator cannot read or compute is a disagreement, never a pass. The text
is parsed, never executed: there is no `eval`, `exec` or `compile` here.

**What independence does not cover:** both paths read the same formula and the same
inputs, so a formula that was wrong in its source is not detected (PHASE_12.md §7).
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext

PRECISION = 50
TOLERANCE = Decimal("1e-40")

_TOKEN = re.compile(
    r"\s*(?:(?P<number>[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)"
    r"|(?P<symbol>[^\W\d_][\w]*)"
    r"|(?P<op>[-+*/^()−×·÷]))"
)
_CANONICAL = {"−": "-", "×": "*", "·": "*", "÷": "/"}
_BINARY = {"+": 1, "-": 1, "*": 2, "/": 2, "^": 4}
_NEGATE = "neg"
_NEGATE_PRECEDENCE = 3


class IndependentError(ValueError):
    """The step could not be evaluated independently; never treated as agreement."""


@dataclass(frozen=True, slots=True)
class Operand:
    value: Decimal
    dimension: tuple[int, ...]


def exact_to_decimal(exact: str) -> Decimal:
    """The Decimal of a recorded exact rational (`30`, `1/3`, `-7/2`) at PRECISION digits."""
    with localcontext() as context:
        context.prec = PRECISION
        numerator, _, denominator = exact.partition("/")
        try:
            value = Decimal(numerator)
            return value / Decimal(denominator) if denominator else +value
        except (InvalidOperation, ZeroDivisionError) as exc:
            raise IndependentError(f"{exact!r} is not an exact rational") from exc


def _tokens(expression: str) -> list[tuple[str, str]]:
    tokens, position = [], 0
    while position < len(expression):
        if expression[position:].strip() == "":
            break
        match = _TOKEN.match(expression, position)
        if match is None or match.end() == position:
            raise IndependentError(f"unreadable text at position {position}: {expression[position:]!r}")
        kind = match.lastgroup
        text = match.group(kind)
        tokens.append((kind, _CANONICAL.get(text, text)))
        position = match.end()
    return tokens


def _postfix(tokens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Shunting-yard. Unary minus binds looser than `^` and tighter than `*` and `/`."""
    output: list[tuple[str, str]] = []
    stack: list[str] = []
    expect_operand = True
    for kind, text in tokens:
        if kind in ("number", "symbol"):
            if not expect_operand:
                raise IndependentError("two operands side by side")
            output.append((kind, text))
            expect_operand = False
        elif text == "(":
            if not expect_operand:
                raise IndependentError("an operand directly before '('")
            stack.append(text)
        elif text == ")":
            while stack and stack[-1] != "(":
                output.append(("op", stack.pop()))
            if not stack:
                raise IndependentError("unbalanced ')'")
            stack.pop()
            expect_operand = False
        elif text == "-" and expect_operand:
            stack.append(_NEGATE)
        else:
            if expect_operand:
                raise IndependentError(f"operator {text!r} without a left operand")
            precedence = _BINARY[text]
            while stack and stack[-1] != "(":
                top = stack[-1]
                top_precedence = _NEGATE_PRECEDENCE if top == _NEGATE else _BINARY[top]
                if top_precedence > precedence or (top_precedence == precedence and text != "^"):
                    output.append(("op", stack.pop()))
                else:
                    break
            stack.append(text)
            expect_operand = True
    if expect_operand:
        raise IndependentError("the expression ends where an operand was expected")
    while stack:
        top = stack.pop()
        if top == "(":
            raise IndependentError("unbalanced '('")
        output.append(("op", top))
    return output


def evaluate(formula: str, inputs: dict[str, Operand]) -> Operand:
    """The value and dimension of a formula's right-hand side, independently."""
    target, separator, expression = formula.partition("=")
    if not separator or "=" in expression:
        raise IndependentError("a formula has exactly one '='")
    stack: list[Operand] = []
    with localcontext() as context:
        context.prec = PRECISION
        for kind, text in _postfix(_tokens(expression)):
            if kind == "number":
                stack.append(Operand(Decimal(text), (0,) * 7))
            elif kind == "symbol":
                if text not in inputs:
                    raise IndependentError(f"no recorded input for {text!r}")
                stack.append(inputs[text])
            elif text == _NEGATE:
                if not stack:
                    raise IndependentError("unary minus without an operand")
                operand = stack.pop()
                stack.append(Operand(-operand.value, operand.dimension))
            else:
                if len(stack) < 2:
                    raise IndependentError(f"operator {text!r} lacks an operand")
                right, left = stack.pop(), stack.pop()
                stack.append(_apply(text, left, right))
        if len(stack) != 1:
            raise IndependentError("the expression does not reduce to one value")
        return Operand(+stack[0].value, stack[0].dimension)


def _apply(op: str, left: Operand, right: Operand) -> Operand:
    if op in "+-":
        if left.dimension != right.dimension:
            raise IndependentError("addition or subtraction of different dimensions")
        return Operand(left.value + right.value if op == "+" else left.value - right.value, left.dimension)
    if op == "*":
        return Operand(left.value * right.value, tuple(a + b for a, b in zip(left.dimension, right.dimension)))
    if op == "/":
        if right.value == 0:
            raise IndependentError("division by zero")
        return Operand(left.value / right.value, tuple(a - b for a, b in zip(left.dimension, right.dimension)))
    if any(right.dimension) or right.value != right.value.to_integral_value():
        raise IndependentError("an exponent must be a dimensionless integer")
    exponent = int(right.value)
    if left.value == 0 and exponent < 0:
        raise IndependentError("zero raised to a negative power")
    return Operand(left.value ** exponent, tuple(a * exponent for a in left.dimension))


def agrees(found: Operand, exact: str, dimension: tuple[int, ...]) -> tuple[bool, str]:
    """Whether an independent result agrees with a recorded exact value and dimension."""
    expected = exact_to_decimal(exact)
    if tuple(found.dimension) != tuple(dimension):
        return False, f"dimension {found.dimension} independently, {tuple(dimension)} recorded"
    with localcontext() as context:
        context.prec = PRECISION
        if expected == 0:
            ok = abs(found.value) <= TOLERANCE
        else:
            ok = abs(found.value - expected) / abs(expected) <= TOLERANCE
    shown = f"{found.value:.15g}"
    if ok:
        return True, f"independently {shown}, agreeing with the recorded exact value {exact}"
    return False, f"independently {shown}, but {exact} was recorded"
