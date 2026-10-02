"""Reading a stored equation as a formula RUDRA can calculate with.

Documents print equations in many shapes, and the extractor keeps each one exactly as the
page gave it: `V = IR`, `P = VI = I^{2} R = \\frac{V^{2}}{R} W`, `I_{c} = C\\,\\frac{dV}{dt}`.
The calculation engine understands one shape - `SYMBOL = expression` in the formula grammar -
so each stored text is *read* into that shape here, by a fixed list of rewrites:

* typeset notation becomes plain: `\\frac{a}{b}` is `((a) / (b))`, `x^{2}` is `x^2`, `V_{c}`
  is the symbol `Vc`, `\\cdot` and `\\times` are `*`, Greek letters are letters;
* a chain `P = V I = I^2 R` is several equations that share a left-hand side;
* quantities written side by side are multiplied, **but only when the reading is
  unambiguous**: `IR` is `I * R` when `I` and `R` are quantities the documents or the
  question have named and no other split exists. The reading is recorded in the equation's
  notes, and the answer says so;
* a unit printed after a result (`... W`) is dropped, since units belong to the values.

Anything else - an integral, a derivative, a square root, a matrix, a sentence with an equals
sign in it, a function of time - is **not readable for calculation** and is reported with the
reason, never approximated. Nothing here consults anything but the text.
"""

import re
from collections.abc import Collection
from dataclasses import dataclass

from app.calculation.formulas import Formula, FormulaError, is_symbol, parse_formula
from app.solving.rearrange import render

_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "vartheta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
    "xi": "ξ", "pi": "π", "rho": "ρ", "varrho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
_NOT_NUMERIC = {
    "int": "an integral", "oint": "an integral", "sum": "a sum", "prod": "a product over a range",
    "partial": "a derivative", "nabla": "a derivative", "lim": "a limit", "sqrt": "a root",
    "begin": "a matrix or aligned block", "end": "a matrix or aligned block", "sin": "a trigonometric function",
    "cos": "a trigonometric function", "tan": "a trigonometric function", "log": "a logarithm",
    "ln": "a logarithm", "exp": "an exponential", "infty": "infinity", "mathcal": "a script letter",
    "hat": "an accent", "bar": "an accent", "vec": "a vector", "dot": "a time derivative", "ddot": "a time derivative",
    "text": "words inside the equation", "overline": "an accent", "underline": "an accent",
}
#: Words printed after a result as its unit: dropped when they close an expression.
UNIT_WORDS = frozenset({
    "V", "A", "W", "F", "H", "J", "C", "S", "s", "Hz", "Wb", "T", "Ω", "Ω",
    "volt", "volts", "Volt", "Volts", "amp", "amps", "Amp", "Amps", "ampere", "amperes", "ohm", "ohms", "Ohm",
    "Ohms", "watt", "watts", "Watt", "Watts", "farad", "farads", "henry", "henries", "joule", "joules",
    "hertz", "Hertz", "second", "seconds", "coulomb", "coulombs",
})
_SUPERSCRIPTS = {"²": "^2", "³": "^3", "¹": "^1", "⁻¹": "^-1", "⁻²": "^-2"}
_ALLOWED = re.compile(r"[\w\s+\-*/^()=.]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class Unreadable:
    """A stored equation that cannot be used to calculate, and why - in plain words."""

    reason: str


@dataclass(frozen=True, slots=True)
class Reading:
    """One formula read from a stored equation, with how it was read."""

    formula: Formula
    notes: tuple[str, ...] = ()


# ------------------------------------------------------------------ typeset notation


def _innermost_fraction(text: str) -> str:
    pattern = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
    while True:
        replaced = pattern.sub(lambda m: f"(({m.group(1)})/({m.group(2)}))", text)
        if replaced == text:
            return text
        text = replaced


def plain(text: str) -> str | Unreadable:
    """The stored equation as plain text, or why it is not a calculable equation."""
    if not isinstance(text, str) or not text.strip():
        return Unreadable("the equation is empty")
    source = " ".join(text.split())
    for mark, plain_form in _SUPERSCRIPTS.items():
        source = source.replace(mark, plain_form)
    for dash in ("−", "–", "—", "‒"):
        source = source.replace(dash, "-")
    for times in ("×", "·", "⋅", "∙", "•"):
        source = source.replace(times, "*")
    source = source.replace("÷", "/")
    source = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)\s*([()\[\]|.]?)", r"\1", source)
    source = re.sub(r"\\(?:,|;|:|!|quad|qquad|ldots|cdots)", " ", source)
    source = re.sub(r"\\\s", " ", source)
    source = re.sub(r"\\tag\s*\{[^{}]*\}", " ", source)
    source = re.sub(r"\\(?:mathrm|mathit|mathbf|operatorname|boldsymbol)\s*\{([^{}]*)\}", r"\1", source)
    source = re.sub(r"\\(?:cdot|times|ast)\b", "*", source)
    source = re.sub(r"\\div\b", "/", source)
    # subscripts and superscripts: only plain letters/digits in a subscript, an integer power
    source = re.sub(r"_\s*\{\s*([A-Za-z0-9α-ωΑ-Ω]+)\s*\}", r"\1", source)
    source = re.sub(r"_\s*([A-Za-z0-9α-ωΑ-Ω])", r"\1", source)
    source = re.sub(r"\^\s*\{\s*(-?\s*[0-9]+)\s*\}", lambda m: "^" + m.group(1).replace(" ", ""), source)
    source = re.sub(r"\^\s*(-?[0-9]+)", lambda m: "^" + m.group(1), source)
    source = _innermost_fraction(source)
    for command in re.findall(r"\\([A-Za-z]+)", source):
        if command in _GREEK:
            continue
        what = _NOT_NUMERIC.get(command)
        return Unreadable(f"it contains {what or 'notation (' + chr(92) + command + ')'} that cannot be calculated "
                          "with plain arithmetic")
    source = re.sub(r"\\([A-Za-z]+)", lambda m: _GREEK[m.group(1)], source)
    source = source.replace("[", "(").replace("]", ")")
    if "{" in source or "}" in source or "\\" in source:
        return Unreadable("it contains typeset structure that is not a plain formula")
    if not _ALLOWED.fullmatch(source):
        odd = next((c for c in source if not _ALLOWED.fullmatch(c)), "?")
        return Unreadable(f"it contains {odd!r}, which is not part of an equation RUDRA can calculate with")
    if "=" not in source:
        return Unreadable("it has no equals sign")
    return source


