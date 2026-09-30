"""Structured mathematics in documents, read into one linear notation.

Word and PowerPoint store equations as Office Math (OMML); web pages and EPUB books as
MathML. Both are trees - a fraction *is* a numerator and a denominator - so nothing has
to be guessed from layout. This module turns either tree into RUDRA's linear notation, a
subset of LaTeX (`\\frac{a}{b}`, `x^{2}`, `\\sqrt[n]{x}`, `\\int_{a}^{b}`, `\\sum`,
`\\begin{matrix}`, `\\begin{aligned}` ...). That text is what RUDRA stores for the
equation: searchable, exact, and read back into a tree by the display
(`app/ui/gui/mathrender.py`).

What cannot be converted is kept as its plain text, never dropped and never completed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

OMML = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MATHML = "http://www.w3.org/1998/Math/MathML"

#: Characters with a LaTeX command the display reads.
_SYMBOL_COMMANDS = {
    "∫": r"\int", "∬": r"\iint", "∭": r"\iiint", "∮": r"\oint", "∑": r"\sum", "∏": r"\prod",
    "≤": r"\le", "≥": r"\ge", "≠": r"\ne", "≈": r"\approx", "→": r"\to", "∞": r"\infty", "∂": r"\partial",
    "·": r"\cdot", "×": r"\times", "±": r"\pm", "∓": r"\mp", "∝": r"\propto", "∇": r"\nabla", "⋅": r"\cdot",
    "−": "-",
}
_BIG = {"∫": r"\int", "∬": r"\iint", "∭": r"\iiint", "∮": r"\oint", "∑": r"\sum", "∏": r"\prod",
        "⋃": r"\bigcup", "⋂": r"\bigcap"}
_ACCENTS = {"̂": r"\hat", "^": r"\hat", "̄": r"\bar", "¯": r"\bar", "̇": r"\dot",
            "⃗": r"\vec", "→": r"\vec", "̃": r"\tilde", "~": r"\tilde", "̈": r"\ddot"}
_FUNCTIONS = frozenset({"sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh",
                        "tanh", "log", "ln", "exp", "lim", "max", "min", "det"})


def _group(text: str) -> str:
    text = text.strip()
    return "{" + text + "}"


def _escape(text: str) -> str:
    """Plain characters in the notation; braces and backslashes kept literal."""
    out = []
    for char in text:
        if char in "{}":
            out.append("\\" + char)
        elif char == "\\":
            out.append(r"\backslash ")
        else:
            out.append(char)
    return "".join(out)


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------------ Office Math (OMML)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local(child.tag) == name]


def _val(element: ET.Element | None, name: str) -> str | None:
    """A property's m:val attribute, e.g. <m:chr m:val="∑"/> inside <m:naryPr>."""
    if element is None:
        return None
    prop = _child(element, name)
    if prop is None:
        return None
    for key, value in prop.attrib.items():
        if _local(key) == "val":
            return value
    return ""


def omml_to_linear(element: ET.Element) -> str:
    """One <m:oMath> (or any OMML element) as linear notation."""
    return _tidy(_omml(element))


