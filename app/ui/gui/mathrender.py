"""Textbook-style display of formulas: parse, lay out, draw. Standard library only.

RUDRA stores a formula as text - the expression as written in its source, such as
``P = dW/dt`` or ``x^2 + \\frac{1}{2}`` - and that text stays the searchable, auditable
record. This module only *displays* it: `parse` turns the text into a small tree
(fractions, powers, subscripts, radicals, integrals and sums, fenced groups, Greek
letters, functions), `layout` sets the tree as boxes with the proportions of printed
mathematics, and `draw_on_canvas` / `to_svg` paint the boxes. Nothing is rasterized
and nothing is stored: the text in, the picture out.

Accepted notation, freely mixed:

* linear text: ``a/b``, ``x^2``, ``x^(n+1)``, ``x_1``, ``R1`` (a letter and digits reads
  as a subscript), ``sqrt(x)``, ``sin^-1 x``, ``d^2y/dx^2``, ``<=``, ``>=``, ``!=``, ``->``,
  ``*`` (shown as a centred dot), Greek names (``theta``, ``pi``);
* Unicode as it comes out of PDFs: ``x²``, ``x₁``, ``√x``, ``∫``, ``∑``, ``θ``, ``≤``, ``·``;
* environments: ``matrix``, ``pmatrix``, ``bmatrix``, ``vmatrix``, ``Bmatrix``, ``cases``,
  ``aligned``/``align``, ``gathered``, ``array``, ``split``; rows by ``\\\\``, columns by
  ``&``; ``\\tag{n}`` equation numbers; accents ``\\hat``, ``\\bar``, ``\\vec``, ``\\dot``,
  ``\\ddot``, ``\\tilde``, ``\\overline``; ``\\binom``. A long equation is broken before its
  relations (then its operators) to fit the width it is given;
* a LaTeX subset: ``\\frac{}{}``, ``\\dfrac``, ``\\sqrt[n]{}``, ``^{}``, ``_{}``, ``\\int``,
  ``\\sum``, ``\\prod``, ``\\lim``, Greek letters, ``\\sin`` ... ``\\arctan``, ``\\cdot``,
  ``\\times``, ``\\pm``, ``\\leq``, ``\\infty``, ``\\partial``, ``\\left(`` ``\\right)``,
  ``\\text{}``, ``\\mathrm{}`` and the spacing commands.

What it cannot read is shown as it is written, never dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

# ------------------------------------------------------------------ symbol tables

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
    "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ",
    "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
#: Plain words read as Greek letters only when they stand alone (``theta``, not ``thetas``).
GREEK_WORDS = {name: letter for name, letter in GREEK.items() if not name.startswith("var")}

FUNCTIONS = frozenset({
    "sin", "cos", "tan", "cot", "sec", "csc", "cosec",
    "arcsin", "arccos", "arctan", "arccot", "arcsec", "arccsc",
    "sinh", "cosh", "tanh", "coth", "sech", "csch",
    "log", "ln", "lg", "exp", "lim", "max", "min", "det", "sgn", "arg", "mod",
})

#: LaTeX commands that are one symbol, and what kind of symbol.
COMMAND_SYMBOLS = {
    "cdot": ("·", "bin"), "times": ("×", "bin"), "div": ("÷", "bin"), "pm": ("±", "bin"), "mp": ("∓", "bin"),
    "ast": ("∗", "bin"), "circ": ("∘", "bin"),
    "le": ("≤", "rel"), "leq": ("≤", "rel"), "ge": ("≥", "rel"), "geq": ("≥", "rel"), "ne": ("≠", "rel"),
    "neq": ("≠", "rel"), "approx": ("≈", "rel"), "cong": ("≅", "rel"), "equiv": ("≡", "rel"),
    "sim": ("∼", "rel"), "propto": ("∝", "rel"), "to": ("→", "rel"), "rightarrow": ("→", "rel"),
    "leftarrow": ("←", "rel"), "Rightarrow": ("⇒", "rel"), "Leftrightarrow": ("⇔", "rel"), "in": ("∈", "rel"),
    "infty": ("∞", "ord"), "partial": ("∂", "ord"), "nabla": ("∇", "ord"), "degree": ("°", "ord"),
    "prime": ("′", "ord"), "ldots": ("…", "ord"), "cdots": ("⋯", "ord"), "hbar": ("ℏ", "ord"),
    "angle": ("∠", "ord"),
}
BIG_OPERATORS = {"int": "∫", "iint": "∬", "iiint": "∭", "oint": "∮", "sum": "∑", "prod": "∏",
                 "bigcup": "⋃", "bigcap": "⋂"}
ACCENT_MARKS = {"hat": "hat", "widehat": "hat", "bar": "bar", "overline": "bar", "vec": "vec",
                "overrightarrow": "vec", "dot": "dot", "ddot": "ddot", "tilde": "tilde", "widetilde": "tilde"}
SPACES = {",": 0.17, ":": 0.22, ">": 0.22, ";": 0.28, "quad": 1.0, "qquad": 2.0, " ": 0.28, "!": 0.0}

RELATIONS = frozenset("=<>≤≥≠≈≅≡∼∝→←⇒⇔∈")
BINARIES = frozenset("+-−±∓·×÷∗∘")
BIG_SYMBOLS = frozenset("∫∬∭∮∑∏")
LIMITS_BELOW = frozenset("∑∏")
#: Functions whose subscript is printed under them: lim with x → 0 below.
LIMIT_FUNCTIONS = frozenset({"lim", "max", "min"})
SUPERSCRIPTS = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ", "0123456789+-=()ni"))
SUBSCRIPTS = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₙₘₖₗₚₛₜ", "0123456789+-=()aeoxnmklpst"))
ASCII_OPERATORS = {"<=": "≤", ">=": "≥", "!=": "≠", "->": "→", "=>": "⇒", "+-": "±", "**": "^", "~=": "≈"}
OPENERS = {"(": ")", "[": "]", "{": "}"}

# ------------------------------------------------------------------ tree


@dataclass(frozen=True)
class Atom:
    """One symbol or run of letters. kind: var, num, func, greek, op (bin), rel, punct, text, ord."""

    text: str
    kind: str


@dataclass(frozen=True)
class Row:
    items: tuple = ()


@dataclass(frozen=True)
class Frac:
    numerator: object
    denominator: object


@dataclass(frozen=True)
class Script:
    base: object
    sup: object | None = None
    sub: object | None = None


@dataclass(frozen=True)
class Radical:
    body: object
    index: object | None = None


@dataclass(frozen=True)
class BigOp:
    symbol: str
    lower: object | None = None
    upper: object | None = None


@dataclass(frozen=True)
class Fenced:
    opener: str
    body: object
    closer: str


@dataclass(frozen=True)
class Space:
    em: float


@dataclass(frozen=True)
class Grid:
    """Rows of cells: a matrix, a system of cases, or aligned or gathered equations."""

    rows: tuple  # tuple[tuple[Row, ...], ...]
    kind: str = "matrix"  # matrix, cases, aligned, gathered, binom


@dataclass(frozen=True)
class Accent:
    mark: str  # hat, bar, vec, dot, ddot, tilde
    body: object


@dataclass(frozen=True)
class Tag:
    """An equation number, printed at the right: (1.2)."""

    label: str


# ------------------------------------------------------------------ tokens

_TOKEN = re.compile(
    r"(?P<ws>\s+)"
    r"|(?P<cmd>\\(?:[A-Za-z]+|.))"
    r"|(?P<num>\d+(?:\.\d+)?)"
    r"|(?P<word>[A-Za-z]+)"
    r"|(?P<ascii><=|>=|!=|->|=>|\+-|\*\*|~=)"
    r"|(?P<sup>[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ]+)"
    r"|(?P<sub>[₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₙₘₖₗₚₛₜ]+)"
    r"|(?P<char>.)",
    re.S,
)


@dataclass
class _Token:
    kind: str
    text: str
    space_before: bool = False


def tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    pending_space = False
    for match in _TOKEN.finditer(text):
        kind = match.lastgroup or "char"
        value = match.group()
        if kind == "ws":
            pending_space = True
            continue
        if kind == "ascii":
            value = ASCII_OPERATORS[value]
            kind = "char"
        tokens.append(_Token(kind, value, pending_space))
        pending_space = False
    return tokens


# ------------------------------------------------------------------ parser


class _Gap:
    """Whitespace in the source: it bounds a linear fraction's operands."""


