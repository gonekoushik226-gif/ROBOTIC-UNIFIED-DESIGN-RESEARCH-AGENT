"""Phase 19 diagram generation (ADR 0051; Part 5 sections 218-219; Part 4 sections 143-146).

    specification  section 144's first half: the subject, the stored knowledge, the
                   parameters, validation, the structured specification (section 145)
    render         the deterministic SVG, and its read-back validation

`draw` runs the whole pipeline and writes the specification and the image side by side.
It reads the knowledge database only; nothing is written to it.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app.diagrams.render import Validation, expected_texts, render, validate
from app.diagrams.specification import (
    Edge,
    Node,
    Parameter,
    Refusal,
    Specification,
    SpecificationBuilder,
    read_request,
)
from app.query import SourceScope

NOT_DRAWABLE = ("RUDRA draws structure diagrams from stored relationships; a schematic, a chart or a picture "
                "cannot be built from what is stored without inventing it.")


@dataclass(frozen=True, slots=True)
class DiagramAnswer:
    request: str
    #: DRAWN, or the refusal: INSUFFICIENT_AUTHORIZED_INFORMATION, AMBIGUOUS, NOT_DRAWABLE.
    status: str
    message: str
    specification: Specification | None = None
    validation: Validation | None = None
    svg_path: str | None = None
    json_path: str | None = None
    svg_sha256: str | None = None
    json_sha256: str | None = None
    #: True when identical files from the same specification already existed.
    reused: bool = False
    candidates: tuple[str, ...] = ()


def draw(connection, *, database_path: str | Path, request: str, out_dir: Path, name: str | None = None,
         scope: SourceScope = SourceScope.MY_BOOKS) -> DiagramAnswer:
    """Section 144's pipeline for one request; the files go to `out_dir`."""
    if name is not None:
        subject, parameters, not_drawable = " ".join(name.split()), (), False
        request = request or f"Draw a diagram of {subject}"
    else:
        subject, parameters, not_drawable = read_request(request)
    if not_drawable:
        return DiagramAnswer(request, "NOT_DRAWABLE", NOT_DRAWABLE)
    if connection is None:
        return DiagramAnswer(request, "INSUFFICIENT_AUTHORIZED_INFORMATION",
                             "Insufficient authorized information: there is no knowledge database - nothing is drawn.")
    spec = SpecificationBuilder(connection, database_path=database_path).build(request, subject, parameters, scope)
    if isinstance(spec, Refusal):
        return DiagramAnswer(request, spec.status, spec.message, candidates=spec.candidates)
    svg = render(spec)
    validation = validate(spec, svg)
    specification = spec.to_json() + "\n"
    digest = hashlib.sha256(specification.encode("utf-8")).hexdigest()
    stem = (re.sub(r"[^a-z0-9]+", "-", spec.subject.casefold()).strip("-")[:40] or "diagram") + "-" + digest[:12]
    out_dir.mkdir(parents=True, exist_ok=True)
    reused = True
    for path, text in ((out_dir / f"{stem}.json", specification), (out_dir / f"{stem}.svg", svg)):
        data = text.encode("utf-8")
        if path.exists():
            if path.read_bytes() != data:
                raise FileExistsError(f"{path} exists with other content; it is never overwritten")
        else:
            reused = False
            with open(path, "xb") as handle:
                handle.write(data)
    svg_bytes = svg.encode("utf-8")
    message = (f"{len(spec.nodes)} node(s) and {len(spec.edges)} edge(s) from stored knowledge; "
               f"{len(spec.unknowns)} unknown(s) marked, none invented; the image {validation.status}.")
    return DiagramAnswer(request, "DRAWN", message, spec, validation, str(out_dir / f"{stem}.svg"),
                         str(out_dir / f"{stem}.json"), hashlib.sha256(svg_bytes).hexdigest(), digest, reused)


__all__ = [
    "NOT_DRAWABLE",
    "DiagramAnswer",
    "Edge",
    "Node",
    "Parameter",
    "Refusal",
    "Specification",
    "SpecificationBuilder",
    "Validation",
    "draw",
    "expected_texts",
    "read_request",
    "render",
    "validate",
]
