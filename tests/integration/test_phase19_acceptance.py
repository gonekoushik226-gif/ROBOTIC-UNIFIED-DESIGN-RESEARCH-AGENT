"""Phase 19 acceptance: Part 5 section 219 through the `diagram` command (ADR 0051 P19-12).

    Generate a diagram from known structured information. Verify: Known parameters are
    represented correctly. Unknown parameters are not invented. The image request is
    traceable to the knowledge used.

Each step is one `python -m app` process in a temporary project root; the live database is
never opened. A generated page defining four adder concepts and stating three
relationships goes through the real `extract`. The expectations are written by hand.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.ui.cli import main as cli_main
from tests.conftest import PROJECT_ROOT
from tests.unit.pdf_fixtures import make_pdf
from tests.unit.test_diagrams import ADDERS, REQUEST

SVG = "{http://www.w3.org/2000/svg}"


def cli(root, *args: str) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUDRA_")}
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, "-m", "app", *args, "--project-root", str(root)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(PROJECT_ROOT), timeout=240,
    )
    return done.returncode, done.stdout, done.stderr


def _json(root, *args) -> tuple[int, dict]:
    code, out, err = cli(root, *args, "--json")
    assert out, err
    return code, json.loads(out)


@pytest.fixture
def root(tmp_path) -> Path:
    pdf = tmp_path / "adders.pdf"
    pdf.write_bytes(make_pdf([ADDERS]))
    project = tmp_path / "project"
    code, out, err = cli(project, "extract", str(pdf))
    assert code == 0, err or out
    return project


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_command_set_gains_exactly_diagram():
    before = {"start", "env", "config", "paths", "db", "extract", "classify", "lookup", "review", "edition",
              "merge", "query", "index", "reason", "calculate", "provenance", "interpret", "act", "do",
              "procedure", "manual", "research", "version"}
    # Later phases add their own commands (ADR 0047 onward); Phase 19's own addition is unchanged.
    assert set(cli_main._COMMANDS) - {"voice", "ask", "source", "solve", "inventory"} == before | {"diagram"}


def test_section_219(root, tmp_path):
    database = root / "data" / "database" / "knowledge.db"
    before = _digest(database)
    out_dir = tmp_path / "diagrams"
    code, answer = _json(root, "diagram", REQUEST, "--out", str(out_dir))
    assert code == 0 and answer["status"] == "DRAWN" and answer["validation"]["status"] == "VERIFIED"
    spec = answer["specification"]

    # Known parameters are represented correctly.
    names = {n["concept_id"]: n["name"] for n in spec["nodes"]}
    assert sorted(names.values()) == ["Carry propagation", "Full adder", "Half adder", "Ripple carry adder"]
    assert sorted((e["relation_type"], names[e["from_id"]], names[e["to_id"]]) for e in spec["edges"]) == [
        ("COMPOSED_OF", "Full adder", "Half adder"), ("COMPOSED_OF", "Ripple carry adder", "Full adder"),
        ("DEPENDS_ON", "Ripple carry adder", "Carry propagation")]
    assert [p["text"] for p in spec["parameters"]] == ["8-bit"] and spec["parameters"][0]["origin"] == "REQUEST"

    # Unknown parameters are not invented.
    assert len(spec["unknowns"]) == 3 and all("does not state it" in u for u in spec["unknowns"])
    svg = ET.fromstring(Path(answer["svg_path"]).read_text(encoding="utf-8"))
    for group in svg.iter(f"{SVG}g"):
        for text in group.iter(f"{SVG}text"):
            assert not re.search(r"\d", text.text or ""), text.text  # no number on any box or line
    legend = [t.text for t in svg.iter(f"{SVG}text") if t.get("class") == "legend"]
    assert legend[0].startswith("Requested: 8-bit (not applied")

    # The image request is traceable to the knowledge used.
    written = json.loads(Path(answer["json_path"]).read_text(encoding="utf-8"))
    assert written["request"] == REQUEST and written["identifiers"] == spec["identifiers"]
    assert json.loads(svg.find(f"{SVG}metadata").text) == spec["identifiers"]
    drawn = {g.get("data-rudra-id") for g in svg.iter(f"{SVG}g")}
    assert drawn == set(names) | {e["relationship_id"] for e in spec["edges"]}
    for edge in spec["edges"]:
        code, provenance = _json(root, "provenance", edge["relationship_id"])
        assert code == 0 and provenance["status"] == "AVAILABLE"
        assert [c["evidence"]["id"] for c in provenance["citations"]] == [i for i, _ in edge["evidence"]]

    # The same request again: the same bytes; the database only read.
    code, again = _json(root, "diagram", REQUEST, "--out", str(out_dir))
    assert code == 0 and again["reused"] and again["svg_sha256"] == answer["svg_sha256"]
    assert _digest(database) == before


def test_the_default_folder_and_the_text_form(root):
    code, out, _ = cli(root, "diagram", "--name", "full adder")
    assert code == 0 and "Validation  : VERIFIED" in out and "Unknown     : how many Half adder a Full adder has" in out
    assert list((root / "data" / "cache" / "diagrams").glob("full-adder-*.svg"))


@pytest.mark.parametrize(("request_text", "status"), [
    ("Draw a diagram of the multiplexer", "INSUFFICIENT_AUTHORIZED_INFORMATION"),
    ("Draw a diagram of the Braun multiplier", "INSUFFICIENT_AUTHORIZED_INFORMATION"),
    ("Draw a schematic of the ripple carry adder", "NOT_DRAWABLE"),
])
def test_nothing_is_drawn_that_cannot_be_drawn_from_knowledge(root, tmp_path, request_text, status):
    code, answer = _json(root, "diagram", request_text, "--out", str(tmp_path / "out"))
    assert code == 3 and answer["status"] == status and answer["svg_path"] is None
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "args",
    [("diagram",), ("diagram", "Open Calculator."), ("diagram", REQUEST, "--name", "full adder"),
     ("diagram", REQUEST, "--site", "http://example.org/"), ("reason", "X", "--out", "C:/x")],
)
def test_invalid_requests_exit_2(root, args):
    assert cli(root, *args)[0] == 2
