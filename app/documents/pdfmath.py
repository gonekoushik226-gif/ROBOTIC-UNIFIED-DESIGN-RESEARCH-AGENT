"""Display equations rebuilt from where a PDF page places its glyphs.

A PDF's text layer is a stream of glyph runs put at positions on the page. Read as plain
text it flattens mathematics: a fraction's numerator and denominator become separate
lines, a superscript joins its base (``BW = R/L`` arrives as ``BW = R`` and ``L``). This
module reads the placement instead - each run's baseline, extent and size, and the thin
rules the page draws - and rebuilds display equations in RUDRA's linear notation
(``app/documents/mathmarkup.py``).

Structure is taken only from geometry:

* **fraction** - a horizontal rule with glyphs above and below it, inside its width;
* **superscript / subscript** - a glyph raised / lowered after its base, within a
  script's distance;
* **limits** - glyphs above / below a large or ordinary big operator (∫ ∑ ∏ ...);
* **radical** - a √ with a bar that starts at its right edge; the bar covers the radicand;
* **matrix, cases** - tall delimiters around rows whose cells line up in columns;
* **aligned lines** - consecutive equations whose relation signs share one x position;
* **equation number** - a parenthesised number set far to the right.

Only display equations are rebuilt: lines that carry a relation or a big operator and
next to no prose. Mathematics inside a sentence is left as the text layer gives it.

**Nothing is invented.** Every glyph of an equation appears in the result exactly once.
Whatever the geometry does not settle - a glyph neither on the baseline nor in a script,
fraction or limit position; a rule with nothing above or below; a radical without a bar;
cells that do not line up; a glyph the font could not decode; glyphs printed over one
another; a glyph inside the equation's area that belongs to none of its structures - is
recorded as a *reason*, and the equation is then **uncertain**: the page keeps its
original text, and the rebuilt form is kept only as a tentative reading beside it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import cached_property

from app.documents.mathmarkup import _BIG, _FUNCTIONS, _SYMBOL_COMMANDS, _escape

#: How the notation of a rebuilt equation is produced, for provenance.
METHOD = "pdf-glyph-layout"
FORMAT_VERSION = 1

RELATIONS = frozenset("=<>≤≥≠≈≡∝→⇒⇔←↔∈∉⊂⊆⊃⊇≅∼≪≫")
BINARY = frozenset("+-−±∓×·⋅÷∗")
OPENING = {"(": ")", "[": "]", "{": "}", "|": "|", "‖": "‖", "⟨": "⟩"}
CLOSING = frozenset(OPENING.values())
ENVIRONMENTS = {"(": "pmatrix", "[": "bmatrix", "|": "vmatrix", "‖": "Vmatrix", "{": "Bmatrix"}
ACCENTS = {"^": r"\hat", "ˆ": r"\hat", "~": r"\tilde", "˜": r"\tilde", "¯": r"\bar", "‾": r"\bar",
           "˙": r"\dot", "¨": r"\ddot", "→": r"\vec", "⃗": r"\vec"}
_NUMBER = re.compile(r"^\(\s*([A-Z]?\d+(?:[.\-–]\d+)*[a-z]?)\s*\)$")
_PROSE_WORD = re.compile(r"[A-Za-z]{3,}")
#: The relations that make a line an equation (an arrow alone does not).
EQUATION_RELATIONS = frozenset("=<>≤≥≠≈≡∝≅∼")
#: A line opening with a condition states circumstances, not an equation.
_CONDITION = re.compile(r"^(?:if|given|when|whenever|suppose|for|at|where|since|because|then|let)\b", re.I)


@dataclass(frozen=True)
class Glyph:
    """One run of text as the page places it: its extent, baseline and effective size."""

    text: str
    x0: float
    x1: float
    #: The baseline, in page units with y growing upwards.
    y: float
    size: float
    #: Position in the content stream, which is the order of the page's plain text.
    order: int = 0
    font: str = ""
    #: False when the font could not be decoded or the run is rotated.
    readable: bool = True

    @property
    def top(self) -> float:
        return self.y + 0.72 * self.size

    @property
    def bottom(self) -> float:
        return self.y - 0.22 * self.size

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(frozen=True)
class Rule:
    """A thin horizontal line the page draws: a fraction bar or a radical's bar."""

    x0: float
    x1: float
    y: float
    thickness: float = 0.5

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(frozen=True)
class LaidOutEquation:
    """One display equation read from the page's geometry."""

    linear: str
    #: The equation's glyphs in content-stream order: what the plain text layer holds.
    flat: str
    #: Why the reading is not certain; empty when every glyph's place was settled.
    reasons: tuple[str, ...]
    #: The structural decisions taken, with the geometry behind each.
    evidence: tuple[str, ...]
    bbox: tuple[float, float, float, float]
    orders: tuple[int, ...]
    number: str | None = None

    @property
    def certain(self) -> bool:
        return not self.reasons