# ------------------------------------------------------------------ tokens and products

_TOKEN = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[^\W\d]\w*|[+\-*/^()=]|\S", re.UNICODE)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def _decompositions(token: str, atoms: Collection[str], limit: int = 3) -> list[tuple[str, ...]]:
    """Every way `token` is `atoms` written side by side (at least two), up to `limit`."""
    ordered = sorted((a for a in atoms if a and a != token), key=lambda a: (-len(a), a))
    found: list[tuple[str, ...]] = []

    def walk(position: int, parts: tuple[str, ...]) -> None:
        if len(found) >= limit:
            return
        if position == len(token):
            if len(parts) >= 2:
                found.append(parts)
            return
        for atom in ordered:
            if token.startswith(atom, position):
                walk(position + len(atom), (*parts, atom))

    walk(0, ())
    return found


def single_symbol(segment: str) -> str | None:
    """The segment's quantity when it is exactly one symbol, else None."""
    word = segment.strip()
    return word if is_symbol(word) else None


def _strip_unit(segment: str) -> tuple[str, str | None]:
    found = re.fullmatch(r"(.*[\d)])\s+([^\W\d_]+)", segment.strip())
    if found and found.group(2) in UNIT_WORDS:
        return found.group(1), found.group(2)
    return segment, None


#: Names that are operations, not quantities: an equation using one needs more than arithmetic.
_FUNCTIONS = frozenset({"sin", "cos", "tan", "cot", "sec", "csc", "sinh", "cosh", "tanh", "log", "ln", "exp",
                        "sqrt", "arg", "Re", "Im", "mod", "min", "max", "abs"})