class _Slash:
    """A linear '/', made into a fraction once the row is read."""


class _Parser:
    def __init__(self, text: str):
        self.tokens = tokenize(text)
        self.pos = 0

    def peek(self, offset: int = 0) -> _Token | None:
        index = self.pos + offset
        return self.tokens[index] if index < len(self.tokens) else None

    def take(self) -> _Token:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    # rows ---------------------------------------------------------

    def row(self, closers: frozenset[str] = frozenset(), stops: frozenset[str] = frozenset()) -> Row:
        items: list = []
        while (token := self.peek()) is not None:
            if token.kind == "char" and token.text in closers:
                break
            if token.kind == "cmd" and (token.text == "\\right" or token.text[1:] in stops):
                break
            if token.space_before and items:
                items.append(_Gap())
            if token.kind == "char" and token.text == "/":
                self.take()
                items.append(_Slash())
                continue
            node = self.primary()
            if node is None:
                continue
            items.append(self.postfix(node))
        return Row(tuple(_fractions(_subscript_digits(items))))

    # primaries ----------------------------------------------------

    def primary(self):
        token = self.take()
        kind, text = token.kind, token.text
        if kind == "num":
            return Atom(text, "num")
        if kind == "word":
            return self.word(text)
        if kind == "cmd":
            return self.command(text[1:])
        if kind == "sup":
            return Script(Atom("", "ord"), sup=Row(tuple(_plain_atoms("".join(SUPERSCRIPTS[c] for c in text)))))
        if kind == "sub":
            return Script(Atom("", "ord"), sub=Row(tuple(_plain_atoms("".join(SUBSCRIPTS[c] for c in text)))))
        if text in OPENERS:
            closer = OPENERS[text]
            body = self.row(frozenset({closer}))
            if self.peek() is not None and self.peek().text == closer:
                self.take()
                return body if text == "{" else Fenced(text, body, closer)
            return Row((Atom(text, "punct"), *body.items)) if text != "{" else body
        if text in BIG_SYMBOLS:
            return BigOp(text)
        if text in "√∛∜":
            index = {"√": None, "∛": Row((Atom("3", "num"),)), "∜": Row((Atom("4", "num"),))}[text]
            nxt = self.peek()
            if nxt is None:
                return Atom(text, "ord")
            return Radical(_unfence(self.postfix(self.primary())), index)
        if text in RELATIONS:
            return Atom(text, "rel")
        if text == "*":
            return Atom("·", "op")
        if text in BINARIES:
            return Atom("−" if text == "-" else text, "op")
        if text in ",;:!":
            return Atom(text, "punct")
        if text in "'′″":
            return Atom("′" * (2 if text == "″" else 1), "ord")
        if text in "})]":
            return Atom(text, "punct")
        if "Ͱ" <= text <= "Ͽ":
            return Atom(text, "greek")
        if text.isalpha():
            return Atom(text, "var")
        return Atom(text, "ord")

    def word(self, text: str):
        nxt = self.peek()
        if text == "sqrt" and nxt is not None and not nxt.space_before and nxt.text == "(":
            return Radical(_unfence(self.primary()))
        if text in FUNCTIONS:
            return Atom(text, "func")
        if text in GREEK_WORDS:
            return Atom(GREEK_WORDS[text], "greek")
        # "sinx", "cosθ": a function name run into a one-letter argument.
        for name in sorted(FUNCTIONS, key=len, reverse=True):
            if text.startswith(name) and len(text) == len(name) + 1 and name not in ("lg", "mod", "arg"):
                return Row((Atom(name, "func"), Atom(text[-1], "var")))
        return Atom(text, "var")

    def argument(self):
        """One LaTeX argument: a braced group or a single token."""
        token = self.peek()
        if token is None:
            return Row()
        if token.text == "{":
            self.take()
            body = self.row(frozenset({"}"}))
            if self.peek() is not None:
                self.take()
            return body.items[0] if len(body.items) == 1 else body
        return self.primary()

    def rows(self, end: str | None) -> list[list[Row]]:
        """Rows and cells up to \\end{end} (or the end of the text when `end` is None)."""
        rows: list[list[Row]] = []
        cells: list[Row] = []
        while True:
            cells.append(self.row(frozenset({"&"}), frozenset({"\\", "end"})))
            token = self.peek()
            if token is None:
                rows.append(cells)
                break
            if token.kind == "char" and token.text == "&":
                self.take()
                continue
            if token.kind == "cmd" and token.text == "\\\\":
                self.take()
                rows.append(cells)
                cells = []
                continue
            if token.kind == "cmd" and token.text == "\\end":
                self.take()
                closing = _source(self.argument()) if self.peek() is not None else ""
                if end is None or closing == end:
                    rows.append(cells)
                    if end is not None:
                        break
                    cells = []
                    continue
                continue
            rows.append(cells)
            break
        while len(rows) > 1 and all(not cell.items for cell in rows[-1]):
            rows.pop()
        return rows

    def environment(self):
        name = _source(self.argument()) if self.peek() is not None else ""
        if name in ("array", "tabular") and self.peek() is not None and self.peek().text == "{":
            self.argument()  # the column specification
        grid = tuple(tuple(row) for row in self.rows(name))
        bare = name.rstrip("*")
        fences = {"pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "vmatrix": ("|", "|"), "Vmatrix": ("‖", "‖"),
                  "Bmatrix": ("{", "}")}
        if bare in fences:
            opener, closer = fences[bare]
            return Fenced(opener, Grid(grid, "matrix"), closer)
        if bare in ("cases", "dcases"):
            return Fenced("{", Grid(grid, "cases"), "")
        if bare in ("aligned", "align", "alignat", "split", "eqnarray", "flalign"):
            return Grid(grid, "aligned")
        if bare in ("gathered", "gather", "multline", "equation"):
            return Grid(grid, "gathered")
        return Grid(grid, "matrix")

    def command(self, name: str):
        if name == "begin":
            return self.environment()
        if name in ("end", "\\"):
            return None
        if name == "tag":
            return Tag(_source(self.argument()))
        if name == "binom":
            top, bottom = self.argument(), self.argument()
            return Fenced("(", Grid(((Row((top,)),), (Row((bottom,)),)), "binom"), ")")
        if name in ACCENT_MARKS:
            return Accent(ACCENT_MARKS[name], self.argument())
        if name in ("frac", "dfrac", "tfrac"):
            return Frac(self.argument(), self.argument())
        if name == "sqrt":
            index = None
            if self.peek() is not None and self.peek().text == "[":
                self.take()
                index = self.row(frozenset({"]"}))
                if self.peek() is not None:
                    self.take()
            return Radical(self.argument(), index)
        if name in GREEK:
            return Atom(GREEK[name], "greek")
        if name in FUNCTIONS:
            return Atom(name, "func")
        if name in BIG_OPERATORS:
            return BigOp(BIG_OPERATORS[name])
        if name in COMMAND_SYMBOLS:
            symbol, kind = COMMAND_SYMBOLS[name]
            return Atom(symbol, {"bin": "op"}.get(kind, kind))
        if name in SPACES:
            return Space(SPACES[name])
        if name in ("left", "right", "big", "Big", "bigg", "Bigg", "displaystyle", "limits"):
            if name in ("left", "right") and self.peek() is not None and self.peek().text == ".":
                self.take()
            return None if name != "left" else self._left()
        if name in ("text", "mathrm", "operatorname", "textrm", "mbox"):
            return _as_text(self.argument())
        if name in ("mathit", "mathbf", "boldsymbol", "mathbb", "mathcal", "mathsf", "mathtt", "displaystyle"):
            return self.argument()
        if name in "{}_^%$#&":
            return Atom(name, "punct")
        return Atom(name, "text")

    def _left(self):
        """\\left( ... \\right): a fenced group sized to its content."""
        token = self.peek()
        if token is None:
            return None
        opener = self.take().text
        opener = {"\\{": "{", "\\lbrace": "{", "\\langle": "⟨", "\\|": "‖"}.get(opener, opener)
        body = self.row(frozenset())
        closer = ""
        if self.peek() is not None and self.peek().text == "\\right":
            self.take()
            if self.peek() is not None:
                closer = self.take().text
        closer = {"\\}": "}", "\\rbrace": "}", "\\rangle": "⟩", "\\|": "‖", ".": ""}.get(closer, closer)
        return Fenced(opener if opener != "." else "", body, closer)

    # scripts ------------------------------------------------------

    def postfix(self, node):
        sup = sub = None
        while (token := self.peek()) is not None and not token.space_before:
            if token.text == "^" and token.kind == "char":
                self.take()
                sup = self.script_argument()
            elif token.text == "_" and token.kind == "char":
                self.take()
                sub = self.script_argument()
            elif token.kind == "sup":
                self.take()
                sup = Row(tuple(_plain_atoms("".join(SUPERSCRIPTS[c] for c in token.text))))
            elif token.kind == "sub":
                self.take()
                sub = Row(tuple(_plain_atoms("".join(SUBSCRIPTS[c] for c in token.text))))
            elif token.text in ("'", "′"):
                self.take()
                sup = Row(((sup.items if isinstance(sup, Row) else ()) + (Atom("′", "ord"),)))
            else:
                break
        if isinstance(node, BigOp):
            return BigOp(node.symbol, sub, sup)
        if sup is None and sub is None:
            return node
        return Script(node, sup, sub)

    def script_argument(self):
        token = self.peek()
        if token is None:
            return Row()
        if token.text in "-+−" and token.kind == "char":
            sign = self.take().text
            rest = self.script_argument()
            items = rest.items if isinstance(rest, Row) else (rest,)
            return Row((Atom("−" if sign in "-−" else "+", "ord"), *items))
        if token.text == "{":
            return self.argument()
        node = self.primary()
        if isinstance(node, Fenced) and node.opener == "(":
            return node.body
        return node


