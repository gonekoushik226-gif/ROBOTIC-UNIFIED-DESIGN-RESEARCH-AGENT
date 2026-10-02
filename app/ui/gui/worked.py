"""A calculation, set out as a worked solution: the result, what was given, each step.

`ask --json` returns a calculation twice: as plain lines ("Step 1: Rtotal = R1 + R2 →
Rtotal = 10 Ω + 20 Ω = 30 Ω") and as the command's own structured answer (`detail`). The
plain lines stay what the command line prints and what is copied. This module reads the
structured answer and prepares what the window typesets: for each step the equation, the
equation with its values put in, and the result - written for `mathrender` to draw as a
textbook would (fractions stacked, units upright, subscripts lowered).

This is presentation only. Every number, unit and symbol shown is one the command
returned; nothing is recomputed, rounded again or inferred here. A substitution this module
cannot read is shown as the command wrote it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ------------------------------------------------------------------ the substitution text
#
# The calculation engine writes a substitution from a fixed grammar:
#
#   NAME = EXPRESSION        EXPRESSION: sums, products (×, ÷), integer powers (^n),
#                            parentheses, a number written bare, or a value with its unit
#                            ("10 Ω", "0.4 A", "-5 V", "1.5e7 Hz").

_NUMBER = r"[-−]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
_TOKEN = re.compile(rf"\s*(?:(?P<quantity>(?P<number>{_NUMBER})(?:\s+(?P<unit>[^\W\d_][^\s()^+×÷−-]*))?)"
                    r"|(?P<operator>[+−×÷^])|(?P<minus>-)|(?P<open>\()|(?P<close>\)))")


class _Unreadable(Exception):
    """The text is not in the engine's substitution grammar."""


def _tokens(text: str) -> list[tuple[str, str, str | None]]:
    tokens: list[tuple[str, str, str | None]] = []
    position = 0
    text = text.strip()
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None or match.end() == position:
            raise _Unreadable(text[position:])
        if match.group("quantity") is not None:
            tokens.append(("quantity", match.group("number"), match.group("unit")))
        elif match.group("operator") is not None:
            tokens.append(("operator", match.group("operator"), None))
        elif match.group("minus") is not None:
            tokens.append(("operator", "−", None))
        elif match.group("open") is not None:
            tokens.append(("open", "(", None))
        else:
            tokens.append(("close", ")", None))
        position = match.end()
    return tokens


@dataclass(frozen=True)
class _Node:
    kind: str  # quantity, negate, binary, power, group
    text: str = ""
    unit: str | None = None
    operator: str = ""
    left: "_Node | None" = None
    right: "_Node | None" = None


class _Reader:
    def __init__(self, tokens: list[tuple[str, str, str | None]]):
        self.tokens = tokens
        self.position = 0

    def peek(self) -> tuple[str, str, str | None] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> tuple[str, str, str | None]:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def expression(self) -> _Node:
        node = self.term()
        while (token := self.peek()) is not None and token[0] == "operator" and token[1] in "+−":
            self.take()
            node = _Node("binary", operator=token[1], left=node, right=self.term())
        return node

    def term(self) -> _Node:
        node = self.factor()
        while (token := self.peek()) is not None and token[0] == "operator" and token[1] in "×÷":
            self.take()
            node = _Node("binary", operator=token[1], left=node, right=self.factor())
        return node

    def factor(self) -> _Node:
        token = self.peek()
        if token is None:
            raise _Unreadable("")
        if token[0] == "operator" and token[1] == "−":
            self.take()
            return _Node("negate", left=self.factor())
        node = self.atom()
        if (token := self.peek()) is not None and token[0] == "operator" and token[1] == "^":
            self.take()
            sign = ""
            if (token := self.peek()) is not None and token[0] == "operator" and token[1] == "−":
                self.take()
                sign = "-"
            exponent = self.peek()
            if exponent is None or exponent[0] != "quantity" or exponent[2] is not None:
                raise _Unreadable("^")
            self.take()
            node = _Node("power", text=sign + exponent[1], left=node)
        return node

    def atom(self) -> _Node:
        token = self.take()
        if token[0] == "quantity":
            return _Node("quantity", text=token[1], unit=token[2])
        if token[0] == "open":
            inner = self.expression()
            closing = self.peek()
            if closing is None or closing[0] != "close":
                raise _Unreadable("(")
            self.take()
            return _Node("group", left=inner)
        raise _Unreadable(token[1])


def _number(text: str) -> str:
    """A number as markup: `1.5e7` becomes 1.5 × 10⁷, a leading `-` a true minus sign."""
    sign = "-" if text[:1] in "-−" else ""
    body = text.lstrip("-−")
    match = re.fullmatch(r"([0-9.]+)[eE]([+-]?[0-9]+)", body)
    if match:
        exponent = int(match.group(2))
        return f"{sign}{match.group(1)} \\times 10^{{{exponent}}}"
    whole, point, fraction = body.partition(".")
    if len(whole) > 4:  # SI style: 20 000, not 20000 (four digits stay together: 1500)
        groups = []
        while whole:
            groups.insert(0, whole[-3:])
            whole = whole[:-3]
        whole = "\\,".join(groups)
    return sign + whole + point + fraction