@dataclass(frozen=True)
class Placement:
    """Where an equation sits in the page's final text, and whether its rebuilt form replaced the flat one.

    A certain equation with structure (a fraction, a script, a radical ...) replaces its
    flat text; a certain one without any - every glyph on one baseline, nothing above,
    below or beside it - leaves the text exactly as it was, which is then known to be
    the whole equation. An uncertain one leaves the text as it was.
    """

    equation: LaidOutEquation
    span: tuple[int, int] | None
    substituted: bool
    reasons: tuple[str, ...]
    #: The page text's own characters for the equation, before any replacement.
    original: str = ""

    @property
    def certain(self) -> bool:
        return self.span is not None and not self.reasons

    def to_json(self) -> dict:
        e = self.equation
        return {"linear": e.linear, "flat": e.flat, "page_text": self.original, "certain": self.certain,
                "substituted": self.substituted,
                "span": list(self.span) if self.span else None, "reasons": list(self.reasons),
                "evidence": list(e.evidence), "bbox": [round(v, 2) for v in e.bbox], "number": e.number}


# ---------------------------------------------------------------------- text of a glyph


def _symbols(text: str) -> str:
    stripped = text.strip()
    if stripped in _FUNCTIONS:
        return "\\" + stripped + " "
    return "".join(" " + _SYMBOL_COMMANDS[c] + " " if c in _SYMBOL_COMMANDS else _escape(c) for c in text)


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([}^_])", r"\1", re.sub(r"([{^_])\s+", r"\1", text))


def _group(text: str) -> str:
    return "{" + text.strip() + "}"


def _unreadable(text: str) -> bool:
    return any(c == "�" or "" <= c <= "" or (unicodedata.category(c) == "Cc" and c not in "\t")
               for c in text)


def _has_relation(text: str) -> bool:
    return any(c in RELATIONS for c in text)


# --------------------------------------------------------------------------- lines


@dataclass
class _Line:
    glyphs: list[Glyph]

    @property
    def y(self) -> float:
        return self.glyphs[0].y

    @property
    def size(self) -> float:
        return max(g.size for g in self.glyphs)

    @property
    def x0(self) -> float:
        return min(g.x0 for g in self.glyphs)

    @property
    def x1(self) -> float:
        return max(g.x1 for g in self.glyphs)

    @property
    def top(self) -> float:
        return max(g.top for g in self.glyphs)

    @property
    def bottom(self) -> float:
        return min(g.bottom for g in self.glyphs)

    @cached_property
    def text(self) -> str:
        return " ".join(g.text for g in sorted(self.glyphs, key=lambda g: g.x0))

    @cached_property
    def prose(self) -> int:
        return sum(1 for w in _PROSE_WORD.findall(self.text) if w.lower() not in _FUNCTIONS)

    def prose_words(self) -> int:
        return self.prose


def _lines(glyphs: list[Glyph]) -> list[_Line]:
    """Glyphs on one baseline and of a similar size, split where a wide gap separates columns."""
    rows: list[list[Glyph]] = []
    for g in sorted(glyphs, key=lambda g: (-g.y, g.x0)):
        for row in rows:
            first = row[0]
            if abs(first.y - g.y) <= 0.2 * min(first.size, g.size) and 0.75 <= g.size / first.size <= 1.33:
                row.append(g)
                break
        else:
            rows.append([g])
    lines = []
    for row in rows:
        ordered = sorted(row, key=lambda g: g.x0)
        pieces = [[ordered[0]]]
        for g in ordered[1:]:
            if g.x0 - max(h.x1 for h in pieces[-1]) > 3.0 * ordered[0].size:
                pieces.append([g])
            else:
                pieces[-1].append(g)
        for piece in pieces:
            # An equation number set far to the right stays with its equation.
            if lines and lines[-1].y == row[0].y and _NUMBER.match("".join(g.text for g in piece).strip()):
                lines[-1].glyphs.extend(piece)
            else:
                lines.append(_Line(piece))
    return lines


# ---------------------------------------------------------------------- regions


@dataclass
class _Region:
    seed: _Line
    glyphs: list[Glyph] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)

    @property
    def size(self) -> float:
        return self.seed.size

    @property
    def axis(self) -> float:
        return self.seed.y + 0.25 * self.size

    @property
    def x0(self) -> float:
        return min([g.x0 for g in self.glyphs] + [r.x0 for r in self.rules])

    @property
    def x1(self) -> float:
        return max([g.x1 for g in self.glyphs] + [r.x1 for r in self.rules])

    @property
    def top(self) -> float:
        return max([g.top for g in self.glyphs] + [r.y for r in self.rules])

    @property
    def bottom(self) -> float:
        return min([g.bottom for g in self.glyphs] + [r.y for r in self.rules])


def _is_seed(line: _Line) -> bool:
    """A display equation's main line: a relation sign, symbols on it, and at most one word of prose."""
    text = line.text.strip()
    if not any(c in EQUATION_RELATIONS for c in text) or not any(c.isalnum() for c in text):
        return False
    return not _CONDITION.match(text) and line.prose_words() <= 1