def _plain_atoms(text: str) -> list[Atom]:
    atoms = []
    for char in text:
        if char.isdigit():
            atoms.append(Atom(char, "num"))
        elif char in "+-=":
            atoms.append(Atom("−" if char == "-" else char, "ord"))
        else:
            atoms.append(Atom(char, "var"))
    return atoms


def _as_text(node) -> Atom:
    return Atom(_source(node), "text")


def _source(node) -> str:
    if isinstance(node, Atom):
        return node.text
    if isinstance(node, Row):
        return "".join(_source(item) for item in node.items)
    return ""


def _unfence(node):
    return node.body if isinstance(node, Fenced) and node.opener in ("(", "{") else node


def _subscript_digits(items: list) -> list:
    """``R1``, ``V2``: one letter directly followed by digits is a subscripted symbol."""
    out: list = []
    for item in items:
        previous = out[-1] if out else None
        if (isinstance(item, Atom) and item.kind == "num" and "." not in item.text
                and isinstance(previous, Atom) and previous.kind in ("var", "greek") and len(previous.text) == 1):
            out[-1] = Script(previous, None, Row((item,)))
            continue
        out.append(item)
    return out


def _is_operand(item) -> bool:
    if isinstance(item, (_Gap, _Slash, Space)):
        return False
    if isinstance(item, Atom) and item.kind in ("op", "rel", "punct"):
        return False
    return not isinstance(item, BigOp)


