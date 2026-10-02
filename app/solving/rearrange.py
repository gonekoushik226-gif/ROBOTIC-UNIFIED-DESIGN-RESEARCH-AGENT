"""Writing a stored equation for another one of its quantities.

A document states `V = I * R` once. A question that gives V and R and asks for I needs
`I = V / R`. This module derives that second form from the first - by undoing the
operations around the wanted quantity one by one - so that the one stored statement serves
every question it can answer. Nothing is guessed: the result is an algebraically identical
equation, and the answer says it was rearranged from the stored one.

**What can be rearranged.** The wanted quantity must occur exactly once in the equation, and
only the operations RUDRA's exact arithmetic can undo may stand between it and the equals
sign: `+`, `-`, `*`, `/`, a minus sign and a power of 1 or -1. A quantity that is squared
(`E = m * c^2`, asked for c) would need a square root, which exact rational arithmetic does
not have, so such an equation is reported as not rearrangeable rather than approximated. A
quantity that occurs twice (`x = a * x + b`) would need real algebra and is also refused.

The functions are pure: text in, text out. `isolate` returns the rearranged formula in the
formula grammar of `app.calculation`, or a `Refusal` saying why not.
"""

from dataclasses import dataclass

from app.calculation.formulas import (
    Expression,
    Formula,
    Negate,
    Number,
    Power,
    Product,
    Sum,
    Symbol,
    parse_formula,
    symbols_of,
)


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why an equation cannot be written for a quantity."""

    reason: str


def _number(text: str) -> Number:
    return parse_formula(f"x = {text}").expression  # type: ignore[return-value]


def _count(expression: Expression, name: str) -> int:
    stack, total = [expression], 0
    while stack:
        node = stack.pop()
        if isinstance(node, Symbol):
            total += node.name == name
        elif isinstance(node, Negate):
            stack.append(node.operand)
        elif isinstance(node, Power):
            stack.append(node.base)
        elif isinstance(node, (Sum, Product)):
            stack.extend([node.first, *(operand for _, operand in node.rest)])
    return total


def _contains(expression: Expression, name: str) -> bool:
    return _count(expression, name) > 0


# ------------------------------------------------------------------ writing a tree as text

_PRECEDENCE = {Sum: 1, Product: 2, Negate: 3, Power: 4, Number: 5, Symbol: 5}


def render(expression: Expression) -> str:
    """The expression in the formula grammar, with only the brackets that are needed."""
    if isinstance(expression, Number):
        return expression.text
    if isinstance(expression, Symbol):
        return expression.name
    if isinstance(expression, Negate):
        return "-" + _wrap(expression.operand, 4)
    if isinstance(expression, Power):
        return f"{_wrap(expression.base, 5)}^{expression.exponent}"
    if isinstance(expression, Sum):
        text = render(expression.first)
        for op, term in expression.rest:
            text += f" {op} {_wrap(term, 2)}"
        return text
    if isinstance(expression, Product):
        text = _wrap(expression.first, 2) if isinstance(expression.first, Sum) else render(expression.first)
        for op, factor in expression.rest:
            text += f" {op} {_wrap(factor, 4)}"
        return text
    raise TypeError(f"Not an expression: {expression!r}")


def _wrap(expression: Expression, minimum: int) -> str:
    text = render(expression)
    return f"({text})" if _PRECEDENCE[type(expression)] < minimum else text


# ------------------------------------------------------------------ building new trees


def _sum(terms: list[tuple[str, Expression]]) -> Expression:
    """Terms joined with their signs; a leading minus becomes a negation."""
    (sign, first), *rest = terms
    head = first if sign == "+" else Negate(first)
    return head if not rest else Sum(head, tuple(rest))


def _product(numerator: list[Expression], denominator: list[Expression]) -> Expression:
    top = numerator or [_number("1")]
    first, *more = top
    parts: list[tuple[str, Expression]] = [("*", f) for f in more] + [("/", d) for d in denominator]
    return first if not parts else Product(first, tuple(parts))


def _undo(node: Expression, name: str, value: Expression) -> Expression | Refusal:
    """The expression for `name` given that `node` equals `value`."""
    if isinstance(node, Symbol):
        return value
    if isinstance(node, Negate):
        return _undo(node.operand, name, Negate(value))
    if isinstance(node, Power):
        if node.exponent == 1:
            return _undo(node.base, name, value)
        if node.exponent == -1:
            return _undo(node.base, name, _product([], [value]))
        return Refusal(f"{name} is raised to the power {node.exponent}, and undoing that needs a root, which "
                       "exact arithmetic does not have")
    if isinstance(node, Sum):
        terms = [("+", node.first), *node.rest]
        at = next(i for i, (_, term) in enumerate(terms) if _contains(term, name))
        sign, term = terms[at]
        others = [("-" if s == "+" else "+", t) for i, (s, t) in enumerate(terms) if i != at]
        rest = _sum([("+", value), *others])
        return _undo(term, name, rest if sign == "+" else Negate(rest))
    if isinstance(node, Product):
        factors = [("*", node.first), *node.rest]
        at = next(i for i, (_, factor) in enumerate(factors) if _contains(factor, name))
        op, factor = factors[at]
        times = [f for i, (o, f) in enumerate(factors) if i != at and o == "*"]
        over = [f for i, (o, f) in enumerate(factors) if i != at and o == "/"]
        if op == "*":  # value = factor * times / over  ->  factor = value * over / times
            rest = _product([value, *over], times)
        else:          # value = times / (factor * over)  ->  factor = times / (value * over)
            rest = _product(times, [value, *over])
        return _undo(factor, name, rest)
    return Refusal(f"{name} is not inside an operation that can be undone")


def isolate(formula: Formula, name: str) -> Formula | Refusal:
    """`formula` written for `name` instead of its own target, or why that cannot be done."""
    if name == formula.target:
        return formula
    occurrences = _count(formula.expression, name)
    if occurrences == 0:
        return Refusal(f"{name} does not appear in {formula.text}")
    if occurrences > 1:
        return Refusal(f"{name} appears {occurrences} times in {formula.text}; separating it would need algebra "
                       "beyond undoing one operation at a time")
    found = _undo(formula.expression, name, Symbol(formula.target))
    if isinstance(found, Refusal):
        return found
    text = f"{name} = {render(found)}"
    try:
        written = parse_formula(text)
    except ValueError as error:  # the rearranged text must itself be inside the grammar
        return Refusal(f"the rearranged form {text!r} is outside the formula grammar ({error})")
    if name in symbols_of(written.expression):
        return Refusal(f"{name} would remain on both sides")
    return written