def _omml_seq(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(_omml(child) for child in element)


def _omml(element: ET.Element) -> str:
    name = _local(element.tag)
    if name.endswith("Pr") or name in ("ctrlPr", "rPr"):
        return ""
    if name == "t":
        text = element.text or ""
        return "".join(_SYMBOL_COMMANDS.get(c, _escape(c)) + (" " if c in _SYMBOL_COMMANDS else "")
                       for c in text)
    if name == "r":
        return "".join(_omml(child) for child in element if _local(child.tag) == "t")
    if name in ("oMath", "e", "num", "den", "sub", "sup", "deg", "lim", "fName", "box", "borderBox",
                "phant", "groupChr", "oMathPara"):
        if name == "oMathPara":
            return " \\\\ ".join(_omml(child) for child in element if _local(child.tag) == "oMath")
        if name == "phant":
            return ""
        return _omml_seq(element)
    if name == "f":
        kind = _val(_child(element, "fPr"), "type")
        numerator, denominator = _omml_seq(_child(element, "num")), _omml_seq(_child(element, "den"))
        if kind == "lin":
            return f"{numerator}/{denominator}"
        return rf"\frac{_group(numerator)}{_group(denominator)}"
    if name == "sSup":
        return f"{_base(_omml_seq(_child(element, 'e')))}^{_group(_omml_seq(_child(element, 'sup')))}"
    if name == "sSub":
        return f"{_base(_omml_seq(_child(element, 'e')))}_{_group(_omml_seq(_child(element, 'sub')))}"
    if name == "sSubSup":
        return (f"{_base(_omml_seq(_child(element, 'e')))}_{_group(_omml_seq(_child(element, 'sub')))}"
                f"^{_group(_omml_seq(_child(element, 'sup')))}")
    if name == "sPre":
        return (f"{{}}_{_group(_omml_seq(_child(element, 'sub')))}^{_group(_omml_seq(_child(element, 'sup')))}"
                f"{_omml_seq(_child(element, 'e'))}")
    if name == "rad":
        hidden = _val(_child(element, "radPr"), "degHide") in ("1", "on", "true", "")
        degree = _omml_seq(_child(element, "deg"))
        body = _group(_omml_seq(_child(element, "e")))
        if degree and not hidden:
            return rf"\sqrt[{degree}]{body}"
        return rf"\sqrt{body}"
    if name == "nary":
        props = _child(element, "naryPr")
        char = _val(props, "chr") or "∫"
        command = _BIG.get(char, _escape(char))
        out = command
        if _val(props, "subHide") not in ("1", "on", "true", ""):
            sub = _omml_seq(_child(element, "sub"))
            if sub:
                out += f"_{_group(sub)}"
        if _val(props, "supHide") not in ("1", "on", "true", ""):
            sup = _omml_seq(_child(element, "sup"))
            if sup:
                out += f"^{_group(sup)}"
        return f"{out} {_omml_seq(_child(element, 'e'))}"
    if name == "d":
        props = _child(element, "dPr")
        begin = _val(props, "begChr")
        end = _val(props, "endChr")
        separator = _val(props, "sepChr")
        begin = "(" if begin is None else begin
        end = ")" if end is None else end
        separator = "," if separator is None else separator
        inner = f" {separator} ".join(_omml_seq(e) for e in _children(element, "e"))
        matrix = _only_matrix(element)
        if matrix is not None and (begin, end) in (("(", ")"), ("[", "]"), ("|", "|"), ("{", "}")):
            kind = {"(": "pmatrix", "[": "bmatrix", "|": "vmatrix", "{": "Bmatrix"}[begin]
            return matrix.replace(r"\begin{matrix}", rf"\begin{{{kind}}}").replace(r"\end{matrix}", rf"\end{{{kind}}}")
        if begin == "{" and end == "" and _has_eqarr(element):
            return rf"\begin{{cases}} {_rows_of(element)} \end{{cases}}"
        left = {"{": r"\{", "": "."}.get(begin, begin)
        right = {"}": r"\}", "": "."}.get(end, end)
        return rf"\left{left} {inner} \right{right}"
    if name == "func":
        function = _tidy(_omml_seq(_child(element, "fName")))
        argument = _omml_seq(_child(element, "e"))
        if function in _FUNCTIONS:
            function = "\\" + function
        return f"{function} {argument}"
    if name == "limLow":
        base = _tidy(_omml_seq(_child(element, "e")))
        if base in ("lim", r"\lim"):
            base = r"\lim"
        return f"{base}_{_group(_omml_seq(_child(element, 'lim')))}"
    if name == "limUpp":
        return f"{_base(_omml_seq(_child(element, 'e')))}^{_group(_omml_seq(_child(element, 'lim')))}"
    if name == "acc":
        char = _val(_child(element, "accPr"), "chr") or "̂"
        return f"{_ACCENTS.get(char, r'\hat')}{_group(_omml_seq(_child(element, 'e')))}"
    if name == "bar":
        return rf"\overline{_group(_omml_seq(_child(element, 'e')))}"
    if name == "m":
        rows = [" & ".join(_omml_seq(cell) for cell in _children(row, "e")) for row in _children(element, "mr")]
        return r"\begin{matrix} " + r" \\ ".join(rows) + r" \end{matrix}"
    if name == "eqArr":
        rows = [_omml_seq(row) for row in _children(element, "e")]
        return r"\begin{aligned} " + r" \\ ".join(rows) + r" \end{aligned}"
    # Anything else: its text, never dropped.
    return _omml_seq(element) or (element.text or "")


def _base(text: str) -> str:
    text = text.strip()
    if len(text) <= 1 or re.fullmatch(r"\\[A-Za-z]+|[A-Za-z]+|[0-9.]+", text):
        return text
    return "{" + text + "}"


def _only_matrix(delimiter: ET.Element) -> str | None:
    parts = _children(delimiter, "e")
    if len(parts) == 1:
        inner = [child for child in parts[0] if not _local(child.tag).endswith("Pr")]
        if len(inner) == 1 and _local(inner[0].tag) == "m":
            return _omml(inner[0])
    return None


def _has_eqarr(delimiter: ET.Element) -> bool:
    parts = _children(delimiter, "e")
    return len(parts) == 1 and any(_local(child.tag) == "eqArr" for child in parts[0])


def _rows_of(delimiter: ET.Element) -> str:
    array = next(child for child in _children(delimiter, "e")[0] if _local(child.tag) == "eqArr")
    return r" \\ ".join(_omml_seq(row) for row in _children(array, "e"))


# ------------------------------------------------------------------ MathML


@dataclass
class Node:
    """A MathML element as read from HTML (lower-case local names)."""

    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)  # Node or str

    def elements(self) -> list["Node"]:
        return [child for child in self.children if isinstance(child, Node)]

    def text(self) -> str:
        return "".join(child if isinstance(child, str) else child.text() for child in self.children)


