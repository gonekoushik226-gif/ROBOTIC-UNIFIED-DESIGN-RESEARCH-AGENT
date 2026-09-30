"""The SVG renderer and its read-back validation (ADR 0051 P19-6, P19-7). Standard library.

The layout is deterministic: the subject on top of its composition, level by level; its
wholes above; its other relations beside it; the request's parameters and the unknowns in
a legend below. Every element carries `data-rudra-id` with the identifier it draws, and the
specification's identifiers are in `<metadata>`. The same specification gives the same
bytes. Validation reads the SVG back and checks that it draws exactly the specification:
every node and edge once, and no text the specification does not hold.
"""

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from app.diagrams.specification import Specification

_SVG = "http://www.w3.org/2000/svg"
_BOX_H = 44
_LEVEL_GAP = 120
_COLUMN_GAP = 60
_MARGIN = 40
_CHAR = 7.2


@dataclass(frozen=True, slots=True)
class Validation:
    status: str
    detail: str


def _width(text: str) -> int:
    return max(150, int(len(text) * _CHAR) + 28)


def expected_texts(spec: Specification) -> list[str]:
    """Every text the image may hold, in drawing order."""
    texts = [spec.title]
    texts += [node.name for node in spec.nodes]
    texts += [edge.label for edge in spec.edges]
    texts += [f"Requested: {p.text} ({p.note})" for p in spec.parameters]
    texts += [f"Unknown: {u}" for u in spec.unknowns]
    return texts


def render(spec: Specification) -> str:
    """The SVG for a specification."""
    levels = sorted({n.level for n in spec.nodes if not n.side})
    rows = {level: [n for n in spec.nodes if n.level == level and not n.side] for level in levels}
    side = [n for n in spec.nodes if n.side]
    positions: dict[str, tuple[int, int, int]] = {}
    y = _MARGIN + 40
    main_width = 0
    for level in levels:
        x = _MARGIN
        for node in rows[level]:
            w = _width(node.name)
            positions[node.concept_id] = (x, y, w)
            x += w + _COLUMN_GAP
        main_width = max(main_width, x)
        y += _LEVEL_GAP
    subject_y = next(positions[n.concept_id][1] for n in spec.nodes if n.level == 0 and not n.side)
    side_x = main_width + 160
    for index, node in enumerate(side):
        positions[node.concept_id] = (side_x, subject_y + index * (_BOX_H + 30), _width(node.name))
    legend = [f"Requested: {p.text} ({p.note})" for p in spec.parameters] + [f"Unknown: {u}" for u in spec.unknowns]
    legend_y = max(y, subject_y + len(side) * (_BOX_H + 30) + _LEVEL_GAP)
    width = max([side_x + max((_width(n.name) for n in side), default=0), main_width,
                 *(int(len(t) * 6.6) + 2 * _MARGIN for t in legend + [spec.title])]) + _MARGIN
    height = legend_y + 24 * len(legend) + _MARGIN
    out = [f'<svg xmlns="{_SVG}" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           'font-family="Segoe UI, Arial, sans-serif" font-size="13">']
    out.append(f"<metadata id=\"rudra-specification\">{escape(json.dumps(list(spec.identifiers)))}</metadata>")
    out.append('<rect width="100%" height="100%" fill="#ffffff"/>')
    out.append(f'<text x="{_MARGIN}" y="{_MARGIN}" font-size="16" font-weight="bold">{escape(spec.title)}</text>')
    for edge in spec.edges:
        x1, y1, w1 = positions[edge.from_id]
        x2, y2, w2 = positions[edge.to_id]
        if y1 == y2:  # beside: from one box's side to the other's
            (ax, bx) = (x1 + w1, x2) if x1 < x2 else (x1, x2 + w2)
            ay = by = y1 + _BOX_H // 2
        elif y1 < y2:
            ax, ay, bx, by = x1 + w1 // 2, y1 + _BOX_H, x2 + w2 // 2, y2
        else:
            ax, ay, bx, by = x1 + w1 // 2, y1, x2 + w2 // 2, y2 + _BOX_H
        mx, my = (ax + bx) // 2, (ay + by) // 2
        out.append(f"<g data-rudra-id={quoteattr(edge.relationship_id)} class=\"edge\">"
                   f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" stroke="#444" stroke-width="1.5" '
                   'marker-end="url(#arrow)"/>'
                   f'<text x="{mx + 6}" y="{my - 4}" font-size="11" fill="#333">{escape(edge.label)}</text></g>')
    for node in spec.nodes:
        x, y0, w = positions[node.concept_id]
        fill = "#e8f0fe" if node.level == 0 and not node.side else "#f5f5f5"
        out.append(f"<g data-rudra-id={quoteattr(node.concept_id)} class=\"node\">"
                   f'<rect x="{x}" y="{y0}" width="{w}" height="{_BOX_H}" rx="6" fill="{fill}" stroke="#222"/>'
                   f'<text x="{x + w // 2}" y="{y0 + 27}" text-anchor="middle">{escape(node.name)}</text></g>')
    for index, line in enumerate(legend):
        out.append(f'<text x="{_MARGIN}" y="{legend_y + 24 * index}" class="legend" font-size="12" '
                   f'fill="#8a3b00">{escape(line)}</text>')
    out.append('<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" '
               'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#444"/></marker></defs>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def validate(spec: Specification, svg: str) -> Validation:
    """Read the SVG back: exactly the specification's nodes, edges, texts and identifiers."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        return Validation("FAILED", f"the SVG does not parse: {exc}")
    ns = {"s": _SVG}
    problems = []
    groups = root.findall(".//s:g", ns)
    drawn = [g.get("data-rudra-id") for g in groups]
    wanted = [n.concept_id for n in spec.nodes] + [e.relationship_id for e in spec.edges]
    if sorted(drawn) != sorted(wanted):
        problems.append(f"drawn {sorted(drawn)} but the specification holds {sorted(wanted)}")
    texts = ["".join(t.itertext()) for t in root.iter(f"{{{_SVG}}}text")]
    if sorted(texts) != sorted(expected_texts(spec)):
        extra = sorted(set(texts) - set(expected_texts(spec)))
        missing = sorted(set(expected_texts(spec)) - set(texts))
        problems.append(f"texts not in the specification {extra}; texts missing {missing}")
    metadata = root.find("s:metadata", ns)
    if metadata is None or json.loads(metadata.text or "[]") != list(spec.identifiers):
        problems.append("the metadata does not list the specification's identifiers")
    if problems:
        return Validation("FAILED", "; ".join(problems))
    return Validation("VERIFIED", f"{len(spec.nodes)} node(s), {len(spec.edges)} edge(s) and {len(texts)} text(s) "
                                  "drawn, exactly the specification's, with its identifiers")