def _fractions(items: list) -> list:
    """Turn each linear '/' into a fraction of the operands touching it.

    An operand is the run of symbols with no space or operator in it: ``dW/dt`` is dW over
    dt, ``V / Rtotal`` is V over Rtotal, ``1/2 m v^2`` is one half times m v².
    """
    out: list = []
    index = 0
    while index < len(items):
        item = items[index]
        if not isinstance(item, _Slash):
            out.append(item)
            index += 1
            continue
        while out and isinstance(out[-1], _Gap):
            out.pop()
        start = len(out)
        while start > 0 and _is_operand(out[start - 1]):
            start -= 1
        numerator = out[start:]
        del out[start:]
        index += 1
        while index < len(items) and isinstance(items[index], _Gap):
            index += 1
        end = index
        while end < len(items) and _is_operand(items[end]):
            # d/dx(x^n): brackets after the denominator are what the fraction applies to.
            if end > index and isinstance(items[end], Fenced) and items[end].opener == "(":
                break
            end += 1
        denominator = items[index:end]
        if not numerator or not denominator:
            out.extend(numerator)
            out.append(Atom("/", "ord"))
            out.extend(denominator)
        else:
            out.append(Frac(_operand(numerator), _operand(denominator)))
        index = end
    # Source spaces are not printed: mathematical spacing comes from the symbols.
    return [item for item in out if not isinstance(item, _Gap)]


def _operand(items: list):
    if len(items) == 1:
        return _unfence(items[0])
    return Row(tuple(items))


def parse(text: str) -> Row:
    """The formula's tree. Never raises: what cannot be read stays as written.

    Rows separated by ``\\\\`` at the top level are equations of one display: aligned at
    ``&`` when any row has one, otherwise centred one under another.
    """
    try:
        rows = _Parser(text).rows(None)
    except (IndexError, KeyError, RecursionError):  # pragma: no cover - defensive
        return Row((Atom(text, "text"),))
    if len(rows) == 1 and len(rows[0]) == 1:
        return rows[0][0]
    kind = "aligned" if any(len(row) > 1 for row in rows) else "gathered"
    return Row((Grid(tuple(tuple(row) for row in rows), kind),))


# ------------------------------------------------------------------ layout

class Metrics(Protocol):
    """Text measurement for one output (a Tk canvas, or an approximation)."""

    def measure(self, text: str, size: float, style: str) -> float: ...


@dataclass
class Box:
    """A laid-out piece: width, height above and depth below its baseline, and what to draw.

    Items are relative to the box's origin (left end of its baseline; y grows down):
    ``("text", x, y, text, size, style)`` with y the baseline, ``("line", x1, y1, x2, y2, width)``
    and ``("path", ((x, y), ...), width)``.
    """

    width: float = 0.0
    ascent: float = 0.0
    descent: float = 0.0
    items: list = field(default_factory=list)

    def place(self, other: "Box", dx: float, dy: float) -> None:
        for item in other.items:
            self.items.append(_shift(item, dx, dy))


def _shift(item: tuple, dx: float, dy: float) -> tuple:
    kind = item[0]
    if kind == "text":
        return ("text", item[1] + dx, item[2] + dy, *item[3:])
    if kind == "line":
        return ("line", item[1] + dx, item[2] + dy, item[3] + dx, item[4] + dy, item[5])
    return ("path", tuple((x + dx, y + dy) for x, y in item[1]), item[2])


ASCENT = 0.72   # glyph height above the baseline, per em
DESCENT = 0.24  # glyph depth below the baseline, per em
AXIS = 0.27     # the maths axis (fraction bars, operators) above the baseline, per em
SCRIPT = 0.72   # script size relative to its base
MIN_SIZE = 8.0  # pixels: scripts of scripts stop shrinking here


def _style(atom: Atom) -> str:
    if atom.kind in ("var",) or (atom.kind == "greek" and atom.text.islower()):
        return "italic"
    if atom.kind in ("op", "rel", "ord", "punct") and atom.text in BIG_SYMBOLS | set("√∞∂∇∑∏"):
        return "symbol"
    return "roman"