def from_element(element: ET.Element) -> Node:
    """A MathML subtree parsed as XML (EPUB) as a `Node` tree."""
    node = Node(_local(element.tag).lower(), {_local(k): v for k, v in element.attrib.items()})
    if element.text:
        node.children.append(element.text)
    for child in element:
        node.children.append(from_element(child))
        if child.tail:
            node.children.append(child.tail)
    return node


def mathml_to_linear(node: Node) -> str:
    """A <math> element as linear notation."""
    return _tidy(_mathml(node))


def _mathml_seq(nodes: list[Node]) -> str:
    return "".join(_mathml(node) for node in nodes)


def _token(text: str) -> str:
    text = text.strip()
    if text in _SYMBOL_COMMANDS:
        return " " + _SYMBOL_COMMANDS[text] + " "
    if text in _FUNCTIONS:
        return "\\" + text + " "
    return _escape(text)


def _mathml(node: Node) -> str:
    tag = node.tag
    kids = node.elements()
    if tag in ("annotation", "annotation-xml", "mphantom", "none"):
        return ""
    if tag == "semantics":
        return _mathml(kids[0]) if kids else ""
    if tag in ("mi", "mn", "mo", "ms"):
        return _token(node.text())
    if tag == "mtext":
        text = node.text().strip()
        return rf"\text{{{_escape(text)}}}" if text else " "
    if tag == "mspace":
        return " "
    if tag == "mfrac" and len(kids) >= 2:
        if node.attrs.get("bevelled") == "true":
            return f"{_mathml(kids[0])}/{_mathml(kids[1])}"
        return rf"\frac{_group(_mathml(kids[0]))}{_group(_mathml(kids[1]))}"
    if tag == "msup" and len(kids) >= 2:
        return f"{_base(_mathml(kids[0]))}^{_group(_mathml(kids[1]))}"
    if tag == "msub" and len(kids) >= 2:
        return f"{_base(_mathml(kids[0]))}_{_group(_mathml(kids[1]))}"
    if tag == "msubsup" and len(kids) >= 3:
        return f"{_base(_mathml(kids[0]))}_{_group(_mathml(kids[1]))}^{_group(_mathml(kids[2]))}"
    if tag == "msqrt":
        return rf"\sqrt{_group(_mathml_seq(kids))}"
    if tag == "mroot" and len(kids) >= 2:
        return rf"\sqrt[{_mathml(kids[1]).strip()}]{_group(_mathml(kids[0]))}"
    if tag in ("munder", "mover", "munderover") and kids:
        base = _mathml(kids[0]).strip()
        if tag == "mover" and len(kids) >= 2 and kids[1].text().strip() in _ACCENTS:
            return f"{_ACCENTS[kids[1].text().strip()]}{_group(base)}"
        if tag == "munder" and len(kids) >= 2:
            return f"{_base(base)}_{_group(_mathml(kids[1]))}"
        if tag == "mover" and len(kids) >= 2:
            return f"{_base(base)}^{_group(_mathml(kids[1]))}"
        if len(kids) >= 3:
            return f"{_base(base)}_{_group(_mathml(kids[1]))}^{_group(_mathml(kids[2]))}"
        return base
    if tag == "mfenced":
        opener = node.attrs.get("open", "(")
        closer = node.attrs.get("close", ")")
        separators = node.attrs.get("separators", ",") or ","
        inner = f" {separators[0]} ".join(_mathml(kid) for kid in kids)
        if len(kids) == 1 and kids[0].tag == "mtable":
            return _table(kids[0], opener, closer)
        return rf"\left{_fence(opener, True)} {inner} \right{_fence(closer, False)}"
    if tag == "mtable":
        return _table(node, "", "")
    if tag in ("mrow", "math", "mstyle", "mpadded", "menclose", "merror", "mtd", "maction"):
        return _mrow(kids)
    return _mathml_seq(kids) or _escape(node.text())


