"""Exact evaluation of one parsed expression over bound quantities (ADR 0042 P11-13, P11-16).

This is the arithmetic foundation only. Which formulas are chained, in what order, and
what happens when a symbol has no value is formula resolution (ADR 0042 P11-17), a
later batch. Here every symbol the expression uses must already be bound. An unbound
symbol is an `EvaluationError`, never a guessed or zero value.

**Dimensional checks** (sections 92, 166): `+` and `-` require identical dimensions.
`*` and `/` combine them, and an integer power multiplies them. A mismatch raises
`EvaluationError` with problem `DIMENSION_MISMATCH`, naming the operation and both
operands' units (*"V + Ω"*). No number is produced.

**Evaluation errors, never values:** division by zero, and zero raised to a negative
power. **Guard** (section 173): a value whose numerator or denominator would exceed
`MAX_BITS` bits is refused as `TOO_LARGE`, reported and never truncated.
"""

from collections.abc import Mapping
from enum import StrEnum
from fractions import Fraction

from app.calculation.formulas import Expression, Negate, Number, Power, Product, Sum, Symbol
from app.calculation.units import DIMENSIONLESS, Dimension, Quantity, coherent_unit

MAX_BITS = 4096


class EvaluationProblem(StrEnum):
    DIMENSION_MISMATCH = "DIMENSION_MISMATCH"
    DIVISION_BY_ZERO = "DIVISION_BY_ZERO"
    ZERO_TO_NEGATIVE_POWER = "ZERO_TO_NEGATIVE_POWER"
    UNBOUND_SYMBOL = "UNBOUND_SYMBOL"
    TOO_LARGE = "TOO_LARGE"


class EvaluationError(ArithmeticError):
    """An expression that has no value, with the problem that prevents one."""

    def __init__(self, problem: EvaluationProblem, detail: str) -> None:
        super().__init__(detail)
        self.problem = problem


def evaluate(expression: Expression, bindings: Mapping[str, Quantity]) -> Quantity:
    """The exact value of `expression`, every symbol taken from `bindings`."""
    if isinstance(expression, Number):
        return Quantity(expression.value, DIMENSIONLESS)
    if isinstance(expression, Symbol):
        if expression.name not in bindings:
            raise EvaluationError(
                EvaluationProblem.UNBOUND_SYMBOL, f"The symbol {expression.name!r} has no value."
            )
        return bindings[expression.name]
    if isinstance(expression, Negate):
        operand = evaluate(expression.operand, bindings)
        return Quantity(-operand.value, operand.dimension)
    if isinstance(expression, Sum):
        return _sum(expression, bindings)
    if isinstance(expression, Product):
        return _product(expression, bindings)
    if isinstance(expression, Power):
        base = evaluate(expression.base, bindings)
        if base.value == 0 and expression.exponent < 0:
            raise EvaluationError(
                EvaluationProblem.ZERO_TO_NEGATIVE_POWER,
                f"Zero raised to the negative power {expression.exponent}.",
            )
        return _checked(base.value**expression.exponent, base.dimension.power(expression.exponent))
    raise TypeError(f"Not an expression: {expression!r}")


def _sum(expression: Sum, bindings: Mapping[str, Quantity]) -> Quantity:
    total = evaluate(expression.first, bindings)
    for op, term in expression.rest:
        right = evaluate(term, bindings)
        if right.dimension != total.dimension:
            raise EvaluationError(
                EvaluationProblem.DIMENSION_MISMATCH,
                f"Dimensionally inconsistent: {_unit(total)} {op} {_unit(right)}. Quantities "
                "added or subtracted must have the same dimension; no number is produced.",
            )
        value = total.value + right.value if op == "+" else total.value - right.value
        total = _checked(value, total.dimension)
    return total


def _product(expression: Product, bindings: Mapping[str, Quantity]) -> Quantity:
    result = evaluate(expression.first, bindings)
    for op, factor in expression.rest:
        right = evaluate(factor, bindings)
        if op == "*":
            result = _checked(result.value * right.value, result.dimension.times(right.dimension))
        else:
            if right.value == 0:
                raise EvaluationError(EvaluationProblem.DIVISION_BY_ZERO, "Division by zero.")
            result = _checked(result.value / right.value, result.dimension.over(right.dimension))
    return result


def _unit(value: Quantity) -> str:
    return coherent_unit(value.dimension) or "a dimensionless number"


def _checked(value: Fraction, dimension: Dimension) -> Quantity:
    """The value with its dimension, refused when it grows beyond `MAX_BITS` bits."""
    if max(value.numerator.bit_length(), value.denominator.bit_length()) > MAX_BITS:
        raise EvaluationError(
            EvaluationProblem.TOO_LARGE,
            f"An intermediate value needs more than {MAX_BITS} bits; the calculation is refused.",
        )
    return Quantity(value, dimension)