class _Layout:
    def __init__(self, metrics: Metrics):
        self.metrics = metrics

    def box(self, node, size: float, level: int = 0) -> Box:
        if isinstance(node, Atom):
            return self.atom(node, size)
        if isinstance(node, Row):
            return self.row(node.items, size, level)
        if isinstance(node, Frac):
            return self.frac(node, size, level)
        if isinstance(node, Script):
            return self.script(node, size, level)
        if isinstance(node, Radical):
            return self.radical(node, size, level)
        if isinstance(node, BigOp):
            return self.bigop(node, size, level)
        if isinstance(node, Fenced):
            return self.fenced(node, size, level)
        if isinstance(node, Space):
            return Box(width=node.em * size)
        if isinstance(node, Grid):
            return self.grid(node, size, level)
        if isinstance(node, Accent):
            return self.accent(node, size, level)
        if isinstance(node, Tag):
            label = f"({node.label})"
            width = self.metrics.measure(label, size, "roman")
            return Box(width + size, ASCENT * size, DESCENT * size, [("text", size, 0.0, label, size, "roman")])
        return Box()

    def atom(self, atom: Atom, size: float) -> Box:
        if not atom.text:
            return Box(ascent=ASCENT * size * 0.6)
        style = _style(atom)
        width = self.metrics.measure(atom.text, size, style)
        if style == "italic":
            width += 0.04 * size  # italic overhang, so the next symbol does not touch it
        return Box(width, ASCENT * size, DESCENT * size, [("text", 0.0, 0.0, atom.text, size, style)])

    def row(self, items, size: float, level: int) -> Box:
        out = Box(ascent=ASCENT * size * 0.8, descent=DESCENT * size * 0.8)
        x = 0.0
        previous = None
        for index, item in enumerate(items):
            gap = self.spacing(previous, item, size, index == 0)
            if level and isinstance(item, Atom) and item.kind in ("op", "rel"):
                gap = (gap[0] * 0.6, gap[1] * 0.6)  # operators sit closer in scripts and fractions
            x += gap[0]
            child = self.box(item, size, level)
            out.place(child, x, 0.0)
            x += child.width + gap[1]
            out.ascent = max(out.ascent, child.ascent)
            out.descent = max(out.descent, child.descent)
            previous = item
        out.width = x
        return out

    def spacing(self, previous, item, size: float, first: bool) -> tuple[float, float]:
        """Space before and after `item`, as printed mathematics spaces it."""
        if isinstance(item, Atom) and item.kind == "rel":
            return (0.28 * size, 0.28 * size)
        if isinstance(item, Atom) and item.kind == "op":
            unary = first or (isinstance(previous, Atom) and previous.kind in ("op", "rel", "punct"))
            return (0.0, 0.0) if unary else (0.22 * size, 0.22 * size)
        if isinstance(item, Atom) and item.kind == "punct" and item.text in ",;":
            return (0.0, 0.17 * size)
        before = 0.0
        if isinstance(previous, Atom) and previous.kind == "func":
            before = 0.17 * size
        elif isinstance(previous, Script) and isinstance(previous.base, Atom) and previous.base.kind == "func":
            before = 0.17 * size
        elif isinstance(previous, BigOp):
            before = 0.12 * size
        # A unit or word written as text stands a space apart: 3.84 V.
        if isinstance(item, Atom) and item.kind == "text" and previous is not None:
            before = max(before, 0.22 * size)
        # The differential of an integral stands apart: ∫ xⁿ dx, ∫ sin θ dθ.
        if (isinstance(item, Atom) and item.kind == "var" and item.text[:1] == "d" and len(item.text) <= 2
                and previous is not None and not (isinstance(previous, Atom) and previous.kind in ("op", "rel"))
                and not isinstance(previous, BigOp)):
            before = max(before, 0.17 * size)
        if isinstance(item, Atom) and item.kind == "func" and previous is not None and not isinstance(previous, Atom):
            before = max(before, 0.12 * size)
        if isinstance(item, Atom) and item.kind == "func" and isinstance(previous, Atom) and previous.kind in (
                "var", "num", "greek"):
            before = max(before, 0.17 * size)
        return (before, 0.0)

    def smaller(self, size: float) -> float:
        return max(MIN_SIZE, size * SCRIPT)

    def frac(self, node: Frac, size: float, level: int) -> Box:
        inner = size if level == 0 else self.smaller(size)
        top = self.box(node.numerator, inner, level + 1)
        bottom = self.box(node.denominator, inner, level + 1)
        rule = max(1.0, size * 0.055)
        pad = 0.12 * size
        width = max(top.width, bottom.width) + 2 * pad
        axis = -AXIS * size
        gap = 0.16 * size
        out = Box(width)
        top_baseline = axis - rule / 2 - gap - top.descent
        bottom_baseline = axis + rule / 2 + gap + bottom.ascent
        out.place(top, (width - top.width) / 2, top_baseline)
        out.place(bottom, (width - bottom.width) / 2, bottom_baseline)
        out.items.append(("line", 0.04 * size, axis, width - 0.04 * size, axis, rule))
        out.ascent = -(top_baseline - top.ascent)
        out.descent = bottom_baseline + bottom.descent
        out.width = width + 0.08 * size
        return out

    def script(self, node: Script, size: float, level: int) -> Box:
        if isinstance(node.base, Atom) and node.base.text in LIMIT_FUNCTIONS and node.sup is None and level == 0:
            return self.bigop(BigOp(node.base.text, node.sub, None), size, level)
        base = self.box(node.base, size, level)
        small = self.smaller(size)
        out = Box(base.width, base.ascent, base.descent)
        out.place(base, 0.0, 0.0)
        right = base.width
        extent = 0.0
        tall = base.ascent > ASCENT * size * 1.15
        if node.sup is not None:
            sup = self.box(node.sup, small, level + 1)
            raise_by = max(0.42 * size, base.ascent - 0.55 * sup.ascent) if tall else 0.42 * size
            if isinstance(node.base, Atom) and not node.base.text:
                raise_by = 0.42 * size
            out.place(sup, right + 0.03 * size, -raise_by)
            extent = max(extent, sup.width)
            out.ascent = max(out.ascent, raise_by + sup.ascent)
        if node.sub is not None:
            sub = self.box(node.sub, small, level + 1)
            lower = max(0.18 * size, base.descent - 0.3 * sub.ascent) if tall else 0.18 * size
            if node.sup is not None:
                lower = max(lower, 0.3 * size)
            out.place(sub, right + 0.02 * size, lower)
            extent = max(extent, sub.width)
            out.descent = max(out.descent, lower + sub.descent)
        out.width = right + extent + (0.05 * size if extent else 0.0)
        return out

    def radical(self, node: Radical, size: float, level: int) -> Box:
        body = self.box(node.body, size, level)
        gap = 0.12 * size
        rule = max(1.0, size * 0.055)
        top = -(body.ascent + gap + rule)
        bottom = body.descent + 0.02 * size
        tick = 0.55 * size
        out = Box()
        x0 = 0.0
        index_width = 0.0
        if node.index is not None:
            index = self.box(node.index, self.smaller(self.smaller(size)), level + 2)
            index_width = max(0.0, index.width - 0.25 * size)
            out.place(index, 0.0, top + (bottom - top) * 0.42)
            out.ascent = max(out.ascent, -(top + (bottom - top) * 0.42 - index.ascent))
            x0 = index_width
        mid = top + (bottom - top) * 0.62
        points = ((x0, mid), (x0 + 0.14 * size, mid - 0.06 * size), (x0 + 0.3 * size, bottom),
                  (x0 + tick, top), (x0 + tick + body.width + 0.1 * size, top))
        out.items.append(("path", points, rule))
        out.place(body, x0 + tick + 0.06 * size, 0.0)
        out.width = x0 + tick + body.width + 0.18 * size
        out.ascent = max(out.ascent, -top + rule)
        out.descent = max(body.descent, bottom)
        return out

    def bigop(self, node: BigOp, size: float, level: int) -> Box:
        integral = node.symbol not in LIMITS_BELOW and node.symbol not in LIMIT_FUNCTIONS
        if node.symbol in LIMIT_FUNCTIONS:
            glyph = self.metrics.measure(node.symbol, size, "roman")
            out = Box(glyph, ASCENT * size, DESCENT * size, [("text", 0.0, 0.0, node.symbol, size, "roman")])
        else:
            glyph_size = size * (1.7 if integral else 1.45) if level == 0 else size * 1.2
            glyph = self.metrics.measure(node.symbol, glyph_size, "symbol")
            # The operator is centred on the maths axis rather than sitting on the baseline.
            baseline = glyph_size * (0.30 if integral else 0.27) - AXIS * size
            out = Box(glyph, glyph_size * 0.62 + AXIS * size, glyph_size * 0.30,
                      [("text", 0.0, baseline, node.symbol, glyph_size, "symbol")])
        top, bottom = -out.ascent, out.descent
        small = self.smaller(size)
        if integral:
            extent = 0.0
            if node.upper is not None:
                upper = self.box(node.upper, small, level + 1)
                out.place(upper, glyph + 0.02 * size, top + upper.ascent)
                extent = max(extent, upper.width)
            if node.lower is not None:
                lower = self.box(node.lower, small, level + 1)
                out.place(lower, glyph - 0.18 * size, bottom - lower.descent * 0.2)
                extent = max(extent, lower.width - 0.2 * size)
                out.descent = max(out.descent, bottom - lower.descent * 0.2 + lower.descent)
            out.width = glyph + extent + 0.06 * size
            return out
        width = glyph
        parts = []
        if node.upper is not None:
            parts.append(("upper", self.box(node.upper, small, level + 1)))
        if node.lower is not None:
            parts.append(("lower", self.box(node.lower, small, level + 1)))
        for _, part in parts:
            width = max(width, part.width)
        centred = Box(width, out.ascent, out.descent)
        centred.place(out, (width - glyph) / 2, 0.0)
        for where, part in parts:
            if where == "upper":
                y = top - 0.08 * size - part.descent
                centred.ascent = max(centred.ascent, -(y - part.ascent))
            else:
                y = bottom + 0.08 * size + part.ascent
                centred.descent = max(centred.descent, y + part.descent)
            centred.place(part, (width - part.width) / 2, y)
        centred.width = width + 0.1 * size
        return centred

    def fenced(self, node: Fenced, size: float, level: int) -> Box:
        body = self.box(node.body, size, level)
        tall = body.ascent + body.descent > (ASCENT + DESCENT) * size * 1.3
        out = Box()
        x = 0.0
        pad = 0.05 * size
        if not tall:
            for symbol, is_open in ((node.opener, True), (node.closer, False)):
                if not symbol:
                    continue
                piece = Box(self.metrics.measure(symbol, size, "roman"), ASCENT * size, DESCENT * size,
                            [("text", 0.0, 0.0, symbol, size, "roman")])
                if is_open:
                    out.place(piece, x, 0.0)
                    x += piece.width + pad
                    out.place(body, x, 0.0)
                    x += body.width + pad
                else:
                    out.place(piece, x, 0.0)
                    x += piece.width
            if not node.opener:
                out.place(body, x, 0.0)
                x += body.width
            out.width = x
            out.ascent = max(body.ascent, ASCENT * size)
            out.descent = max(body.descent, DESCENT * size)
            return out
        top, bottom = -body.ascent - 0.06 * size, body.descent + 0.06 * size
        rule = max(1.0, size * 0.06)
        width = 0.32 * size

        def delimiter(symbol: str, left: float, opening: bool) -> list:
            inner, outer = (left + width * 0.85, left + width * 0.2) if opening else (left + width * 0.15,
                                                                                      left + width * 0.8)
            if symbol in "()":
                points = tuple((inner + (outer - inner) * (1 - ((t - 0.5) * 2) ** 2), top + (bottom - top) * t)
                               for t in (i / 12 for i in range(13)))
                return [("path", points, rule)]
            if symbol in "[]":
                return [("path", ((inner, top), (outer, top), (outer, bottom), (inner, bottom)), rule)]
            if symbol in "|‖":
                return [("line", (left + width / 2), top, (left + width / 2), bottom, rule)]
            middle = (top + bottom) / 2
            if symbol in "{}":
                # A curly brace: in to the middle point and out again.
                mid = (inner + outer) / 2
                quarter = (bottom - top) * 0.08
                return [("path", ((inner, top), (mid, top + quarter), (mid, middle - quarter), (outer, middle),
                                  (mid, middle + quarter), (mid, bottom - quarter), (inner, bottom)), rule)]
            return [("path", ((inner, top), (outer, middle), (inner, bottom)), rule)]

        if node.opener:
            out.items += delimiter(node.opener, 0.0, True)
            x = width + pad
        out.place(body, x, 0.0)
        x += body.width + pad
        if node.closer:
            out.items += delimiter(node.closer, x, False)
            x += width
        out.width = x
        out.ascent = -top
        out.descent = bottom
        return out


    def grid(self, node: Grid, size: float, level: int) -> Box:
        cells = [[self.box(cell, size, level) for cell in row] for row in node.rows]
        columns = max((len(row) for row in cells), default=0)
        if not columns:
            return Box()
        widths = [max((row[c].width for row in cells if c < len(row)), default=0.0) for c in range(columns)]
        heights = [(max([ASCENT * size, *(b.ascent for b in row)]), max([DESCENT * size, *(b.descent for b in row)]))
                   for row in cells]
        row_gap = 0.45 * size if node.kind in ("aligned", "gathered", "cases") else 0.3 * size

        def gap_before(column: int) -> float:
            if column == 0:
                return 0.0
            if node.kind == "aligned":
                return 0.0 if column % 2 else 1.6 * size  # "a &= b": no gap at &, a wide one between pairs
            return 1.0 * size if node.kind == "cases" else 0.8 * size

        def align(column: int) -> str:
            if node.kind == "aligned":
                return "right" if column % 2 == 0 else "left"
            if node.kind == "cases":
                return "left"
            return "center"

        xs, x = [], 0.0
        for column in range(columns):
            x += gap_before(column)
            xs.append(x)
            x += widths[column]
        total = sum(a + d for a, d in heights) + row_gap * (len(heights) - 1)
        top = -AXIS * size - total / 2  # the block is centred on the maths axis
        out = Box(x + 0.1 * size)
        y = top
        for row, (above, below) in zip(cells, heights):
            baseline = y + above
            for column, cell in enumerate(row):
                slack = widths[column] - cell.width
                offset = {"left": 0.0, "right": slack, "center": slack / 2}[align(column)]
                out.place(cell, xs[column] + offset, baseline)
            y = baseline + below + row_gap
        out.ascent = -top
        out.descent = top + total
        return out

    def accent(self, node: Accent, size: float, level: int) -> Box:
        body = self.box(node.body, size, level)
        out = Box(body.width, body.ascent + 0.28 * size, body.descent)
        out.place(body, 0.0, 0.0)
        y = -(body.ascent + 0.1 * size)
        left, right = 0.12 * size, max(0.3 * size, body.width - 0.08 * size)
        middle = (left + right) / 2
        stroke = max(1.0, size * 0.05)
        if node.mark == "bar":
            out.items.append(("line", left, y, right, y, stroke))
        elif node.mark == "hat":
            out.items.append(("path", ((middle - 0.2 * size, y + 0.1 * size), (middle, y - 0.04 * size),
                                       (middle + 0.2 * size, y + 0.1 * size)), stroke))
        elif node.mark == "vec":
            out.items.append(("line", left, y, right, y, stroke))
            out.items.append(("path", ((right - 0.12 * size, y - 0.07 * size), (right, y),
                                       (right - 0.12 * size, y + 0.07 * size)), stroke))
        elif node.mark in ("dot", "ddot"):
            spots = (middle,) if node.mark == "dot" else (middle - 0.1 * size, middle + 0.1 * size)
            for spot in spots:
                out.items.append(("line", spot - 0.03 * size, y, spot + 0.03 * size, y, max(2.0, 0.09 * size)))
        else:  # tilde
            out.items.append(("path", ((middle - 0.22 * size, y + 0.04 * size), (middle - 0.1 * size, y - 0.05 * size),
                                       (middle + 0.1 * size, y + 0.05 * size), (middle + 0.22 * size, y - 0.04 * size)),
                              stroke))
        return out

    def wrapped(self, tree, size: float, max_width: float) -> Box:
        """A long equation broken before its relations (then its operators) to fit `max_width`."""
        whole = self.box(tree, size)
        items = tree.items if isinstance(tree, Row) else ()
        if whole.width <= max_width or len(items) < 3 or any(isinstance(i, Grid) for i in items):
            return whole

        def pieces(breakers: tuple[str, ...]) -> list[list]:
            groups: list[list] = [[]]
            for position, item in enumerate(items):
                if (position and isinstance(item, Atom) and item.kind in breakers and groups[-1]
                        and not (item.kind == "op" and isinstance(items[position - 1], Atom)
                                 and items[position - 1].kind in ("op", "rel"))):
                    groups.append([])
                groups[-1].append(item)
            return groups

        groups = pieces(("rel",))
        if max(self.box(Row(tuple(g)), size).width for g in groups) > max_width:
            groups = pieces(("rel", "op"))
        lines: list[list] = []
        for group in groups:
            candidate = (lines[-1] if lines else []) + group
            if lines and self.box(Row(tuple(candidate)), size).width <= max_width:
                lines[-1] = candidate
            else:
                lines.append(list(group))
        if len(lines) == 1:
            return whole
        first = next((k for k, item in enumerate(lines[0]) if isinstance(item, Atom) and item.kind == "rel"), None)
        indent = self.box(Row(tuple(lines[0][:first])), size).width if first else size
        if indent > max_width * 0.45:
            indent = size
        boxes = [self.box(Row(tuple(line)), size) for line in lines]
        out = Box(max([boxes[0].width] + [indent + b.width for b in boxes[1:]]))
        y = 0.0
        out.ascent = boxes[0].ascent
        for number, box in enumerate(boxes):
            if number:
                y += boxes[number - 1].descent + 0.35 * size + box.ascent
            out.place(box, 0.0 if number == 0 else indent, y)
        out.descent = y + boxes[-1].descent
        return out