def _script_owners(lines: list[_Line]) -> dict[int, int | None]:
    """For each line that could be a script, the glyph it is a script of - None when two bases fit equally.

    A superscript of one line can sit as close to the line above as a subscript would;
    it belongs to the base whose baseline it is nearest, directly after which it starts.
    """
    owners: dict[int, int | None] = {}
    largest = max((line.size for line in lines), default=0.0)
    for line in lines:
        if line.size > 0.85 * largest:
            continue
        scored = []
        for other in lines:
            if other is line or other.size < line.size or abs(other.y - line.y) > 0.9 * other.size:
                continue
            for g in other.glyphs:
                gap = line.x0 - g.x1
                if line.size <= 0.85 * g.size and -0.3 * g.size <= gap <= 0.6 * g.size \
                        and abs(line.y - g.y) <= 0.9 * g.size:
                    scored.append((abs(line.y - g.y) / g.size + abs(gap) / g.size * 0.5, id(g), id(other)))
        if not scored:
            continue
        scored.sort()
        rivals = [t for t in scored[1:] if t[2] != scored[0][2] and t[0] - scored[0][0] < 0.15]
        owners[id(line)] = None if rivals else scored[0][1]
    return owners


def _radical_bar(rule: Rule, glyphs: list[Glyph]) -> Glyph | None:
    """The √ whose right edge a rule starts at, when the rule is that radical's bar."""
    for g in glyphs:
        if g.text.strip() == "√" and abs(rule.x0 - g.x1) <= 0.5 * g.size and g.y + 0.3 * g.size <= rule.y <= g.top + 0.5 * g.size:
            return g
    return None


def _attach_rule(region: _Region, rule: Rule) -> bool:
    s = region.size
    if rule.x1 - rule.x0 < 0.25 * s or rule.x1 - rule.x0 > 40 * s:
        return False
    if _radical_bar(rule, region.glyphs) is not None:
        return True
    # A fraction bar in line with the equation's axis, touching or inside its extent.
    if abs(rule.y - region.axis) <= 0.4 * s and rule.x0 <= region.x1 + 1.2 * s and rule.x1 >= region.x0 - 1.2 * s:
        return True
    # A bar nested inside another bar's numerator or denominator.
    return any(rule.x0 >= r.x0 - 0.2 * s and rule.x1 <= r.x1 + 0.2 * s and 0 < abs(rule.y - r.y) <= 2.2 * s
               for r in region.rules)


def _attach_line(region: _Region, line: _Line, *, seed: bool = False, owners: dict | None = None) -> bool:
    """Whether geometry ties a line to the region: as a script, a fraction's part, limits or rows.

    A line that is itself an equation's main line (`seed`) joins only as a numerator,
    denominator, limit or row - never as a script or a tall glyph beside the region.
    """
    s = region.size
    if abs(line.y - region.seed.y) > 3.2 * s or line.x1 < region.x0 - 1.0 * s or line.x0 > region.x1 + 1.0 * s:
        return False
    if line.prose_words() > 1:
        return False
    # (a) a script: smaller, close to the baseline of the glyph just before it - the best
    # such base on the page, not merely one in this region.
    owner = (owners or {}).get(id(line))
    if not seed and owner is not None and any(id(g) == owner for g in region.glyphs):
        return True
    # (b) a numerator or denominator: just above or below a bar, inside its width.
    for r in region.rules:
        if r.x0 - 0.3 * s <= (line.x0 + line.x1) / 2 <= r.x1 + 0.3 * s:
            if line.y >= r.y and line.bottom - r.y <= 1.0 * s:
                return True
            if line.y < r.y and line.top <= r.y + 0.2 * s and r.y - line.top <= 1.0 * s:
                return True
    for g in region.glyphs:
        # (c) limits above or below a big operator.
        if g.text.strip() in _BIG and g.x0 - 0.6 * s <= (line.x0 + line.x1) / 2 <= g.x1 + 0.6 * s:
            if -0.3 * line.size <= line.bottom - g.top <= 0.8 * s or -0.3 * line.size <= g.bottom - line.top <= 0.8 * s:
                return True
        # (d) rows inside a tall delimiter (a matrix, cases).
        if g.text.strip() in OPENING and g.size >= 1.4 * s and line.x0 >= g.x1 - 0.2 * s \
                and g.bottom - 0.2 * s <= line.y and line.top <= g.top + 0.3 * s:
            return True
    # (e) tall glyphs (big operators, delimiters) spanning the equation's axis beside it.
    if not seed and all(g.size >= 1.2 * s and g.bottom <= region.axis <= g.top for g in line.glyphs):
        return line.x0 <= region.x1 + 1.0 * s and line.x1 >= region.x0 - 1.0 * s
    return False


def _anchor(line: _Line, lines: list[_Line], rules: list[Rule]) -> float:
    """How squarely a bar or a tall glyph sits on this line's axis (0 is exactly; inf is not at all).

    The main line of a fraction has the bar on its axis; the main line of a matrix or of
    cases has the tall delimiter centred on its axis - its rows sit higher and lower.
    """
    s = line.size
    axis = line.y + 0.25 * s
    scores = [abs(r.y - axis) for r in rules
              if abs(r.y - axis) <= 0.4 * s and line.x0 - 1.2 * s <= r.x0 <= line.x1 + 1.2 * s]
    scores += [abs((o.top + o.bottom) / 2 - axis) for o in lines if o is not line
               and all(g.size >= 1.4 * s for g in o.glyphs) and o.bottom <= axis <= o.top
               and o.x0 <= line.x1 + 1.0 * s and o.x1 >= line.x0 - 1.0 * s]
    return min(scores, default=float("inf"))


