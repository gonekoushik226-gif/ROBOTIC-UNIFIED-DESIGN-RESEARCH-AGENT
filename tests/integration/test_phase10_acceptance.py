"""Phase 10 acceptance: Part 5 section 201 through the `reason` command (U12; ADR 0038).

Section 201's graph - X requires A and B; A requires C and D; C requires E - and its two
approved variants (U12(b)), each run through `python -m app reason`, one process per
step, in a temporary project root. The live database is never opened.

* **literal**: "Given E, find X" - `reason X --input E`. Only E is supplied, so X cannot
  be determined: C is derived from E, and A and X are blocked by the missing D and B.
* **completed**: `reason X --input E --input D --input B` - X is derived in three steps.

Both variants run on both approved paths (U12(c)), with the same hand-written
expectations:

* **structured fixture**: section 201's graph stored through the validating repository
  (`tests/unit/reasoning_rows.py`, over the shared `QueryRows` builder, unchanged);
* **generated document**: a one-page PDF of original sentences - one definition per
  concept, then one requirement per sentence - taken through the real Phase 4 ingestion
  and Phase 5 extraction by the `extract` command. Extraction is unchanged; the test
  first checks it stored exactly section 201's five dependency relationships.

On both paths no stored item, and no sentence, joins X and E directly: the answer can
only come from the chain.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from types import SimpleNamespace

import pytest

from app.models import RelationType
from app.storage import Repository, connect, migrate
from tests.conftest import PROJECT_ROOT
from tests.unit.pdf_fixtures import make_pdf
from tests.unit.reasoning_rows import ReasoningRows, section_201

# The generated document: original text, one sentence per line.
NAMES = {"X": "Xenode", "A": "Alphon", "B": "Betron", "C": "Cyrite", "D": "Deltor", "E": "Eptor"}
DEFINITIONS = (
    "A xenode is a composite signal block.",
    "An alphon is a primary coupling stage.",
    "A betron is a secondary coupling stage.",
    "A cyrite is a reference element.",
    "A deltor is a bias element.",
    "An eptor is a source element.",
)
REQUIREMENTS = (
    "A xenode requires an alphon.",
    "A xenode requires a betron.",
    "An alphon requires a cyrite.",
    "An alphon depends on a deltor.",
    "A cyrite requires an eptor.",
)
PAGE = "Signal blocks\n" + "\n".join(DEFINITIONS + REQUIREMENTS)

# Section 201's five dependency relationships, in letters.
EDGES = {("X", "REQUIRES", "A"), ("X", "REQUIRES", "B"), ("A", "REQUIRES", "C"),
         ("A", "DEPENDS_ON", "D"), ("C", "REQUIRES", "E")}

LITERAL = ("--input", "E")
COMPLETED = ("--input", "E", "--input", "D", "--input", "B")


def cli(root, *args: str) -> tuple[int, str, str]:
    """One `python -m app` step against the scratch root: (exit code, stdout, stderr)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUDRA_")}
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, "-m", "app", *args, "--project-root", str(root)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(PROJECT_ROOT), timeout=240,
    )
    return done.returncode, done.stdout, done.stderr


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _structured(root) -> dict[str, str]:
    """Section 201 written directly, with the letters as concept names."""
    db = root / "data" / "database" / "knowledge.db"
    db.parent.mkdir(parents=True)
    with closing(connect(db)) as connection:
        migrate(connection, database_path=db)
        w = ReasoningRows(Repository(connection))
        section_201(w)
        w.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {letter: letter for letter in "XABCDE"}


def _generated(root, base) -> dict[str, str]:
    """Section 201 as a document, through the real ingestion and extraction."""
    pdf = base / "section-201.pdf"
    pdf.write_bytes(make_pdf([PAGE]))
    code, out, err = cli(root, "extract", str(pdf))
    assert code == 0, err or out
    return dict(NAMES)