def layout(tree, size: float, metrics: Metrics, max_width: float | None = None) -> Box:
    """Set a parsed formula at `size` pixels to the em, broken into lines to fit `max_width`."""
    engine = _Layout(metrics)
    if max_width:
        return engine.wrapped(tree, float(size), float(max_width))
    return engine.box(tree, float(size))


# ------------------------------------------------------------------ output

#: Faces for each style, first available wins. Cambria and Cambria Math ship with Windows.
FACES = {
    "roman": ("Cambria", "Times New Roman", "DejaVu Serif", "serif"),
    "italic": ("Cambria", "Times New Roman", "DejaVu Serif", "serif"),
    "symbol": ("Cambria Math", "Segoe UI Symbol", "DejaVu Sans", "Cambria", "serif"),
}


class TkMetrics:
    """Measurement and fonts from Tk, for the window."""

    def __init__(self, widget):
        import tkinter.font as tkfont

        self._tkfont = tkfont
        self.widget = widget
        available = set(tkfont.families(widget))
        self.faces = {style: next((f for f in faces if f in available), faces[-1]) for style, faces in FACES.items()}
        self._fonts: dict[tuple[str, int], object] = {}

    def font(self, size: float, style: str):
        key = (style, max(1, round(size)))
        if key not in self._fonts:
            self._fonts[key] = self._tkfont.Font(
                root=self.widget, family=self.faces[style], size=-key[1],
                slant="italic" if style == "italic" else "roman")
        return self._fonts[key]

    def measure(self, text: str, size: float, style: str) -> float:
        return float(self.font(size, style).measure(text))

    def font_ascent(self, size: float, style: str) -> int:
        return int(self.font(size, style).metrics("ascent"))