def _unit(unit: str) -> str:
    return "\\mathrm{" + unit.replace("µ", "μ") + "}"


def _quantity(node: _Node) -> str:
    text = _number(node.text)
    return text if not node.unit else f"{text}\\,{_unit(node.unit)}"


def _emit(node: _Node, *, bare: bool = False) -> str:
    """Markup for `node`. `bare` drops the parentheses of a group that a fraction or a
    product already sets apart (they would only add noise to a stacked fraction)."""
    if node.kind == "quantity":
        return _quantity(node)
    if node.kind == "negate":
        return "-" + _emit(node.left)
    if node.kind == "group":
        inner = _emit(node.left, bare=True)
        if bare or _is_fraction(node.left):
            return inner
        return f"\\left({inner}\\right)"
    if node.kind == "power":
        base = node.left
        # The exponent belongs to the whole base: (a ÷ b)² keeps its brackets.
        shown = f"\\left({_emit(base.left, bare=True)}\\right)" if base.kind == "group" else _emit(base)
        return f"{shown}^{{{node.text}}}"
    left, right = node.left, node.right
    if node.operator == "÷":
        return f"\\frac{{{_emit(left, bare=True)}}}{{{_emit(right, bare=True)}}}"
    if node.operator == "×":
        return f"{_emit(left)} \\times {_emit(right)}"
    return f"{_emit(left)} {node.operator} {_emit(right)}"


def _is_fraction(node: _Node) -> bool:
    return node.kind == "binary" and node.operator == "÷"


def expression_markup(text: str) -> str:
    """The right-hand side of a substitution as markup; `_Unreadable` when it is not one."""
    reader = _Reader(_tokens(text))
    node = reader.expression()
    if reader.peek() is not None:
        raise _Unreadable(text)
    return _emit(node)


def substitution_markup(substitution: str, result: str = "") -> str:
    """`T = 1 ÷ 50 Hz` (and the result `0.02 s`) as markup: T = 1/(50 Hz) = 0.02 s.

    Falls back to the text exactly as the command wrote it when it is not in the engine's
    grammar. `result` is appended as a further `= ...` when given.
    """
    name, separator, right = substitution.partition(" = ")
    try:
        if not separator:
            raise _Unreadable(substitution)
        markup = f"{name.strip()} = {expression_markup(right)}"
        if result:
            markup += f" = {value_markup(result)}"
        return markup
    except _Unreadable:
        return substitution + (f" = {result}" if result else "")


def value_markup(text: str) -> str:
    """`0.4 A`, `-5 V`, `1.5e7 Hz`, `10` as markup (the unit upright, a thin space before it)."""
    match = re.fullmatch(rf"\s*(?P<number>{_NUMBER})(?:\s+(?P<unit>\S+))?\s*", text)
    if match is None:
        return text
    return _quantity(_Node("quantity", text=match.group("number"), unit=match.group("unit")))


def equation_markup(equation: str) -> str:
    """A formula as the command wrote it (`T = 1 / f`), with its symbols as they are."""
    return equation.strip()


# ------------------------------------------------------------------ the worked solution


@dataclass(frozen=True)
class Given:
    symbol: str
    #: `12 V`, as returned.
    value: str
    #: What the documents call the quantity ("voltage"), when they say.
    name: str = ""


@dataclass(frozen=True)
class Step:
    number: int
    #: The equation as used (rearranged when it was turned around), as markup.
    equation: str
    #: The equation as the document states it, when the step turned it around; else "".
    stored: str
    #: The equation with its values put in and the step's result, as markup.
    working: str
    #: What the step's symbol is called in the documents, when they say.
    name: str = ""
    #: The symbol the step calculates.
    symbol: str = ""


@dataclass(frozen=True)
class Result:
    symbol: str
    relation: str
    value: str
    unit: str
    #: The exact value (`1/3`), shown only when the displayed one is rounded.
    exact: str = ""
    name: str = ""

    def markup(self) -> str:
        shown = value_markup(f"{self.value} {self.unit}".strip())
        return f"{self.symbol} {self.relation} {shown}"


@dataclass(frozen=True)
class Worked:
    result: Result
    givens: tuple[Given, ...]
    steps: tuple[Step, ...]
    #: "Checked: an independent evaluation reproduced every step", or the failure to say so.
    check: str = ""
    check_ok: bool = True


def _names(detail: dict) -> dict[str, str]:
    return {str(symbol): str(name) for symbol, name in detail.get("quantities") or () if symbol and name}


