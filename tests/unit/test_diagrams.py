"""Phase 19: diagram generation from stored knowledge (ADR 0051).

What every test holds Phase 19 to: every box and line is a stored concept or an EXPLICIT
relationship with evidence in scope; what the knowledge does not state - a composition's
count, how a request parameter maps onto the structure - is marked unknown, never drawn as
a value; the specification is kept beside the image and names every identifier used; the
image is read back and must draw exactly the specification; the same request gives the
same bytes; nothing is written to the database.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path

import pytest

from app.core.errors import InvalidInputError
from app.diagrams import draw, read_request, render, validate
from app.provenance import ProvenanceService
from app.query import SourceScope
from app.storage import Repository, connect
from app.ui.cli.main import main
from tests.unit.pdf_fixtures import make_pdf

ADDERS = (
    "Adders\n"
    "Half adder is defined as a circuit that adds two bits.\n"
    "Full adder is defined as a circuit that adds three bits.\n"
    "Ripple carry adder is defined as a chain of full adders that adds two binary numbers.\n"
    "Ripple carry adder consists of full adder.\n"
    "Full adder consists of half adder.\n"
    "Ripple carry adder depends on carry propagation.\n"
    "Carry propagation is defined as the passing of a carry from one stage to the next.\n"
    "Multiplexer is defined as a circuit that selects one of several inputs.\n"
)
REQUEST = "Draw a diagram of an 8-bit ripple carry adder"


@pytest.fixture(scope="module")
def adders_project(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("adders")
    pdf = base / "adders.pdf"
    pdf.write_bytes(make_pdf([ADDERS]))
    root = base / "project"
    assert main(["extract", str(pdf), "--project-root", str(root)]) == 0
    return root


@pytest.fixture
def project(adders_project, tmp_path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(adders_project, root)
    return root


def _draw(root: Path, request: str = REQUEST, **options):
    path = root / "data" / "database" / "knowledge.db"
    with closing(connect(path, read_only=True)) as connection:
        return draw(connection, database_path=path, request=request, out_dir=root / "diagrams", **options)


# ------------------------------------------------------------ the request (P19-2, P19-4)


def test_the_subject_and_the_requests_parameters():
    subject, parameters, not_drawable = read_request(REQUEST)
    assert subject == "ripple carry adder" and not not_drawable
    (parameter,) = parameters
    assert (parameter.text, parameter.value, parameter.unit, parameter.origin) == ("8-bit", "8", "bit", "REQUEST")


@pytest.mark.parametrize("text", ["Draw a schematic of the ripple carry adder", "Generate an image of a full adder"])
def test_what_is_not_drawn_is_refused_with_its_reason(project, text):
    answer = _draw(project, text)
    assert answer.status == "NOT_DRAWABLE" and answer.svg_path is None


def test_a_request_that_is_not_for_a_diagram_is_invalid():
    with pytest.raises(InvalidInputError):
        read_request("Open Calculator.")


# ------------------------------------------------------------ section 219


def test_known_parameters_are_represented_correctly(project):
    answer = _draw(project)
    spec = answer.specification
    assert answer.status == "DRAWN"
    names = {n.name: n for n in spec.nodes}
    assert set(names) == {"Ripple carry adder", "Full adder", "Half adder", "Carry propagation"}
    assert (names["Ripple carry adder"].level, names["Full adder"].level, names["Half adder"].level) == (0, 1, 2)
    assert names["Carry propagation"].side
    edges = {(e.relation_type, names_of(spec, e.from_id), names_of(spec, e.to_id)) for e in spec.edges}
    assert edges == {("COMPOSED_OF", "Ripple carry adder", "Full adder"), ("COMPOSED_OF", "Full adder", "Half adder"),
                     ("DEPENDS_ON", "Ripple carry adder", "Carry propagation")}
    assert all(e.evidence for e in spec.edges)
    assert names["Ripple carry adder"].definition.startswith("Ripple carry adder is defined as")


def names_of(spec, concept_id: str) -> str:
    return next(n.name for n in spec.nodes if n.concept_id == concept_id)


def test_unknown_parameters_are_not_invented(project):
    answer = _draw(project)
    spec = answer.specification
    assert [e.label for e in spec.edges if e.relation_type == "COMPOSED_OF"] == ["COMPOSED_OF (count not stated)"] * 2
    assert spec.unknowns == (
        "how many Full adder a Ripple carry adder has - the knowledge used does not state it",
        "how many Half adder a Full adder has - the knowledge used does not state it",
        "how 8-bit maps onto the structure - the knowledge used does not state it",
    )
    (parameter,) = spec.parameters
    assert parameter.note.startswith("not applied")
    svg = Path(answer.svg_path).read_text(encoding="utf-8")
    texts = ["".join(t.itertext()) for t in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}text")]
    numbered = [t for t in texts if re.search(r"\d", t)]
    # The only number in the image is the request's own, in the legend, marked not applied.
    assert numbered == ["Requested: 8-bit (not applied - the knowledge used does not state how it maps onto "
                        "the structure)", "Unknown: how 8-bit maps onto the structure - the knowledge used does "
                        "not state it"]


def test_the_request_is_traceable_to_the_knowledge_used(project):
    answer = _draw(project)
    spec = answer.specification
    written = json.loads(Path(answer.json_path).read_text(encoding="utf-8"))
    assert written["identifiers"] == list(spec.identifiers) and written["request"] == REQUEST
    svg = ET.fromstring(Path(answer.svg_path).read_text(encoding="utf-8"))
    metadata = svg.find("{http://www.w3.org/2000/svg}metadata")
    assert json.loads(metadata.text) == list(spec.identifiers)
    for prefix in ("CPT-", "REL-", "K-", "RO-"):
        assert any(i.startswith(prefix) for i in spec.identifiers), prefix
    path = project / "data" / "database" / "knowledge.db"
    with closing(connect(path, read_only=True)) as connection:
        service = ProvenanceService(Repository(connection))
        for edge in spec.edges:
            assert service.of_item(edge.relationship_id).status.value == "AVAILABLE"


def test_the_image_is_read_back_and_verified(project):
    answer = _draw(project)
    assert answer.validation.status == "VERIFIED"
    svg = Path(answer.svg_path).read_text(encoding="utf-8")
    tampered = svg.replace(">Full adder<", ">Full adder x8<", 1)
    assert validate(answer.specification, tampered).status == "FAILED"


def test_the_same_request_gives_the_same_bytes_and_nothing_is_written(project):
    database = project / "data" / "database" / "knowledge.db"
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    first = _draw(project)
    second = _draw(project)
    assert second.reused and (first.svg_sha256, first.json_sha256) == (second.svg_sha256, second.json_sha256)
    assert render(first.specification) == Path(first.svg_path).read_text(encoding="utf-8")
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


# ------------------------------------------------------------ refusals (P19-5)


def test_a_concept_without_relationships_is_not_a_diagram(project):
    answer = _draw(project, "Draw a diagram of the multiplexer")
    assert answer.status == "INSUFFICIENT_AUTHORIZED_INFORMATION" and "single box" in answer.message


def test_an_unknown_subject_is_insufficient(project):
    answer = _draw(project, "Draw a diagram of the Braun multiplier")
    assert answer.status == "INSUFFICIENT_AUTHORIZED_INFORMATION" and answer.svg_path is None


def test_by_name_and_in_the_authorized_scope(project):
    answer = _draw(project, "", name="Full adder", scope=SourceScope.AUTHORIZED)
    assert answer.status == "DRAWN"
    assert {n.name for n in answer.specification.nodes} == {"Full adder", "Half adder", "Ripple carry adder"}