class ApproximateMetrics:
    """Measurement without a display: average glyph widths. Used for tests and fallbacks."""

    def measure(self, text: str, size: float, style: str) -> float:
        wide = sum(1 for c in text if c in "mwMW∑∏∫")
        return size * (0.52 * (len(text) - wide) + 0.8 * wide)


def draw_on_canvas(canvas, box: Box, metrics: TkMetrics, x: float, baseline: float, color: str) -> list[int]:
    """Paint a laid-out formula on a Tk canvas with its baseline at `baseline`. Returns the item ids."""
    ids = []
    for item in box.items:
        kind = item[0]
        if kind == "text":
            _, dx, dy, text, size, style = item
            top = baseline + dy - metrics.font_ascent(size, style)
            ids.append(canvas.create_text(x + dx, top, text=text, anchor="nw", fill=color,
                                          font=metrics.font(size, style)))
        elif kind == "line":
            _, x1, y1, x2, y2, width = item
            ids.append(canvas.create_line(x + x1, baseline + y1, x + x2, baseline + y2, fill=color,
                                          width=width, capstyle="butt"))
        else:
            _, points, width = item
            flat = [value for px, py in points for value in (x + px, baseline + py)]
            ids.append(canvas.create_line(*flat, fill=color, width=width, joinstyle="round", capstyle="round"))
    return ids