def _result(symbol: str, raw: dict, names: dict[str, str]) -> Result:
    relation = str(raw.get("relation") or "=")
    exact = str(raw.get("exact") or "") if relation == "≈" else ""
    return Result(symbol, relation, str(raw.get("displayed", "?")), str(raw.get("unit") or ""), exact,
                  names.get(symbol, ""))


_VERIFICATION = {
    "VERIFIED": ("Checked: an independent evaluation reproduced every step.", True),
    "INCONCLUSIVE": ("Not independently confirmed: the value is the one you gave.", True),
    "FAILED": ("The independent check FAILED - do not rely on this result.", False),
}


def from_part(raw: dict) -> Worked | None:
    """The worked solution of a CALCULATED `solve` or `calculate` answer, else None."""
    command = raw.get("command") or ()
    detail = raw.get("detail")
    if not command or command[0] not in ("solve", "calculate") or not isinstance(detail, dict):
        return None
    if detail.get("status") != "CALCULATED" or not isinstance(detail.get("result"), dict):
        return None
    try:
        return _solve(detail) if command[0] == "solve" else _calculate(detail)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _solve(detail: dict) -> Worked | None:
    names = _names(detail)
    target = detail.get("target") or {}
    symbol = str(target.get("symbol") or target.get("asked") or "")
    if not symbol:
        return None
    givens = tuple(Given(str(g["symbol"]), str(g["value"]), names.get(str(g["symbol"]), ""))
                   for g in detail.get("givens") or () if g.get("symbol") and g.get("used", True))
    steps = []
    for step in detail.get("steps") or ():
        result = step["result"]
        shown = f"{result.get('displayed', '?')} {result.get('unit') or ''}".strip()
        stored = ""
        if step.get("rearranged") and step.get("stored_text"):
            stored = " ".join(str(step["stored_text"]).split())
        steps.append(Step(int(step["number"]), equation_markup(str(step["formula"])), stored,
                          substitution_markup(str(step["substitution"]), shown),
                          names.get(str(step.get("symbol", "")), ""), str(step.get("symbol", ""))))
    check, ok = _VERIFICATION.get(str(detail.get("verification")), ("Not independently checked.", True))
    return Worked(_result(symbol, detail["result"], names), givens, tuple(steps), check, ok)


def _calculate(detail: dict) -> Worked | None:
    symbol = str(detail.get("target") or "")
    if not symbol:
        return None
    givens = tuple(Given(str(i["symbol"]), str(i["text"])) for i in detail.get("inputs") or ()
                   if i.get("symbol") and i.get("origin") != "ASSUMPTION")
    steps = []
    for step in detail.get("steps") or ():
        result = step["result"]
        shown = f"{result.get('displayed', '?')} {result.get('unit') or ''}".strip()
        steps.append(Step(int(step["number"]), equation_markup(str(step["text"])), "",
                          substitution_markup(str(step["substitution"]), shown), "", str(step.get("symbol", ""))))
    check, ok = _VERIFICATION.get(str(detail.get("verification_status")),
                                  ("Dimensions were checked; the result is not independently verified.", True))
    return Worked(_result(symbol, detail["result"], {}), givens, tuple(steps), check, ok)


# ------------------------------------------------------------------ equations that disagree


@dataclass(frozen=True)
class Route:
    letter: str
    #: The stored equations this route used, as the command wrote them (without their identifiers).
    equations: tuple[str, ...]
    result: Result


@dataclass(frozen=True)
class Conflict:
    """Stored equations that give different values for the quantity asked: all are shown, none chosen."""

    symbol: str
    routes: tuple[Route, ...]

    def route_markup(self, route: Route) -> str:
        """`V = I R  ⇒  V = 10 V`, the equations and the value they gave."""
        return " \\quad ".join(route.equations) + f" \\quad \\Rightarrow \\quad {route.result.markup()}"


_IDENTIFIER = re.compile(r"\s*\([A-Z]{1,5}-\d{8}\)\s*$")


def conflict_from_part(raw: dict) -> Conflict | None:
    """The disagreeing routes of a CONFLICTING `solve` answer, else None."""
    command = raw.get("command") or ()
    detail = raw.get("detail")
    if not command or command[0] != "solve" or not isinstance(detail, dict) or detail.get("status") != "CONFLICTING":
        return None
    target = detail.get("target") or {}
    symbol = str(target.get("symbol") or target.get("asked") or "")
    names = _names(detail)
    routes = []
    try:
        for letter, route in zip("ABCDEFGH", detail.get("routes") or ()):
            value = route.get("value")
            equations = tuple(_IDENTIFIER.sub("", str(text)) for text in route.get("equations") or ())
            if not isinstance(value, dict) or not equations:
                return None
            routes.append(Route(letter, equations, _result(symbol, value, names)))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return Conflict(symbol, tuple(routes)) if symbol and len(routes) > 1 else None