@pytest.fixture(scope="module", params=["structured", "generated"])
def world(request, tmp_path_factory) -> SimpleNamespace:
    base = tmp_path_factory.mktemp(f"phase10_{request.param}")
    root = base / "project"
    names = _structured(root) if request.param == "structured" else _generated(root, base)
    db = root / "data" / "database" / "knowledge.db"
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as check:
        concepts = {name: cid for cid, name in check.execute("SELECT id, canonical_name FROM concept")}
        relationships = check.execute(
            "SELECT id, from_concept_id, relation_type, to_concept_id, origin, from_knowledge_id,"
            " to_knowledge_id, lifecycle_status FROM relationship ORDER BY id"
        ).fetchall()
        statements = [s for (s,) in check.execute("SELECT statement FROM knowledge_object")]
    ids = {letter: concepts[name] for letter, name in names.items()}
    letter_of = {cid: letter for letter, cid in ids.items()}
    before = _sha256(db)
    runs = {}
    for variant, inputs in (("literal", LITERAL), ("completed", COMPLETED)):
        args = ["reason", names["X"]]
        for flag, value in zip(inputs[::2], inputs[1::2]):
            args += [flag, names[value]]
        runs[variant] = SimpleNamespace(json=cli(root, *args, "--json"), again=cli(root, *args, "--json"),
                                        text=cli(root, *args))
    forward = ["reason", "--forward"]
    for letter in "EDB":
        forward += ["--input", names[letter]]
    runs["forward"] = SimpleNamespace(json=cli(root, *forward, "--json"), again=cli(root, *forward, "--json"))
    return SimpleNamespace(path=request.param, root=root, db=db, names=names, ids=ids, letter_of=letter_of,
                           relationships=relationships, statements=statements, before=before,
                           after=_sha256(db), runs=runs)


def _method(world, variant) -> tuple[int, dict, dict]:
    code, out, err = world.runs[variant].json
    assert out, err
    data = json.loads(out)
    return code, data, data["methods"][0]


def _letters(world, ids) -> list[str]:
    return [world.letter_of[i] for i in ids]


def _dependencies(world) -> set[tuple[str, str, str]]:
    return {
        (world.letter_of[f], kind, world.letter_of[t])
        for _, f, kind, t, *_ in world.relationships
        if kind in (RelationType.REQUIRES, RelationType.DEPENDS_ON)
    }


# --------------------------------------------------------------- the stored graph


def test_201_exactly_the_five_dependency_relationships_are_stored(world):
    assert _dependencies(world) == EDGES
    dependencies = [r for r in world.relationships if r[2] in (RelationType.REQUIRES, RelationType.DEPENDS_ON)]
    assert len(dependencies) == 5
    assert {(r[4], r[7]) for r in dependencies} == {("EXPLICIT", "ACTIVE")}
    assert set(world.ids) == set("XABCDE")


def test_201_nothing_stored_or_written_joins_x_and_e_directly(world):
    x, e = world.ids["X"], world.ids["E"]
    assert not [r for r in world.relationships if {r[1], r[3]} == {x, e}]
    for statement in world.statements:
        words = set(re.findall(r"[a-z]+", statement.lower()))
        assert not {world.names["X"].lower(), world.names["E"].lower()} <= words, statement
    if world.path == "generated":
        for sentence in DEFINITIONS + REQUIREMENTS:
            words = set(re.findall(r"[a-z]+", sentence.lower()))
            assert not {"xenode", "eptor"} <= words, sentence


# ----------------------------------------------------------------- the literal variant


