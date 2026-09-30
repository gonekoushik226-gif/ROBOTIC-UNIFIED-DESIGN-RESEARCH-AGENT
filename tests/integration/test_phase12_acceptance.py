"""Phase 12 acceptance: Part 5 section 205 through the `provenance` command (ADR 0044 P12-17).

    Ask: "Where did you get this?" The system must return source information when
    available. If provenance does not exist: "Provenance unavailable." It must NOT invent
    a citation.

Each step is one `python -m app` process in a temporary project root; the live database is
never opened. Original one-page PDFs (`tests/unit/pdf_fixtures.py`) go through the real
Phase 4 ingestion and Phase 5 extraction by `extract`, unchanged. The expectations are
written by hand:

1. source information when available: a stored definition and its concept, with source,
   document, page, span, extraction method, run and extractor version, and the quote and
   file checks VERIFIED;
2. "Provenance unavailable." for an object with no evidence, for one whose only evidence
   is unauthorised (both written directly into the scratch database), and for a
   calculation answer built only from the request - no citation in any of them;
3. not found (exit 3) for an identifier no row has;
4. a calculation answer with its admitted stored equation exposes section 204's seven
   fields and verifies VERIFIED; 5. a tampered one verifies FAILED;
6. a reasoning answer (section 201's generated document) verifies VERIFIED;
7. the preserved file removed gives the file check INCONCLUSIVE and keeps the
   provenance; changed, it gives FAILED.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import Authorization, KnowledgeType
from app.storage import Repository, connect
from tests.conftest import PROJECT_ROOT
from tests.integration.test_phase10_acceptance import NAMES
from tests.integration.test_phase10_acceptance import PAGE as SECTION_201_PAGE
from tests.unit.pdf_fixtures import make_pdf
from tests.unit.query_rows import QueryRows

PAGE = (
    "Resistance\n"
    "Resistance is defined as the opposition offered by a material \nto the flow of current.\n"
    "Rtotal = R1 + R2\n"
    "Ohm's law gives the current through the series circuit.\n"
    "I = V / Rtotal"
)
DEFINITION = "Resistance is defined as the opposition offered by a material to the flow of current."
INPUTS = ("--input", "R1=10 Ω", "--input", "R2=20 Ω", "--input", "V=10 V")


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


def _extract(base: Path, name: str, page: str) -> Path:
    root = base / "project"
    pdf = base / f"{name}.pdf"
    pdf.write_bytes(make_pdf([page]))
    code, out, err = cli(root, "extract", str(pdf))
    assert code == 0, err or out
    return root


def _ids(root: Path, sql: str, *params) -> list:
    db = root / "data" / "database" / "knowledge.db"
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as check:
        return check.execute(sql, params).fetchall()


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> SimpleNamespace:
    base = tmp_path_factory.mktemp("phase12")
    w = SimpleNamespace(base=base)
    w.root = _extract(base, "resistance", PAGE)
    (w.definition,) = _ids(w.root, "SELECT id FROM knowledge_object WHERE statement = ?", DEFINITION)[0]
    (w.ohm,) = _ids(w.root, "SELECT id FROM knowledge_object WHERE statement = 'I = V / Rtotal'")[0]
    (w.concept,) = _ids(w.root, "SELECT id FROM concept WHERE canonical_name = 'Resistance'")[0]
    (w.document,) = _ids(w.root, "SELECT id FROM document")[0]
    # Section 201's generated document, into the same project, for a reasoning answer.
    pdf = base / "section-201.pdf"
    pdf.write_bytes(make_pdf([SECTION_201_PAGE]))
    assert cli(w.root, "extract", str(pdf))[0] == 0
    # Written directly into the scratch database: an object with no evidence, and one
    # whose only evidence is from a source that is not authorised.
    db = w.root / "data" / "database" / "knowledge.db"
    with closing(connect(db)) as connection:
        rows = QueryRows(Repository(connection))
        w.bare = rows.knowledge(KnowledgeType.CLAIM, "A claim nobody wrote down.").id
        closed = rows.source(rows.document("closed-book"), authorization=Authorization.NOT_AUTHORIZED)
        hidden = rows.knowledge(KnowledgeType.DEFINITION, "A definition from a closed book.")
        rows.knowledge_occurrence(hidden, closed, page=7)
        w.hidden = hidden.id
        rows.commit()
    return w


# ------------------------------------------------ 1. source information when available


def test_a_stored_definition_returns_its_source_information(world):
    code, data = _json(world.root, "provenance", world.definition)

    assert code == 0 and data["status"] == "AVAILABLE"
    (citation,) = data["citations"]
    evidence = citation["evidence"]
    assert evidence["document_id"] == world.document and evidence["page_number"] == 1
    assert evidence["char_start"] is not None and evidence["extraction_method"]
    assert evidence["evidence_text"] == DEFINITION
    from app.extraction.pipeline import EXTRACTOR_VERSION

    assert citation["run"]["extractor_version"] == EXTRACTOR_VERSION
    assert citation["source"]["authorization"] == "AUTHORIZED"
    assert citation["quote"]["status"] == "VERIFIED" and "flattened" in citation["quote"]["detail"]
    assert data["documents"][0]["file"]["status"] == "VERIFIED"
    assert data["verification"] == "VERIFIED"


def test_the_text_answer_states_the_source_and_page(world):
    code, out, _ = cli(world.root, "provenance", world.definition)
    assert code == 0
    assert "Answer      : AVAILABLE - Provenance available" in out
    assert f"document {world.document} p.1" in out and "quote check VERIFIED" in out


def test_a_concepts_provenance_is_its_defining_sentence(world):
    code, data = _json(world.root, "provenance", world.concept)
    assert code == 0 and data["status"] == "AVAILABLE"
    assert data["citations"][0]["quote"]["status"] == "VERIFIED"


# ------------------------------------------------------ 2. Provenance unavailable


def test_an_object_without_evidence_is_provenance_unavailable(world):
    code, out, _ = cli(world.root, "provenance", world.bare)
    assert code == 0 and "Provenance unavailable." in out
    assert "A claim nobody wrote down" not in out and "Evidence    :" not in out


def test_an_object_whose_only_source_is_unauthorised_is_provenance_unavailable(world):
    code, data = _json(world.root, "provenance", world.hidden)
    assert code == 0 and data["status"] == "UNAVAILABLE" and data["message"].startswith("Provenance unavailable.")
    assert data["citations"] == [] and data["item"] is None
    assert data["withheld"]["unauthorized_evidence"] == 1
    assert "closed book" not in json.dumps(data)


def test_a_request_only_answer_is_provenance_unavailable_and_cites_nothing(world):
    path = world.base / "request-only.json"
    code, out, err = cli(world.root, "calculate", "I", "--formula", "Rtotal = R1 + R2",
                         "--formula", "I = V / Rtotal", *INPUTS, "--json")
    assert code == 0, err
    path.write_text(out, encoding="utf-8")

    code, report = _json(world.root, "provenance", "--answer", str(path))

    assert code == 0 and report["status"] == "VERIFIED"
    exposure = report["exposure"]
    assert exposure["source_status"] == "UNAVAILABLE"
    assert exposure["source_message"].startswith("Provenance unavailable.")
    assert exposure["sources"] == [] and exposure["pages"] == [] and exposure["relevant_knowledge"] == []


# ---------------------------------------------------------------- 3. not found


def test_an_identifier_no_row_has_is_not_found(world):
    code, out, _ = cli(world.root, "provenance", "K-00099999")
    assert code == 3 and "NOT_FOUND" in out and "p." not in out


# ------------------------------------------------- 4-5. a calculation answer, and tampering


@pytest.fixture(scope="module")
def admitted_answer(world) -> Path:
    path = world.base / "admitted.json"
    code, out, err = cli(world.root, "calculate", "I", "--formula", "Rtotal = R1 + R2",
                         "--admit", world.ohm, *INPUTS, "--json")
    assert code == 0, err
    path.write_text(out, encoding="utf-8")
    return path


def test_an_admitted_calculation_answer_exposes_section_204s_seven_fields(world, admitted_answer):
    code, report = _json(world.root, "provenance", "--answer", str(admitted_answer))

    assert code == 0 and report["status"] == "VERIFIED"
    exposure = report["exposure"]
    assert exposure["source_status"] == "AVAILABLE"                       # source
    assert exposure["pages"] == [f"{world.document} p.1"]                 # page
    assert exposure["relevant_knowledge"] == [world.ohm]                  # relevant knowledge
    assert exposure["derivation"][1].endswith(f"stored equation {world.ohm}")  # derivation
    assert exposure["calculation"][0].startswith("Step 1: Rtotal = 10 Ω + 20 Ω -> Rtotal = 30 Ω")  # calculation
    assert exposure["calculation"][1].startswith("Step 2: I = 10 V ÷ 30 Ω -> I ≈ 0.333333 A")
    assert exposure["assumptions"] == []                                  # assumptions: none
    assert exposure["verification_status"] == "VERIFIED"                  # verification status


def test_a_tampered_answer_verifies_failed(world, admitted_answer):
    data = json.loads(admitted_answer.read_text(encoding="utf-8"))
    data["steps"][0]["result"]["exact"] = "31"
    tampered = world.base / "tampered.json"
    tampered.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    code, report = _json(world.root, "provenance", "--answer", str(tampered))

    assert code == 0 and report["status"] == "FAILED"
    failed = {c["kind"] for c in report["checks"] if c["status"] == "FAILED"}
    assert {"REPRODUCTION", "INDEPENDENT", "CHAIN"} <= failed


# ------------------------------------------------------------ 6. a reasoning answer


def test_a_reasoning_answer_verifies(world):
    path = world.base / "reason.json"
    code, out, err = cli(world.root, "reason", NAMES["X"], "--input", NAMES["E"], "--input", NAMES["D"],
                         "--input", NAMES["B"], "--json")
    assert code == 0, err
    assert json.loads(out)["status"] == "DETERMINED"
    path.write_text(out, encoding="utf-8")

    code, report = _json(world.root, "provenance", "--answer", str(path))

    assert code == 0 and report["kind"] == "REASONING" and report["status"] == "VERIFIED"
    assert len([c for c in report["checks"] if c["kind"] == "STEP"]) == 3
    assert report["exposure"]["source_status"] == "AVAILABLE"


# ---------------------------------------------------------------- 7. the file states


def test_a_removed_or_changed_preserved_file(tmp_path):
    root = _extract(tmp_path, "resistance", PAGE)
    (definition,) = _ids(root, "SELECT id FROM knowledge_object WHERE statement = ?", DEFINITION)[0]
    ((stored,),) = _ids(root, "SELECT file_path FROM document")
    preserved = Path(stored)

    original = preserved.read_bytes()
    preserved.write_bytes(original + b"%% changed")
    code, data = _json(root, "provenance", definition)
    assert code == 0 and data["documents"][0]["file"]["status"] == "FAILED" and data["verification"] == "FAILED"

    preserved.unlink()
    code, data = _json(root, "provenance", definition)
    assert code == 0 and data["status"] == "AVAILABLE"  # Part 7: provenance survives the file
    assert data["documents"][0]["preserved_file_present"] is False
    assert data["documents"][0]["file"]["status"] == "INCONCLUSIVE"
    assert data["citations"][0]["quote"]["status"] == "VERIFIED"
