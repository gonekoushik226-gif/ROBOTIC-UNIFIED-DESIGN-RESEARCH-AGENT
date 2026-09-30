"""Phase 23: the final demonstrations (Part 5 sections 234-238) and the final acceptance
scenario (section 251), end to end (ADR 0055).

Each step is one `python -m app` process in a temporary project root - so every answer
after the first extraction is given "after restarting", from persistent knowledge alone.
The live database is never opened. Actions run on the simulated computer (`--dry-run`),
or as file operations inside pytest's temporary folder, as the suite never acts on the
live desktop (ADR 0047 P15-11). The generated documents stand in for an ECE chapter, three
overlapping books, an application manual and a digital-electronics text; the expectations
are written by hand from what those pages state.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from tests.conftest import PROJECT_ROOT
from tests.unit.pdf_fixtures import make_pdf

CHAPTER = (
    "Ohm's law\n"
    "Voltage is defined as the potential difference between two points.\n"
    "Current is defined as the rate of flow of charge.\n"
    "Resistance is defined as the opposition offered by a material to the flow of current.\n"
    "Voltage is a prerequisite for resistance.\n"
    "Resistance depends on current.\n"
    "V = I * R\n"
    "where V is the voltage in volts, I is the current in amperes and R is the resistance in ohms.\n"
    "JK flip-flop excitation is defined as the J and K input values needed for each transition of a JK flip-flop.\n"
)
#: The same knowledge in three different books (different documents, the same definition).
SHARED = "{title}\nConductance\nConductance is defined as the ease with which current flows through a material.\n"
MANUAL = (
    "MATLAB Projects\n"
    "Creating a project\n"
    "File\n"
    "-> New Project\n"
    "-> Select Template\n"
    "-> Enter Name\n"
    "-> Create\n"
)
PROCEDURE = (
    "Preparing a work folder\n"
    "Step 1: Create a folder called <folder>.\n"
    "Step 2: Create a file called <folder>/notes.txt.\n"
)


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


def _ask(root, text, *options) -> tuple[int, list[dict]]:
    code, answer = _json(root, "ask", text, *options)
    return code, answer["parts"]


def _extract(root: Path, folder: Path, name: str, page: str, *options: str) -> None:
    pdf = folder / f"{name}.pdf"
    pdf.write_bytes(make_pdf([page]))
    code, out, err = cli(root, "extract", str(pdf), *options)
    assert code == 0, err or out


@pytest.fixture(scope="module")
def library(tmp_path_factory) -> tuple[Path, Path]:
    folder = tmp_path_factory.mktemp("library")
    root = folder / "project"
    _extract(root, folder, "chapter", CHAPTER)                         # DOC-00000001
    for name in ("Book A", "Book B", "Book C"):                        # DOC-00000002 .. 4
        _extract(root, folder, name, SHARED.format(title=f"{name}: circuit notes"))
    _extract(root, folder, "matlab-manual", MANUAL, "--manual", "MATLAB")  # DOC-00000005
    _extract(root, folder, "procedures", PROCEDURE)                    # DOC-00000006
    return root, folder


# ------------------------------------------------------------ section 234


def test_section_234_the_first_real_world_demonstration(library):
    root, _ = library
    # What is X?
    code, (part,) = _ask(root, "What is resistance?")
    assert part["status"] == "ANSWERED" and part["answer"].startswith("Definition: Resistance is defined as")
    assert part["sources"][0].startswith("DOC-00000001 p.1:")
    # Which equations are associated with X? The equation is stored, but no stored link joins
    # it to resistance: the honest answer is Unknown, never a guess.
    code, (part,) = _ask(root, "Which equations are associated with resistance?")
    assert part["status"] == "UNKNOWN"
    # What are the prerequisites?
    code, (part,) = _ask(root, "What are the prerequisites of resistance?")
    assert part["status"] == "ANSWERED" and "Voltage" in part["answer"]
    # Calculate Y - an actual calculation, with the derivation shown.
    code, calculated = _json(root, "calculate", "I", "--formula", "I = V / R", "--input", "V=10 V", "--input", "R=5 Ω")
    assert code == 0 and calculated["status"] == "CALCULATED" and calculated["result"]["displayed"] == "2"
    assert calculated["result"]["unit"] == "A" and calculated["steps"]
    # What source did you use?
    definition = next(b for b in _ask(root, "What is resistance?")[1][0]["basis"] if b.startswith("K-"))
    code, provenance = _json(root, "provenance", definition)
    assert provenance["status"] == "AVAILABLE" and provenance["verification"] == "VERIFIED"
    # What information is missing?
    code, (part,) = _ask(root, "What does resistance require?")
    assert part["status"] == "CANNOT_DETERMINE" and part["missing"] == ["Current (CPT-00000002)"]
    # Are there conflicts? None is stored for resistance, and none is invented.
    code, answer = _json(root, "query", "--name", "resistance")
    assert answer["concept"]["conflicts"] == []


# ------------------------------------------------------------ section 235


def test_section_235_duplicate_knowledge_is_one_canonical_object_with_three_sources(library):
    root, _ = library
    with closing(sqlite3.connect(f"file:{root / 'data' / 'database' / 'knowledge.db'}?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT id FROM knowledge_object WHERE statement LIKE 'Conductance is defined as%'").fetchall()
        (knowledge_id,) = [r[0] for r in rows]
        documents = {r[0] for r in db.execute("SELECT document_id FROM source_occurrence WHERE knowledge_id = ?",
                                               (knowledge_id,))}
    assert documents == {"DOC-00000002", "DOC-00000003", "DOC-00000004"}
    code, review = _json(root, "review", knowledge_id)
    assert code == 0


# ------------------------------------------------------------ section 236


def test_section_236_missing_data_is_named_and_nothing_is_invented(library):
    root, _ = library
    code, answer = _json(root, "calculate", "P", "--formula", "P = A * B * C", "--input", "A=2", "--input", "B=3")
    # `calculate` answers "cannot determine" as an answer (exit 0, ADR 0043), never as a value.
    assert answer["status"] == "CANNOT_DETERMINE" and answer["result"] is None
    assert answer["missing"] == [{"required_by": ["P = A * B * C"], "symbol": "C"}]


# ------------------------------------------------------------ section 237


def test_section_237_computer_control_and_calculation_distinguished(library):
    root, _ = library
    code, (action, calculation) = _ask(root, "Open Calculator and calculate 123 × 456", "--dry-run")
    assert code == 0 and action["path"] == "ACTION" and action["status"] == "DONE"
    assert action["actions"][0].startswith("OPEN_APPLICATION: VERIFIED")
    assert calculation["path"] == "KNOWLEDGE" and calculation["answer"] == "value = 56088"


# ------------------------------------------------------------ section 238


def test_section_238_a_documented_procedure_planned_authorized_executed_verified(library, tmp_path):
    root, _ = library
    code, listed = _json(root, "procedure")
    (step_procedure,) = [p for p in listed["procedures"] if p["name"] == "Procedure stated on page 1"
                         and p["parameters"] == ["folder"]]
    folder = tmp_path / "work"
    # Structured plan and authorization: refused without the user's confirmation ...
    code, refused = _json(root, "procedure", step_procedure["id"], "--execute", "--param", f"folder={folder}")
    assert code == 6 and refused["outcome"] == "REFUSED" and not folder.exists()
    # ... executed and verified with it (file operations inside pytest's temporary folder).
    code, run = _json(root, "procedure", step_procedure["id"], "--execute", "--confirm", "--param", f"folder={folder}")
    assert code == 0 and run["outcome"] == "DONE" and (folder / "notes.txt").exists()
    assert [s["status"] for s in run["execution"]["steps"]] == ["VERIFIED", "VERIFIED"]
    assert step_procedure["labels"] == ["DOCUMENTED_PROCEDURE"]  # never a guessed workflow


# ------------------------------------------------------------ section 251


def test_section_251_the_final_acceptance_scenario(library):
    root, _ = library
    # Preserved, extracted, deduplicated, source-referenced, persistent: every answer below
    # comes from a fresh process ("after restarting the computer").
    code, source = _json(root, "source", "DOC-00000001")
    assert source["preserved_file_present"] and source["status"] == "SOURCE_FILE_AVAILABLE"
    # "Explain JK flip-flop excitation." - found, explained from its source, with provenance.
    code, (part,) = _ask(root, "Explain JK flip-flop excitation.")
    assert part["status"] == "ANSWERED" and "JK flip-flop excitation is defined as" in part["answer"]
    assert part["sources"][0].startswith("DOC-00000001 p.1:")
    # "Calculate this circuit." - which circuit, which formula: asked, not guessed.
    code, (part,) = _ask(root, "Calculate this circuit.")
    assert part["status"] == "NEEDS_INFORMATION" and part["missing"]
    # The circuit named: R2 missing, why, what is available, and the options.
    code, (part,) = _ask(root, "Calculate I, given I = V / (R1 + R2) and V = 10 V and R1 = 5 Ω")
    assert part["status"] == "CANNOT_DETERMINE" and part["missing"] == ["R2"]
    assert part["why"] == ["R2 is required by I = V / (R1 + R2)"]
    assert part["available"] == ["V = 10 V", "R1 = 5 Ω"]
    assert [s.split(" ")[0] for s in part["next_steps"]] == ["Provide", "Use", "Authorize", "Cancel."]
    # "Open MATLAB." - OPEN_APPLICATION(MATLAB), risk-checked, executed and verified (dry run).
    code, (part,) = _ask(root, "Open MATLAB.", "--dry-run")
    assert part["status"] == "DONE" and part["actions"][0].startswith("OPEN_APPLICATION: VERIFIED")
    # "Use the MATLAB manual to create a project called amplifier." - the documented workflow
    # from the declared manual, the name bound, the missing template named; its menu steps
    # are not executed without UI inspection (ADR 0049 P17-1).
    code, (part,) = _ask(root, "Use the MATLAB manual to create a project called amplifier.", "--dry-run")
    assert code == 3 and part["status"] == "NOT_EXECUTED"
    assert part["actions"] == ["File → New Project → Select Template → Enter Name → Create"]
    assert part["missing"] == ["template"] and "Nothing was executed" in part["answer"]