def test_201_literal_x_cannot_be_determined_from_e_alone(world):
    code, data, method = _method(world, "literal")

    assert code == 0 and data["status"] == "CANNOT_DETERMINE"
    assert {world.letter_of[n["concept"]["id"]]: n["state"] for n in method["nodes"]} == {
        "X": "BLOCKED", "A": "BLOCKED", "B": "MISSING", "C": "DERIVED", "D": "MISSING", "E": "AVAILABLE"
    }
    blocks = {world.letter_of[n["concept"]["id"]]: [(b["reason"], world.letter_of[b["concept_id"]])
                                                     for b in n["blocks"]] for n in method["nodes"]}
    assert blocks == {"X": [("BLOCKED_REQUIREMENT", "A"), ("MISSING_REQUIREMENT", "B")],
                      "A": [("MISSING_REQUIREMENT", "D")], "B": [], "C": [], "D": [], "E": []}
    assert [(s["number"], world.letter_of[s["concept_id"]], _letters(world, s["requirements"]))
            for s in method["steps"]] == [(1, "C", ["E"])]
    assert [[(world.letter_of[n["concept_id"]], n["state"]) for n in p["nodes"]] for p in method["paths"]] == [
        [("E", "AVAILABLE"), ("C", "DERIVED"), ("A", "BLOCKED"), ("X", "BLOCKED")]
    ]
    assert [(world.letter_of[m["concept"]["id"]], _letters(world, [r["concept_id"] for r in m["required_by"]]))
            for m in method["missing"]] == [("B", ["X"]), ("D", ["A"])]
    assert method["cycles"] == [] and method["paths_complete"] is True
    assert [i["concept"]["id"] for i in data["initial"]["inputs"]] == [world.ids["E"]]


def test_201_literal_steps_cite_the_stored_relationship(world):
    _, _, method = _method(world, "literal")
    (step,) = method["steps"]
    (relationship,) = step["relationships"]
    row = next(r for r in world.relationships if r[0] == relationship)
    assert (world.letter_of[row[1]], row[2], world.letter_of[row[3]]) == ("C", "REQUIRES", "E")
    assert (step["rule"], step["rule_version"]) == ("REQUIREMENT_SET", "1")


# --------------------------------------------------------------- the completed variant


def test_201_completed_x_is_derived_in_three_steps(world):
    code, data, method = _method(world, "completed")

    assert code == 0 and data["status"] == "DETERMINED" and method["state"] == "DERIVED"
    assert {world.letter_of[n["concept"]["id"]]: (n["state"], n["origins"]) for n in method["nodes"]} == {
        "X": ("DERIVED", ["DERIVED"]), "A": ("DERIVED", ["DERIVED"]), "C": ("DERIVED", ["DERIVED"]),
        "B": ("AVAILABLE", ["USER_INPUT"]), "D": ("AVAILABLE", ["USER_INPUT"]), "E": ("AVAILABLE", ["USER_INPUT"]),
    }
    assert [(s["number"], world.letter_of[s["concept_id"]], _letters(world, s["requirements"]))
            for s in method["steps"]] == [(1, "C", ["E"]), (2, "A", ["C", "D"]), (3, "X", ["A", "B"])]
    assert [[world.letter_of[n["concept_id"]] for n in p["nodes"]] for p in method["paths"]] == [
        ["B", "X"], ["D", "A", "X"], ["E", "C", "A", "X"]
    ]
    assert method["missing"] == [] and method["cycles"] == []
    assert all(not n["blocks"] and not n["conditional_on"] for n in method["nodes"])


def test_201_forward_from_the_completed_inputs_derives_c_a_and_x(world):
    code, out, err = world.runs["forward"].json
    data = json.loads(out)
    assert code == 0 and data["status"] == "DETERMINED"
    assert _letters(world, data["forward"]["derived"]) == ["C", "A", "X"]


# ----------------------------------------------------------------- the invariants


def test_201_text_output_states_the_same_answer(world):
    for variant, answer in (("literal", "CANNOT_DETERMINE"), ("completed", "DETERMINED")):
        code, out, _ = world.runs[variant].text
        assert code == 0 and f"Answer     : {answer}" in out
    code, out, _ = world.runs["literal"].text
    n = world.names
    assert f"{n['E']} [AVAILABLE] -> {n['C']} [DERIVED] -> {n['A']} [BLOCKED] -> {n['X']} [BLOCKED]" in out


def test_201_output_is_identical_across_processes(world):
    for run in world.runs.values():
        assert run.json[:2] == run.again[:2]


def test_201_reasoning_writes_nothing(world):
    assert world.after == world.before
    assert not (world.root / "data" / "indexes" / "index.db").exists()
    with closing(sqlite3.connect(f"file:{world.db}?mode=ro", uri=True)) as check:
        for table in ("derivation", "calculation", "query"):
            assert check.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
