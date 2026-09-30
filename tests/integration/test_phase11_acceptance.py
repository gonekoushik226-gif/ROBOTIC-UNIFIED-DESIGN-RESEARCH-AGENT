"""Phase 11 acceptance: Part 5 section 203 through the `calculate` command (ADR 0041 P11-8).

    R1 = 10 Ω, R2 = 20 Ω, V = 10 V; Rtotal = R1 + R2; I = V / Rtotal
    expected: Rtotal = 30 Ω; I ≈ 0.333333 A

**Worked by hand** (section 167), never by the code under test: Rtotal = 10 Ω + 20 Ω =
30 Ω exactly, shown with "="; I = 10 V ÷ 30 Ω = 1/3 A, which does not terminate, so it is
shown rounded to six significant figures with "≈": 0.333333 A.

Each step is one `python -m app` process in a temporary project root; the live database
is never opened. Both approved paths (N10; `PHASE_11.md` §7):

* **structured request**: both formulas and all inputs in the request; no admission, so
  no database is opened or created (P11-29);
* **generated document**: an original one-page PDF carrying the two equation lines goes
  through the real Phase 4 ingestion, Phase 5 extraction and stage 15 via `extract`, with
  no extraction change. The test first checks exactly those two equations were stored.
  **Exactly one is admitted**, `I = V / Rtotal`; `Rtotal = R1 + R2` is supplied by the
  request, because a request may admit at most one stored equation (N2, §4.5).

The result must include the seven fields section 203 names: inputs, formula,
intermediate result, final result, units, formula source and input source.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from types import SimpleNamespace

import pytest

from tests.conftest import PROJECT_ROOT
from tests.unit.pdf_fixtures import make_pdf

# The generated document: original text, one line each.
PAGE = (
    "Series circuits\n"
    "The total resistance of resistors in series is their sum.\n"
    "Rtotal = R1 + R2\n"
    "Ohm's law gives the current through the series circuit.\n"
    "I = V / Rtotal"
)
INPUTS = ("--input", "R1=10 Ω", "--input", "R2=20 Ω", "--input", "V=10 V")


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


_WORLDS: dict[str, SimpleNamespace] = {}


def _world(path: str, tmp_path_factory) -> SimpleNamespace:
    """Each path's project, built once for the module."""
    if path not in _WORLDS:
        _WORLDS[path] = _build(path, tmp_path_factory)
    return _WORLDS[path]


@pytest.fixture(scope="module", params=["structured", "generated"])
def world(request, tmp_path_factory) -> SimpleNamespace:
    return _world(request.param, tmp_path_factory)


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> SimpleNamespace:
    return _world("generated", tmp_path_factory)


def _build(path: str, tmp_path_factory) -> SimpleNamespace:
    base = tmp_path_factory.mktemp(f"phase11_{path}")
    w = SimpleNamespace(path=path, root=base / "project")
    w.db = w.root / "data" / "database" / "knowledge.db"
    if path == "structured":
        w.base = ("calculate", "I", "--formula", "Rtotal = R1 + R2", "--formula", "I = V / Rtotal")
        w.args = (*w.base, *INPUTS)
        return w
    pdf = base / "series.pdf"
    pdf.write_bytes(make_pdf([PAGE]))
    code, out, err = cli(w.root, "extract", str(pdf))
    assert code == 0, err or out
    with closing(sqlite3.connect(f"file:{w.db}?mode=ro", uri=True)) as check:
        w.equations = check.execute(
            "SELECT k.id, k.statement, k.certainty, k.lifecycle_status, e.expression "
            "FROM knowledge_object k JOIN equation e ON e.knowledge_id = k.id "
            "WHERE k.knowledge_type = 'EQUATION' ORDER BY k.id"
        ).fetchall()
        w.authorised = check.execute(
            "SELECT DISTINCT v.subject_id FROM evidence v JOIN source s ON s.id = v.source_id "
            "WHERE s.authorization = 'AUTHORIZED'"
        ).fetchall()
        w.empty = [check.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("calculation", "derivation")]
    ids = {statement: kid for kid, statement, *_ in w.equations}
    w.ohm, w.series = ids.get("I = V / Rtotal"), ids.get("Rtotal = R1 + R2")
    w.base = ("calculate", "I", "--formula", "Rtotal = R1 + R2", "--admit", str(w.ohm))
    w.args = (*w.base, *INPUTS)
    w.extracted = _sha256(w.db)
    return w