def _fence(char: str, opening: bool) -> str:
    if not char:
        return "."
    return {"{": r"\{", "}": r"\}"}.get(char, char)


def _table(node: Node, opener: str, closer: str) -> str:
    rows = []
    for row in node.elements():
        cells = row.elements() if row.tag in ("mtr", "mlabeledtr") else [row]
        if row.tag == "mlabeledtr" and cells:
            cells = cells[1:]
        rows.append(" & ".join(_mathml(cell).strip() for cell in cells))
    body = r" \\ ".join(rows)
    kind = {("(", ")"): "pmatrix", ("[", "]"): "bmatrix", ("|", "|"): "vmatrix", ("{", "}"): "Bmatrix"}.get(
        (opener, closer))
    if kind:
        return rf"\begin{{{kind}}} {body} \end{{{kind}}}"
    if opener == "{" and not closer:
        return rf"\begin{{cases}} {body} \end{{cases}}"
    aligned = node.attrs.get("columnalign", "").split()
    if len(aligned) >= 2 and aligned[0] == "right" and aligned[1] == "left":
        return rf"\begin{{aligned}} {body} \end{{aligned}}"
    return rf"\begin{{matrix}} {body} \end{{matrix}}"


def _mrow(kids: list[Node]) -> str:
    """A row, recognising fences written as <mo>(</mo> ... <mo>)</mo> around a table."""
    if (len(kids) == 3 and kids[0].tag == "mo" and kids[2].tag == "mo" and kids[1].tag == "mtable"):
        return _table(kids[1], kids[0].text().strip(), kids[2].text().strip())
    if len(kids) == 2 and kids[0].tag == "mo" and kids[0].text().strip() == "{" and kids[1].tag == "mtable":
        return _table(kids[1], "{", "")
    return _mathml_seq(kids)