def _regions(glyphs: list[Glyph], rules: list[Rule]) -> tuple[list[_Region], list[_Line]]:
    lines = _lines(glyphs)
    seeds = [i for i, line in enumerate(lines) if _is_seed(line)]
    owners = _script_owners(lines)
    seeds.sort(key=lambda i: (_anchor(lines[i], lines, rules), i))
    taken: set[int] = set()
    regions = []
    for index in seeds:
        if index in taken:
            continue
        line = lines[index]
        region = _Region(line, list(line.glyphs))
        taken.add(index)
        free_rules = [r for r in rules if not any(r in other.rules for other in regions)]
        changed = True
        while changed:
            changed = False
            for rule in free_rules:
                if rule not in region.rules and _attach_rule(region, rule):
                    region.rules.append(rule)
                    changed = True
            for other, candidate in enumerate(lines):
                if other not in taken and _attach_line(region, candidate, seed=other in seeds, owners=owners):
                    region.glyphs.extend(candidate.glyphs)
                    taken.add(other)
                    changed = True
        regions.append(region)
    regions.sort(key=lambda r: min(g.order for g in r.glyphs))
    return regions, lines


# ------------------------------------------------------------------------ parsing


@dataclass
class _Token:
    text: str
    x0: float
    x1: float
    relation: bool = False
    #: Where the first relation sign is (estimated inside a run of several characters).
    rx: float | None = None
    #: The run's own text, for a plain run with no script or accent.
    source: str | None = None


@dataclass(eq=False)
class _Element:
    """Something on a baseline: a glyph run, or a structure that owns the glyphs it dominates."""

    x0: float
    x1: float
    kind: str
    payload: object
    sup: list[Glyph] = field(default_factory=list)
    sub: list[Glyph] = field(default_factory=list)
    over: list[Glyph] = field(default_factory=list)