def test_the_generated_document_stored_exactly_the_two_equations(generated):
    world = generated
    # Each equation is printed on one baseline with nothing above, below or beside it, so
    # the page layout proves the text layer holds all of it: the source's own statement
    # (extractor v6, app/documents/pdfmath.py). Without that proof it would be UNCERTAIN.
    assert [(statement, certainty, status, expression) for _, statement, certainty, status, expression
            in world.equations] == [
        ("Rtotal = R1 + R2", "REPORTED_BY_SOURCE", "ACTIVE", "Rtotal = R1 + R2"),
        ("I = V / Rtotal", "REPORTED_BY_SOURCE", "ACTIVE", "I = V / Rtotal"),
    ]
    assert {world.ohm, world.series} <= {row[0] for row in world.authorised}
    assert world.empty == [0, 0]


def test_section_203_expected_values(world):
    code, out, err = cli(world.root, *world.args, "--json")
    assert code == 0, err
    data = json.loads(out)

    assert data["status"] == "CALCULATED" and data["verification_status"] == "PENDING"
    symbols = {s["symbol"]: s for s in data["symbols"]}
    rtotal, current = symbols["Rtotal"]["value"], symbols["I"]["value"]
    assert (rtotal["exact"], rtotal["relation"], rtotal["displayed"], rtotal["unit"]) == ("30", "=", "30", "Ω")
    assert (current["exact"], current["relation"], current["displayed"], current["unit"]) == (
        "1/3", "≈", "0.333333", "A")
    assert data["result"] == current


def test_the_result_includes_section_203s_seven_fields(world):
    code, out, _ = cli(world.root, *world.args, "--json")
    data = json.loads(out)
    first, second = data["steps"]

    # Inputs, with their input source and units.
    assert [(i["symbol"], i["origin"], i["value"]["displayed"], i["value"]["unit"]) for i in data["inputs"]] == [
        ("R1", "USER_INPUT", "10", "Ω"), ("R2", "USER_INPUT", "20", "Ω"), ("V", "USER_INPUT", "10", "V")]
    # Formula, and the intermediate result with its substitution.
    assert (first["text"], first["substitution"], first["result"]["displayed"]) == (
        "Rtotal = R1 + R2", "Rtotal = 10 Ω + 20 Ω", "30")
    # Final result.
    assert (second["text"], second["substitution"], second["result"]["displayed"]) == (
        "I = V / Rtotal", "I = 10 V ÷ 30 Ω", "0.333333")
    assert [(i["symbol"], i["origin"], i["from_step"]) for i in second["inputs"]] == [
        ("V", "USER_INPUT", None), ("Rtotal", "DERIVED", 1)]
    # Formula source: the request for Rtotal; the request or the document for I.
    assert (first["formula_origin"], first["knowledge_id"]) == ("USER_INPUT", None)
    if world.path == "structured":
        assert (second["formula_origin"], second["knowledge_id"]) == ("USER_INPUT", None)
        assert data["admitted"] is None and data["database_opened"] is False
    else:
        assert (second["formula_origin"], second["knowledge_id"]) == ("ADMITTED_STORED_ITEM", world.ohm)
        admitted = data["admitted"]
        assert admitted["label"] == "UNCERTAIN" and admitted["knowledge"]["knowledge_version"] == 1
        (evidence,) = admitted["evidence"]
        assert evidence["page_number"] == 1 and evidence["extraction_run_id"] is not None
        assert [s["authorization"] for s in admitted["provenance"]["sources"]] == ["AUTHORIZED"]
        assert data["read_only_connection"] is True


def test_the_text_output_states_the_answer(world):
    code, out, _ = cli(world.root, *world.args)
    assert code == 0
    assert "Answer      : CALCULATED - I ≈ 0.333333 A" in out
    assert "Rtotal = 30 Ω (exact 30)" in out and "I ≈ 0.333333 A (exact 1/3)" in out


def test_output_is_byte_identical_across_processes_and_nothing_is_written(world):
    first = cli(world.root, *world.args, "--json")[1]
    assert cli(world.root, *world.args, "--json")[1] == first
    assert not (world.root / "data" / "indexes" / "index.db").exists()
    if world.path == "structured":
        assert not world.db.exists()
    else:
        assert _sha256(world.db) == world.extracted
        with closing(sqlite3.connect(f"file:{world.db}?mode=ro", uri=True)) as check:
            assert [check.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                    for t in ("calculation", "derivation")] == [0, 0]


def test_without_r2_the_answer_names_what_is_missing(world):
    code, out, _ = cli(world.root, *world.base, "--input", "R1=10 Ω", "--input", "V=10 V", "--json")
    data = json.loads(out)
    assert code == 0 and data["status"] == "CANNOT_DETERMINE" and data["result"] is None
    assert [(m["symbol"], m["required_by"]) for m in data["missing"]] == [("R2", ["Rtotal = R1 + R2"])]


def test_admitting_both_stored_equations_is_invalid(generated):
    world = generated
    code, _, err = cli(world.root, "calculate", "I", "--admit", str(world.ohm), "--admit",
                       str(world.series), *INPUTS)
    assert code == 2 and "at most one stored equation" in err