#: Differentials, as a typeset derivative loses its fraction bar: `dV / dt` flattens to `dV dt`.
_DIFFERENTIALS = frozenset({"dt", "dx", "dy", "dz", "dr", "dθ", "dω", "dφ", "dq", "di", "dv", "dV", "dI",
                            "dW", "dE", "dP", "dQ", "dT", "dw", "dp", "ds", "du"})
_FUNCTION_LIKE = re.compile(r"[^\W\d_]\w*\(")
_LONE_ARGUMENT = re.compile(r"[^\W\d_]\w*\s*\(\s*[^\W\d_]\w*\s*\)")


def _insert_products(segment: str, atoms: Collection[str], *, first: bool = False
                     ) -> tuple[str, list[str]] | Unreadable:
    """Write quantities that stand side by side with an explicit `*`, when only one reading exists.

    The first side of an equation that is one word names the quantity the equation gives; it is
    never split into a product.
    """
    for piece in tokens(segment):
        if piece in _FUNCTIONS:
            return Unreadable(f"it uses the function {piece}, which RUDRA's arithmetic does not have")
        if piece in _DIFFERENTIALS:
            return Unreadable("it contains a derivative or differential, which is not plain arithmetic")
    if first and single_symbol(segment):
        return segment.strip(), []
    if _FUNCTION_LIKE.search(segment) or _LONE_ARGUMENT.search(segment):
        return Unreadable("it uses a quantity as a function of something (like i(t)), not as a product")
    notes: list[str] = []
    out: list[str] = []
    previous = ""
    for piece in tokens(segment):
        starts_operand = bool(re.match(r"[\w(]", piece, re.UNICODE))
        ends_operand = bool(previous) and (bool(re.match(r"\w", previous, re.UNICODE)) or previous == ")")
        if ends_operand and starts_operand:
            out.append("*")
            notes.append("quantities written side by side were read as a product")
        if is_symbol(piece) and piece not in atoms:
            readings = _decompositions(piece, atoms)
            if len(readings) > 1:
                return Unreadable(f"{piece} could be read as {' × '.join(readings[0])} or {' × '.join(readings[1])}")
            if readings:
                out.append("*".join(readings[0]))
                notes.append(f"{piece} was read as {' × '.join(readings[0])}")
                previous = piece
                continue
        out.append(piece)
        previous = piece
    return " ".join(out), notes


def read(text: str, atoms: Collection[str]) -> tuple[list[Reading], Unreadable | None]:
    """Every formula a stored equation states, or why it states none.

    `atoms` are the quantities already named - by the documents' equations and variables
    and by the question - that a product written without a sign may be made of.
    """
    converted = plain(text)
    if isinstance(converted, Unreadable):
        return [], converted
    segments = [part.strip() for part in converted.split("=")]
    if len(segments) < 2 or any(not part for part in segments):
        return [], Unreadable("it does not have a quantity on each side of an equals sign")
    cleaned: list[str] = []
    notes: list[tuple[str, ...]] = []
    for position, segment in enumerate(segments):
        segment, unit = _strip_unit(segment)
        made = _insert_products(segment, atoms, first=position == 0)
        if isinstance(made, Unreadable):
            return [], made
        text_, product_notes = made
        if unit:
            product_notes = [*product_notes, f"the unit {unit} printed after the result was dropped"]
        cleaned.append(text_)
        notes.append(tuple(dict.fromkeys(product_notes)))
    quantities = [i for i, s in enumerate(cleaned) if single_symbol(s)]
    if not quantities:
        return [], Unreadable("no single quantity stands alone on one side, so it is a relation between "
                              "expressions rather than a formula for one quantity")
    readings: list[Reading] = []
    seen: set[str] = set()
    failure: Unreadable | None = None
    for i in quantities:
        for j, other in enumerate(cleaned):
            if i == j or (j in quantities and j < i):
                continue
            try:
                parsed = parse_formula(f"{cleaned[i]} = {other}")
                formula = parse_formula(f"{parsed.target} = {render(parsed.expression)}")
            except FormulaError as error:
                failure = Unreadable(str(error))
                continue
            if formula.text in seen:
                continue
            seen.add(formula.text)
            readings.append(Reading(formula, tuple(dict.fromkeys((*notes[i], *notes[j])))))
    if not readings:
        return [], failure or Unreadable("it is outside the formula grammar")
    return readings, None