class _Parser:
    """A baseline-structure reading of one region: every decision recorded, every doubt a reason."""

    def __init__(self) -> None:
        self.reasons: list[str] = []
        self.evidence: list[str] = []
        self.used: set[int] = set()

    def doubt(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)

    def atom(self, g: Glyph) -> str:
        self.used.add(id(g))
        if not g.readable or _unreadable(g.text):
            self.doubt(f"the glyph run {g.text!r} could not be decoded reliably")
        return _symbols(g.text)

    @staticmethod
    def join(tokens: list[_Token], size: float) -> str:
        out = ""
        previous = None
        for token in tokens:
            if previous is not None and (token.x0 - previous.x1 >= 0.15 * size or token.relation or previous.relation
                                         or token.text.strip() in BINARY or previous.text.strip() in BINARY):
                out += " "
            out += token.text
            previous = token
        return _tidy(out)

    def parse(self, glyphs: list[Glyph], rules: list[Rule]) -> str:
        size = max((g.size for g in glyphs), default=10.0)
        return self.join(self.tokens(glyphs, rules), size)

    # --- structures that dominate other glyphs

    def _radicals(self, glyphs, rules, s, taken, taken_rules) -> list[_Element]:
        out = []
        for rule in sorted(rules, key=lambda r: r.x1 - r.x0, reverse=True):
            root = _radical_bar(rule, glyphs)
            if root is None or id(root) in taken or id(rule) in taken_rules:
                continue
            under = [g for g in glyphs if g is not root and id(g) not in taken and rule.x0 - 0.1 * s <= g.cx <= rule.x1
                     and g.top <= rule.y + 0.25 * g.size and g.y >= root.bottom - 0.6 * s]
            inner = [r for r in rules if r is not rule and id(r) not in taken_rules
                     and r.x0 >= rule.x0 - 0.1 * s and r.x1 <= rule.x1 + 0.1 * s and r.y < rule.y]
            index = [g for g in glyphs if g is not root and id(g) not in taken and g not in under
                     and g.x1 <= root.cx + 0.2 * root.size and g.x0 >= root.x0 - 1.5 * s
                     and g.y >= root.y + 0.3 * root.size and g.size < 0.9 * root.size]
            taken.update(id(g) for g in (root, *under, *index))
            taken_rules.update(id(r) for r in (rule, *inner))
            x0 = min([root.x0, *(g.x0 for g in index)])
            out.append(_Element(x0, rule.x1, "radical", (root, rule, index, under, inner)))
            self.evidence.append(f"radical: bar {rule.x0:.1f}-{rule.x1:.1f} at y {rule.y:.1f} begins at the √'s "
                                 f"right edge and covers {len(under)} glyph run(s)"
                                 + (f"; index of {len(index)} run(s)" if index else ""))
        for g in glyphs:
            if g.text.strip() == "√" and id(g) not in taken:
                self.doubt("a radical sign has no bar, so what it covers cannot be told")
        return out

    def _fractions(self, glyphs, rules, s, taken, taken_rules) -> list[_Element]:
        out = []
        tol = 0.3 * s
        for bar in sorted(rules, key=lambda r: r.x1 - r.x0, reverse=True):
            if id(bar) in taken_rules:
                continue
            within = [g for g in glyphs if id(g) not in taken and bar.x0 - tol <= g.cx <= bar.x1 + tol]
            above = [g for g in within if g.y >= bar.y and g.bottom - bar.y <= 3.0 * s]
            below = [g for g in within if g.y < bar.y and g.top <= bar.y + 0.25 * g.size and bar.y - g.top <= 3.0 * s]
            if not above and not below:
                self.doubt("a horizontal rule has no glyphs above or below it")
                taken_rules.add(id(bar))
                continue
            crossing = [g for g in glyphs if id(g) not in taken and g not in above and g not in below
                        and g.bottom < bar.y < g.top and g.x0 < bar.x1 - tol and g.x1 > bar.x0 + tol]
            if crossing:
                self.doubt("a glyph crosses a fraction bar")
            if not above or not below:
                self.doubt("a horizontal rule has glyphs on one side only, so it may not be a fraction bar")
            nested = [r for r in rules if r is not bar and id(r) not in taken_rules
                      and r.x0 >= bar.x0 - tol and r.x1 <= bar.x1 + tol and 0 < abs(r.y - bar.y) <= 3.0 * s]
            taken.update(id(g) for g in above + below)
            taken_rules.update(id(r) for r in (bar, *nested))
            items = above + below
            out.append(_Element(min([bar.x0, *(g.x0 for g in items)]), max([bar.x1, *(g.x1 for g in items)]),
                                "fraction", (bar, above, below, nested)))
            self.evidence.append(f"fraction: bar {bar.x0:.1f}-{bar.x1:.1f} at y {bar.y:.1f}, {len(above)} glyph "
                                 f"run(s) above it and {len(below)} below")
        return out

    def _limits(self, glyphs, s, taken) -> list[_Element]:
        out = []
        for g in sorted(glyphs, key=lambda g: g.x0):
            if g.text.strip() not in _BIG or id(g) in taken:
                continue
            # Limits are centred over or under the operator; scripts start after its right edge.
            limits = [h for h in glyphs if h is not g and id(h) not in taken and h.x0 < g.x1 - 0.1 * s
                      and abs(h.cx - g.cx) <= max(g.x1 - g.x0, h.x1 - h.x0) / 2 * 0.6 + 0.1 * s
                      and (h.bottom >= g.top - 0.2 * h.size or h.top <= g.bottom + 0.2 * h.size)]
            if not limits:
                continue
            taken.update(id(h) for h in (g, *limits))
            out.append(_Element(min(h.x0 for h in (g, *limits)), max(h.x1 for h in (g, *limits)), "limits",
                                (g, limits)))
            self.evidence.append(f"limits: {len(limits)} glyph run(s) set above/below the {g.text.strip()} "
                                 f"at x {g.x0:.1f}")
        return out

    def _fences(self, glyphs, rules, s, taken, taken_rules) -> list[_Element]:
        out = []
        for left in sorted(glyphs, key=lambda g: g.x0):
            opening = left.text.strip()
            if opening not in OPENING or id(left) in taken or left.size < 1.4 * s:
                continue
            rights = [g for g in glyphs if g is not left and id(g) not in taken and g.text.strip() == OPENING[opening]
                      and g.x0 > left.x1 and g.size >= 0.8 * left.size and abs(g.y - left.y) <= 0.3 * left.size]
            right = min(rights, key=lambda g: g.x0) if rights else None
            if right is None and opening != "{":
                continue
            end = right.x0 if right is not None else max(h.x1 for h in glyphs) + 1
            inside = [g for g in glyphs if g is not left and g is not right and id(g) not in taken
                      and left.x1 - 0.1 * s <= g.cx <= end and left.bottom - 0.3 * s <= g.y <= left.top + 0.3 * s]
            inner = [r for r in rules if id(r) not in taken_rules and r.x0 >= left.x1 - 0.1 * s and r.x1 <= end]
            taken.update(id(g) for g in (left, *inside, *([right] if right else [])))
            taken_rules.update(id(r) for r in inner)
            x1 = right.x1 if right is not None else max([left.x1, *(g.x1 for g in inside)])
            out.append(_Element(left.x0, x1, "fence", (left, right, inside, inner)))
        return out

    # --- one level

    def tokens(self, glyphs: list[Glyph], rules: list[Rule]) -> list[_Token]:
        if not glyphs:
            if rules:
                self.doubt("a horizontal rule has no glyphs above or below it")
            return []
        sizes = sorted(g.size for g in glyphs)
        median = sizes[len(sizes) // 2]
        s = max(size for size in sizes if size <= 1.3 * median)
        taken: set[int] = set()
        taken_rules: set[int] = set()
        structures = (self._radicals(glyphs, rules, s, taken, taken_rules)
                      + self._fractions(glyphs, rules, s, taken, taken_rules)
                      + self._limits(glyphs, s, taken)
                      + self._fences(glyphs, rules, s, taken, taken_rules))
        free = [g for g in glyphs if id(g) not in taken]
        largest = max((g.size for g in free if g.text.strip() not in _BIG), default=0.0)
        reference = [g for g in free if g.size >= 0.9 * largest and g.text.strip() not in _BIG]
        base = min(reference, key=lambda g: g.x0) if reference else None
        if base is not None:
            y, s = base.y, base.size
        line: list[_Element] = list(structures)
        scripts: list[Glyph] = []
        for g in free:
            on_baseline = base is None or (abs(g.y - y) <= 0.15 * s and g.size >= 0.8 * s) \
                or (g.text.strip() in _BIG and g.bottom <= y + 0.25 * s <= g.top)
            if on_baseline:
                line.append(_Element(g.x0, g.x1, "glyph", g))
            else:
                scripts.append(g)
        line.sort(key=lambda e: e.x0)
        for a, b in zip(line, line[1:]):
            overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
            if a.kind == b.kind == "glyph" and overlap > 0.5 * min(a.x1 - a.x0, b.x1 - b.x0) > 0:
                self.doubt("glyphs are printed over one another")
        for g in sorted(scripts, key=lambda g: g.x0):
            before = [e for e in line if e.x0 <= g.x0 + 0.1 * s]
            if not before:
                self.doubt(f"the glyph run {g.text!r} sits before the start of the equation's baseline")
                line.insert(0, _Element(g.x0, g.x1, "glyph", g))
                continue
            owner = max(before, key=lambda e: e.x0)
            if owner.kind == "glyph" and g.text.strip() in ACCENTS and owner.x0 - 0.1 * s <= g.cx <= owner.x1 + 0.1 * s \
                    and g.bottom >= owner.payload.top - 0.35 * s:
                owner.over.append(g)
            elif g.y - y > 0.12 * s:
                owner.sup.append(g)
            elif y - g.y > 0.08 * s:
                owner.sub.append(g)
            else:
                self.doubt(f"a smaller glyph run {g.text!r} sits on the baseline")
                (owner.sup if g.y >= y else owner.sub).append(g)
        return [self.token(e, s) for e in line]

    def token(self, element: _Element, s: float) -> _Token:
        text = self.element(element, s)
        shown = text.strip()
        relation = element.kind == "glyph" and _has_relation(element.payload.text)
        if element.over:
            accent = element.over[0]
            self.used.add(id(accent))
            text = ACCENTS[accent.text.strip()] + _group(text)
            self.evidence.append(f"accent: {accent.text.strip()!r} over {shown!r}")
            if len(element.over) > 1:
                self.doubt("more than one mark sits above one glyph")
                element.sup.extend(element.over[1:])
        simple = len(shown) <= 1 or shown.isalnum() or re.fullmatch(r"\\[A-Za-z]+", shown) is not None
        if (element.sub or element.sup) and not simple and not element.over:
            text = _group(text)
        if element.sub:
            text += "_" + _group(self.parse(element.sub, []))
            self.evidence.append(f"subscript: {len(element.sub)} run(s) lowered after {shown!r}")
        if element.sup:
            text += "^" + _group(self.parse(element.sup, []))
            self.evidence.append(f"superscript: {len(element.sup)} run(s) raised after {shown!r}")
        rx = source = None
        if relation:
            raw = element.payload.text
            at = next(i for i, c in enumerate(raw) if c in RELATIONS)
            rx = element.x0 + (element.x1 - element.x0) * at / max(len(raw), 1)
            if not (element.sub or element.sup or element.over):
                source = raw
        return _Token(text, element.x0, element.x1, relation, rx, source)

    def element(self, element: _Element, s: float) -> str:
        kind, payload = element.kind, element.payload
        if kind == "glyph":
            return self.atom(payload)
        if kind == "fraction":
            bar, above, below, nested = payload
            return (r"\frac" + _group(self.parse(above, [r for r in nested if r.y > bar.y]))
                    + _group(self.parse(below, [r for r in nested if r.y < bar.y])))
        if kind == "radical":
            root, _rule, index, under, inner = payload
            self.used.add(id(root))
            degree = f"[{self.parse(index, [])}]" if index else ""
            return r"\sqrt" + degree + _group(self.parse(under, inner))
        if kind == "limits":
            operator, limits = payload
            upper = [g for g in limits if g.y > operator.y]
            lower = [g for g in limits if g.y <= operator.y]
            text = self.atom(operator).strip()
            if lower:
                text += "_" + _group(self.parse(lower, []))
            if upper:
                text += "^" + _group(self.parse(upper, []))
            return text
        return self.fence(payload, s)

    def fence(self, payload, s: float) -> str:
        left, right, items, inner = payload
        self.used.add(id(left))
        if right is not None:
            self.used.add(id(right))
        opening = left.text.strip()
        rows = self.rows(items)
        if len(rows) < 2:
            close = right.text.strip() if right is not None else "."
            return rf"\left{_escape(opening)} {self.parse(items, inner)} \right{_escape(close)}"
        if inner:
            self.doubt("a rule between tall delimiters is not read as a fraction")
        if right is None:
            cells = [self.cells(row, s, split_largest=True) for row in rows]
            self.evidence.append(f"cases: {len(rows)} rows beside a tall {{")
            body = r" \\ ".join(" & ".join(self.parse(c, []) for c in row) for row in cells)
            return r"\begin{cases} " + body + r" \end{cases}"
        cells = [self.cells(row, s) for row in rows]
        counts = {len(row) for row in cells}
        if len(counts) != 1:
            self.doubt("the matrix rows do not have the same number of cells")
        else:
            for column in range(next(iter(counts))):
                centres = [(min(g.x0 for g in row[column]) + max(g.x1 for g in row[column])) / 2 for row in cells]
                if max(centres) - min(centres) > 1.5 * s:
                    self.doubt("the matrix cells do not line up in columns")
        name = ENVIRONMENTS.get(opening, "matrix")
        self.evidence.append(f"matrix: {len(rows)} rows × {max(len(r) for r in cells)} columns between a tall "
                             f"{opening} and {right.text.strip()}")
        body = r" \\ ".join(" & ".join(self.parse(c, []) for c in row) for row in cells)
        return rf"\begin{{{name}}} " + body + rf" \end{{{name}}}"

    @staticmethod
    def rows(items: list[Glyph]) -> list[list[Glyph]]:
        """Rows of a matrix: full-size glyphs grouped by baseline, smaller ones joining the nearest row."""
        if not items:
            return []
        big = max(g.size for g in items)
        rows: list[list[Glyph]] = []
        for g in sorted((g for g in items if g.size >= 0.85 * big), key=lambda g: -g.y):
            if rows and abs(rows[-1][0].y - g.y) <= 0.4 * big:
                rows[-1].append(g)
            else:
                rows.append([g])
        for g in items:
            if g.size < 0.85 * big:
                min(rows, key=lambda row: abs(row[0].y - g.y)).append(g)
        return rows

    @staticmethod
    def cells(row: list[Glyph], s: float, *, split_largest: bool = False) -> list[list[Glyph]]:
        ordered = sorted(row, key=lambda g: g.x0)
        gaps = [(b.x0 - max(g.x1 for g in ordered[:i + 1]), i) for i, b in enumerate(ordered[1:])]
        if split_largest:
            wide = [gap for gap in gaps if gap[0] >= 1.0 * s]
            cuts = {max(wide)[1]} if wide else set()
        else:
            cuts = {i for gap, i in gaps if gap >= 0.8 * s}
        cells: list[list[Glyph]] = [[]]
        for i, g in enumerate(ordered):
            cells[-1].append(g)
            if i in cuts:
                cells.append([])
        return [c for c in cells if c]


# --------------------------------------------------------------------- equations


def _flat(glyphs: list[Glyph]) -> str:
    return "".join(g.text for g in sorted(glyphs, key=lambda g: g.order))


def _read(region: _Region) -> tuple[list[_Token], _Parser]:
    parser = _Parser()
    tokens = parser.tokens(region.glyphs, region.rules)
    missing = [g for g in region.glyphs if id(g) not in parser.used]
    if missing:
        parser.doubt(f"{len(missing)} glyph run(s) of the equation were not placed in its structure")
        tokens.extend(_Token(parser.atom(g), g.x0, g.x1) for g in missing)
    return tokens, parser


def _number(tokens: list[_Token], size: float) -> tuple[list[_Token], str | None]:
    """A trailing '(2.13)' set well apart from the equation is its number."""
    for count in (1, 2, 3):
        if len(tokens) <= count:
            break
        tail = tokens[-count:]
        match = _NUMBER.match("".join(t.text.strip() for t in tail))
        if match and tail[0].x0 - tokens[-count - 1].x1 >= 1.5 * size:
            return tokens[:-count], match.group(1)
    return tokens, None


def _relation_x(tokens: list[_Token]) -> float | None:
    for t in tokens:
        if t.relation:
            return t.rx if t.rx is not None else t.x0
    return None


def _split_at_relation(tokens: list[_Token]) -> tuple[list[_Token], list[_Token]]:
    """The tokens before the first relation sign and from it on, splitting a plain run at the sign."""
    for i, t in enumerate(tokens):
        if not t.relation:
            continue
        if t.source is not None:
            at = next(j for j, c in enumerate(t.source) if c in RELATIONS)
            if t.source[:at].strip():
                rx = t.rx if t.rx is not None else t.x0
                return (tokens[:i] + [_Token(_symbols(t.source[:at]), t.x0, rx)],
                        [_Token(_symbols(t.source[at:]), rx, t.x1, True, rx)] + tokens[i + 1:])
        return tokens[:i], tokens[i:]
    return tokens, []


def _unaccounted(region: _Region, lines: list[_Line], used: set[int]) -> int:
    """Glyphs of no equation inside its area, right beside it, or just above or below it.

    A prose line above or below a display equation is separate text; a lone glyph that
    close, with no bar or operator to explain it, may belong to the equation.
    """
    s = region.size
    x0, x1, bottom, top = region.x0, region.x1, region.bottom, region.top
    count = 0
    for line in lines:
        near_line = line.prose_words() <= 1
        for g in line.glyphs:
            if id(g) in used:
                continue
            inside = x0 - 0.2 * s <= g.cx <= x1 + 0.2 * s and bottom <= g.y <= top
            beside = g.bottom < top and g.top > bottom and (x0 - 1.5 * s <= g.x1 <= x0 or x1 <= g.x0 <= x1 + 1.5 * s)
            close = near_line and g.x1 > x0 and g.x0 < x1 and (0 <= g.bottom - top <= 0.5 * s
                                                               or 0 <= bottom - g.top <= 0.5 * s)
            count += inside or beside or close
    return count


def equations(glyphs: list[Glyph], rules: list[Rule]) -> list[LaidOutEquation]:
    """The display equations of one page, in reading order, each with its evidence and any doubt."""
    regions, lines = _regions(glyphs, rules)
    read = []
    in_regions: set[int] = {id(g) for r in regions for g in r.glyphs}
    for region in regions:
        tokens, parser = _read(region)
        tokens, number = _number(tokens, region.size)
        if number:
            parser.evidence.append(f"equation number ({number}) set apart at the right")
        stray = _unaccounted(region, lines, in_regions)
        if stray:
            parser.doubt(f"{stray} glyph run(s) inside, beside, just above or just below the equation belong to "
                         "none of its structures")
        read.append((region, tokens, number, parser))

    # A line that begins with its relation sign continues the equation above it when the
    # signs share one x position: together they are one aligned derivation. Lines with a
    # left-hand side of their own stay separate equations, however their signs line up.
    out: list[LaidOutEquation] = []
    i = 0
    while i < len(read):
        group = [read[i]]
        while i + len(group) < len(read):
            region, tokens, number, _ = read[i + len(group)]
            last_region, last_tokens, last_number, _ = group[-1]
            x_now, x_last = _relation_x(tokens), _relation_x(last_tokens)
            if (x_now is None or x_last is None or _split_at_relation(tokens)[0]
                    or abs(x_now - x_last) > 0.6 * region.size
                    or last_region.bottom - region.top > 1.6 * region.size or last_number):
                break
            group.append(read[i + len(group)])
        i += len(group)
        size = group[0][0].size
        reasons = [r for g in group for r in g[3].reasons]
        evidence = [e for g in group for e in g[3].evidence]
        number = next((g[2] for g in group if g[2]), None)
        if len(group) == 1:
            body = group[0][3].join(group[0][1], size)
        else:
            lines = []
            for _region, tokens, _n, parser in group:
                left, right = _split_at_relation(tokens)
                lines.append(parser.join(left, size) + " &" + (" " + parser.join(right, size) if right else ""))
            body = r"\begin{aligned} " + r" \\ ".join(line.strip() for line in lines) + r" \end{aligned}"
            evidence.append(f"aligned: {len(group)} lines with their relation signs at x "
                            f"{_relation_x(group[0][1]):.1f}")
        linear = _tidy(body + (rf" \tag{{{number}}}" if number else ""))
        members = [g for item in group for g in item[0].glyphs]
        boxes = [(item[0].x0, item[0].bottom, item[0].x1, item[0].top) for item in group]
        bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        out.append(LaidOutEquation(linear, _flat(members), tuple(dict.fromkeys(reasons)), tuple(evidence), bbox,
                                   tuple(sorted(g.order for g in members)), number))
    return out


# ------------------------------------------------------------ placing in the page text


def _normalised(text: str) -> tuple[str, list[int]]:
    """The text without whitespace, compatibility-normalised, with each character's index in the original."""
    chars: list[str] = []
    index: list[int] = []
    for i, c in enumerate(text):
        if c.isspace():
            continue
        for n in unicodedata.normalize("NFKC", c):
            if not n.isspace():
                chars.append(n)
                index.append(i)
    return "".join(chars), index


def place(text: str, found: list[LaidOutEquation]) -> tuple[str, list[Placement]]:
    """The page text with each certain, structured equation's flat glyphs replaced by its rebuilt form.

    An equation is located by its glyphs' characters, in content-stream order, which is
    the order of the plain text; one that cannot be located exactly is not substituted.
    Uncertain equations leave the text as it was: their span is recorded, nothing more.
    """
    flat_text, index = _normalised(text)
    located: list[tuple[LaidOutEquation, tuple[int, int] | None]] = []
    cursor = 0
    for equation in sorted(found, key=lambda e: e.orders[0] if e.orders else 0):
        needle, _ = _normalised(equation.flat)
        at = flat_text.find(needle, cursor) if needle else -1
        if at < 0:
            located.append((equation, None))
            continue
        start, end = index[at], index[at + len(needle) - 1] + 1
        cursor = at + len(needle)
        located.append((equation, (start, end)))
    pieces: list[str] = []
    placements: list[Placement] = []
    position = 0
    shift = 0
    for equation, span in located:
        if span is None:
            placements.append(Placement(equation, None, False,
                                        (*equation.reasons, "its glyphs could not be located in the page's text")))
            continue
        start, end = span
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        line_end = len(text) if line_end < 0 else line_end
        reasons = equation.reasons
        if text[line_start:start].strip() or text[end:line_end].strip():
            # The plain text puts other characters on the equation's own lines: the two
            # readings of the page disagree about where the equation ends.
            reasons = (*reasons, "the page's text puts other characters on the equation's lines")
        if reasons or not equation.evidence:
            # Uncertain, or certain with nothing to rebuild: the text stays exactly as it is.
            placements.append(Placement(equation, (start + shift, end + shift), False, reasons, text[start:end]))
            continue
        pieces.append(text[position:start])
        placements.append(Placement(equation, (start + shift, start + shift + len(equation.linear)), True, (),
                                    text[start:end]))
        pieces.append(equation.linear)
        shift += len(equation.linear) - (end - start)
        position = end
    pieces.append(text[position:])
    return "".join(pieces), placements