def to_svg(box: Box, color: str = "#111", background: str | None = None, margin: float = 6.0) -> str:
    """The laid-out formula as a standalone SVG document (text stays text)."""
    from xml.sax.saxutils import escape

    width = box.width + 2 * margin
    height = box.ascent + box.descent + 2 * margin
    base = margin + box.ascent
    body = []
    if background:
        body.append(f'<rect width="100%" height="100%" fill="{background}"/>')
    for item in box.items:
        kind = item[0]
        if kind == "text":
            _, dx, dy, text, size, style = item
            family = ", ".join(f"'{f}'" for f in FACES[style])
            slant = ' font-style="italic"' if style == "italic" else ""
            body.append(f'<text x="{margin + dx:.2f}" y="{base + dy:.2f}" font-family="{family}" '
                        f'font-size="{size:.2f}"{slant} fill="{color}">{escape(text)}</text>')
        elif kind == "line":
            _, x1, y1, x2, y2, stroke = item
            body.append(f'<line x1="{margin + x1:.2f}" y1="{base + y1:.2f}" x2="{margin + x2:.2f}" '
                        f'y2="{base + y2:.2f}" stroke="{color}" stroke-width="{stroke:.2f}"/>')
        else:
            _, points, stroke = item
            path = " ".join(f"{margin + px:.2f},{base + py:.2f}" for px, py in points)
            body.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="{stroke:.2f}" '
                        'stroke-linejoin="round" stroke-linecap="round"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.1f}" height="{height:.1f}" '
            f'viewBox="0 0 {width:.2f} {height:.2f}">' + "".join(body) + "</svg>")


# ------------------------------------------------------------------ which text is a formula

_EQUATION_PREFIX = re.compile(r"^(?P<label>(?:Equation|Formula)\s*:\s*)(?P<formula>.+)$")
_LATEX_HINT = re.compile(r"\\(?:frac|dfrac|sqrt|int|sum|prod|lim|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|"
                         r"omega|Omega|sin|cos|tan|cdot|times|pm|infty|partial|left|right)\b|[\^_]\{")


def formula_parts(line: str) -> tuple[str, str] | None:
    """(label, formula) when an answer line holds a formula to typeset, else None.

    Lines labelled ``Equation:`` or ``Formula:`` - how RUDRA's answers present a stored
    equation - and any line written in LaTeX notation.
    """
    match = _EQUATION_PREFIX.match(line.strip())
    if match:
        return match.group("label"), match.group("formula").strip()
    if _LATEX_HINT.search(line):
        return "", line.strip()
    return None


def rendered_text(tree) -> str:
    """The glyphs the display shows, in reading order (what a screen reader or a copy gets)."""
    if isinstance(tree, Atom):
        return tree.text
    if isinstance(tree, Row):
        return "".join(rendered_text(item) for item in tree.items)
    if isinstance(tree, Frac):
        return f"({rendered_text(tree.numerator)})/({rendered_text(tree.denominator)})"
    if isinstance(tree, Script):
        text = rendered_text(tree.base)
        if tree.sub is not None:
            text += f"_{rendered_text(tree.sub)}"
        if tree.sup is not None:
            text += f"^{rendered_text(tree.sup)}"
        return text
    if isinstance(tree, Radical):
        return f"√({rendered_text(tree.body)})"
    if isinstance(tree, BigOp):
        return tree.symbol
    if isinstance(tree, Fenced):
        return f"{tree.opener}{rendered_text(tree.body)}{tree.closer}"
    if isinstance(tree, Grid):
        return "; ".join(", ".join(rendered_text(cell) for cell in row) for row in tree.rows)
    if isinstance(tree, Accent):
        return f"{tree.mark}({rendered_text(tree.body)})"
    if isinstance(tree, Tag):
        return f" ({tree.label})"
    return " "
