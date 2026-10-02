"""What RUDRA knows, as a person reads it: the knowledge inventory.

The inventory answers "what is in my knowledge base?" - per document (what was stored, what
was linked to knowledge other evidence already supported, what was found but *not* stored and
why) and per item (where it came from and how sure RUDRA is) - and says which stored equations
a calculation can use. These tests build real knowledge bases through the command line's own
`extract` and read them back through `inventory`, as the Knowledge page does.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.inventory import ISSUE_WORDS, KINDS, build
from app.inventory.build import readable
from app.storage import DatabaseRole, Repository, connect
from app.ui.cli.main import main

DOCUMENT = """Series circuits

Resistance is defined as the opposition offered by a material to the flow of current.
The symbols are used in the usual way, where V is the voltage across the element.
The opposition to current is limited by the resistor, where R is the resistance in ohms.
Rtotal = R1 + R2
V = I * R
P = V * I
Z = R +
"""


def _cli(root: Path, capsys, *arguments: str) -> tuple[int, str]:
    capsys.readouterr()
    code = main([*arguments, "--project-root", str(root)])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


@pytest.fixture
def project(tmp_path, capsys):
    root = tmp_path / "project"
    document = tmp_path / "series.txt"
    document.write_text(DOCUMENT, encoding="utf-8")
    code, out = _cli(root, capsys, "extract", str(document))
    assert code == 0, out
    return root


def _inventory(project: Path, capsys, *arguments: str) -> dict:
    code, out = _cli(project, capsys, "inventory", "--json", *arguments)
    assert code == 0, out
    return json.loads(out[out.index("{"):out.rindex("}") + 1])


def test_the_inventory_counts_what_a_document_gave(project, capsys):
    data = _inventory(project, capsys)
    counts = data["totals"]["counts"]
    assert data["totals"]["documents"] == 1
    assert counts["EQUATION"] >= 3 and counts["DEFINITION"] >= 1 and counts["CONCEPT"] >= 1
    (document,) = data["documents"]
    assert document["name"] == "series.txt" and document["status_words"]
    assert document["stored"]["EQUATION"] >= 3 and document["file_present"] is True


def test_what_was_found_but_not_stored_is_listed_in_plain_words(project, capsys):
    data = _inventory(project, capsys)
    (document,) = data["documents"]
    not_stored = [group for group in document["problems"] if group["not_stored"]]
    assert not_stored, "the broken equation 'Z = R +' must be reported, not silently dropped"
    group = next(g for g in not_stored if g["issue_type"] == "INVALID_EQUATION_STRUCTURE")
    assert "not stored" in group["meaning"] and group["short"] and group["count"] >= 1
    assert any("Z = R +" in excerpt for _page, excerpt in group["examples"])
    assert "Phase" not in json.dumps(data) and "ADR" not in json.dumps(data)


def test_every_recorded_problem_kind_has_words_for_a_person():
    from app.models.enums import ExtractionIssueType

    for kind in ExtractionIssueType:
        meaning, discarded, short = ISSUE_WORDS[kind.value]
        assert meaning.endswith(".") and short and isinstance(discarded, bool)


def test_items_carry_their_source_and_quote(project, capsys):
    data = _inventory(project, capsys, "--knowledge-type", "EQUATION")
    items = data["items"]
    assert items and all(item["kind"] == "EQUATION" for item in items)
    product = next(item for item in items if item["title"] == "P = V * I")
    assert product["document"] == "series.txt" and product["quote"] == "P = V * I"
    assert product["calculation"] == "usable" and product["certainty"] == "REPORTED_BY_SOURCE"
    broken = [item for item in items if item["title"].startswith("Z = R +")]
    assert not broken, "an equation that looked broken is not stored, so it is not an equation item"


def test_definitions_say_which_concept_they_define(project, capsys):
    data = _inventory(project, capsys, "--knowledge-type", "definitions")
    definition = next(item for item in data["items"] if "opposition" in item["statement"])
    assert definition["about"] == ["Resistance"] and definition["page"] == 1


def test_variables_are_listed_as_a_symbol_and_its_meaning(project, capsys):
    data = _inventory(project, capsys, "--knowledge-type", "VARIABLE")
    titles = {item["title"] for item in data["items"]}
    assert "V = voltage" in titles and "R = resistance" in titles


def test_the_problems_themselves_can_be_listed(project, capsys):
    data = _inventory(project, capsys, "--knowledge-type", "PROBLEM")
    stored_nothing = [item for item in data["items"] if item["certainty"] == "NOT_STORED"]
    assert stored_nothing and stored_nothing[0]["document"] == "series.txt"
    assert stored_nothing[0]["statement"].startswith("Not stored.")


def test_calculation_readiness_counts_the_equations_a_calculation_can_use(project, capsys):
    readiness = _inventory(project, capsys)["calculation"]
    assert readiness["equations"] >= 3 and readiness["usable"] == readiness["equations"]
    assert ["V", "voltage"] in [list(pair) for pair in readiness["named_quantities"]]


def test_an_equation_that_cannot_be_calculated_with_says_why(tmp_path, capsys):
    root = tmp_path / "project"
    document = tmp_path / "calculus.txt"
    document.write_text("Calculus\n\ni = C * \\frac{dV}{dt}\nP = V * I\n", encoding="utf-8")
    assert main(["extract", str(document), "--project-root", str(root)]) == 0
    data = _inventory(root, capsys, "--knowledge-type", "EQUATION")
    by_title = {item["title"]: item for item in data["items"]}
    assert by_title["P = V * I"]["calculation"] == "usable"
    calculus = next(item for item in data["items"] if "dV/dt" in item["title"])
    assert "cannot be calculated with" in calculus["calculation"]
    assert data["calculation"]["usable"] == 1 and data["calculation"]["reasons"]


def test_the_list_can_be_limited_to_one_document(tmp_path, capsys):
    root = tmp_path / "project"
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    first.write_text("Power\n\nP = V * I\n", encoding="utf-8")
    second.write_text("Energy\n\nW = P * t\n", encoding="utf-8")
    for document in (first, second):
        assert main(["extract", str(document), "--project-root", str(root)]) == 0
    everything = _inventory(root, capsys, "--knowledge-type", "EQUATION")
    only_second = _inventory(root, capsys, "--knowledge-type", "EQUATION", "--in-document", "DOC-00000002")
    assert len(everything["items"]) == 2 and [i["title"] for i in only_second["items"]] == ["W = P * t"]


def test_a_similar_statement_in_another_document_is_kept_apart_and_said_so(tmp_path, capsys):
    root = tmp_path / "project"
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    first.write_text("Power\n\nP = V * I\n", encoding="utf-8")
    second.write_text("Power again\n\nP = V * I\n", encoding="utf-8")
    for document in (first, second):
        assert main(["extract", str(document), "--project-root", str(root)]) == 0
    data = _inventory(root, capsys, "--knowledge-type", "EQUATION")
    assert [item["document"] for item in data["items"]] == ["a.txt", "b.txt"]  # neither is merged away
    assert data["documents"][0]["possible_duplicates"] == 0 and data["documents"][1]["possible_duplicates"] == 1


def test_text_form_is_readable_without_json(project, capsys):
    code, out = _cli(project, capsys, "inventory")
    assert code == 0
    assert "Knowledge   :" in out and "series.txt" in out and "Not stored" in out
    assert "Phase" not in out and "ADR" not in out


def test_an_unknown_kind_is_refused_with_the_accepted_ones(project, capsys):
    code, out = _cli(project, capsys, "inventory", "--knowledge-type", "FOO")
    assert code == 2 and "Accepted:" in out and "EQUATION" in out


def test_an_empty_project_says_there_is_nothing_yet(tmp_path, capsys):
    code, out = _cli(tmp_path / "empty", capsys, "inventory")
    assert code == 3 and "nothing yet" in out
    code, out = _cli(tmp_path / "empty", capsys, "inventory", "--json")
    assert code == 3 and json.loads(out[out.index("{"):out.rindex("}") + 1])["totals"]["documents"] == 0


def test_the_inventory_reads_only(project, capsys):
    path = project / "data" / "database" / "knowledge.db"
    before = path.read_bytes()
    _inventory(project, capsys, "--knowledge-type", "EQUATION")
    _inventory(project, capsys)
    assert path.read_bytes() == before


def test_building_it_directly_matches_the_command(project):
    connection = connect(project / "data" / "database" / "knowledge.db", role=DatabaseRole.KNOWLEDGE, read_only=True)
    try:
        inventory = build(Repository(connection), kinds=frozenset({"CONCEPT"}))
    finally:
        connection.close()
    assert [item.title for item in inventory.items] == ["Resistance"]
    assert inventory.kinds == ("CONCEPT",) and set(KINDS) >= {"CONCEPT", "PROBLEM"}


@pytest.mark.parametrize("stored, shown", [
    (r"P = VI = I^{2} R = \frac{V^{2}}{R} W", "P = VI = I² R = V²/R W"),
    (r"V = \frac{r}{A} \cdot I", "V = r/A · I"),
    (r"x = \frac{a+b}{c} \alpha^{-1}", "x = (a+b)/c α⁻¹"),
    ("V = I * R", "V = I * R"),
    (r"weird \foo thing", r"weird \foo thing"),
])
def test_stored_equations_are_shown_as_a_person_would_write_them(stored, shown):
    assert readable(stored) == shown


def test_the_stored_text_is_never_changed_by_showing_it(project, capsys):
    database = sqlite3.connect(project / "data" / "database" / "knowledge.db")
    try:
        before = database.execute("SELECT expression FROM equation ORDER BY id").fetchall()
    finally:
        database.close()
    _inventory(project, capsys, "--knowledge-type", "EQUATION")
    database = sqlite3.connect(project / "data" / "database" / "knowledge.db")
    try:
        assert database.execute("SELECT expression FROM equation ORDER BY id").fetchall() == before
    finally:
        database.close()
