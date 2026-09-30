"""Phase 7 acceptance: Part 5 section 195 on a synthetic document (ADR 0031).

Import document -> Extract knowledge -> Store -> Close RUDRA -> Restart RUDRA ->
Query same knowledge. Each step is its own operating-system process that exits
cleanly after its committed work (P7-4, P7-5), in a fresh project root (P7-2).
The result is then traced back to its source (section 68) - which is what section
194's "the original documents and extracted knowledge must remain connected"
means here - and the database is checked for integrity.

The real document runs the same sequence in `test_phase7_real_document.py`; the
helpers here are shared with it.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from app.extraction.normalize import collapse
from app.models import normalize_alias
from app.storage import connect, queries
from tests.conftest import PROJECT_ROOT
from tests.integration.test_phase6_acceptance import SECTION_193_PAGES
from tests.unit.pdf_fixtures import make_pdf


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rudra(project: pathlib.Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """One RUDRA process: it starts, does one command, and exits."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUDRA_")}
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "app", *args, "--project-root", str(project)],
        capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT), timeout=timeout,
    )


def run_section_195(root: pathlib.Path, pdf: pathlib.Path, name: str) -> dict:
    """The section 195 sequence. Returns every process result for the tests."""
    project = root / "project"
    database = project / "data" / "database" / "knowledge.db"

    # Import document -> Extract knowledge -> Store; the process exits: Close RUDRA.
    extract = rudra(project, "extract", str(pdf), timeout=900)
    stored_hash = sha256(database) if database.exists() else None
    # Recorded before any lookup runs, since a read-only open may create sidecars.
    wal = pathlib.Path(str(database) + "-wal")
    wal_after_close = wal.stat().st_size if wal.exists() else None

    # Restart RUDRA -> Query same knowledge (by exact name, then by exact id).
    first_by_name = rudra(project, "lookup", "--name", name, "--json")
    concept_id = json.loads(first_by_name.stdout)["matches"][0]["concept"]["id"]
    first_by_id = rudra(project, "lookup", concept_id, "--json")
    first_text = rudra(project, "lookup", concept_id)

    # A second restart: the same questions again, each in a new process.
    second_by_name = rudra(project, "lookup", "--name", name, "--json")
    second_by_id = rudra(project, "lookup", concept_id, "--json")
    second_text = rudra(project, "lookup", concept_id)

    return {
        "project": project,
        "database": database,
        "pdf": pdf,
        "extract": extract,
        "stored_hash": stored_hash,
        "wal_after_close": wal_after_close,
        "concept_id": concept_id,
        "first": (first_by_name, first_by_id, first_text),
        "second": (second_by_name, second_by_id, second_text),
        "final_hash": sha256(database),
    }


def extract_identifiers(stdout: str) -> tuple[str, str]:
    document = re.search(r"^Document\s*:\s*(DOC-\d+)", stdout, re.MULTILINE).group(1)
    run = re.search(r"^Run\s*:\s*(RUN-\d+)", stdout, re.MULTILINE).group(1)
    return document, run


def check_restart_answers(result: dict, name: str) -> dict:
    """The restarted RUDRA returns the same knowledge the extraction stored."""
    for process in (*result["first"], *result["second"]):
        assert process.returncode == 0, process.stderr
    by_name = json.loads(result["first"][0].stdout)
    by_id = json.loads(result["first"][1].stdout)
    assert by_name["match_count"] == 1
    assert by_name["matches"] == by_id["matches"]
    match = by_name["matches"][0]
    assert normalize_alias(match["concept"]["canonical_name"]) == normalize_alias(name)
    assert match["definitions"], "the stored definition is returned"
    document_id, run_id = extract_identifiers(result["extract"].stdout)
    assert [item["document"]["id"] for item in match["documents"]] == [document_id]
    assert run_id in [run["id"] for run in match["runs"]]
    return match


def check_identical_after_restart(result: dict) -> None:
    """Restart after restart, the answer is byte-identical (deterministic output)."""
    for first, second in zip(result["first"], result["second"]):
        assert first.stdout == second.stdout
        assert first.stdout.strip()


def check_trace_to_source(result: dict, match: dict) -> None:
    """Section 68: the result traces back to the preserved original, page and span."""
    original = sha256(result["pdf"])
    (item,) = match["documents"]
    stored = pathlib.Path(item["document"]["file_path"])
    assert item["preserved_file_present"] is True and stored.is_file()
    assert item["document"]["file_hash"] == original == sha256(stored)

    rows = list(match["occurrences"])
    for definition in match["definitions"]:
        assert definition["evidence"], "every definition has source evidence"
        rows += definition["evidence"]
    for relation in match["relationships"]:
        if relation["relationship"]["origin"] == "EXPLICIT":
            assert relation["evidence"], "every EXPLICIT edge has a stating occurrence"
        rows += relation["evidence"]

    connection = connect(result["database"], read_only=True)
    try:
        for row in rows:
            assert row["document_id"] == item["document"]["id"]
            segment = connection.execute(
                "SELECT text, page_number FROM document_segment WHERE id = ?",
                (row["segment_id"],),
            ).fetchone()
            assert segment is not None
            assert segment["page_number"] == row["page_number"]
            span = segment["text"][row["char_start"]:row["char_end"]]
            if row["subject_kind"] == "CONCEPT":
                # A concept occurrence's span is its sentence; the surface form is in it.
                assert normalize_alias(row["evidence_text"]) in normalize_alias(span)
            else:
                assert collapse(span) == collapse(row["evidence_text"])
    finally:
        connection.close()


def check_integrity(result: dict) -> None:
    connection = connect(result["database"], read_only=True)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        report = queries.graph_integrity(connection)
        assert report.is_clean
        assert report.inferred_without_basis == ()
    finally:
        connection.close()


def check_lookups_wrote_nothing(result: dict) -> None:
    assert result["stored_hash"] is not None
    assert result["final_hash"] == result["stored_hash"]


# ---------------------------------------------------------------- the test run

NAME = "Full adder"


@pytest.fixture(scope="module")
def section_195(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("phase7_acceptance")
    pdf = root / "adders.pdf"
    pdf.write_bytes(make_pdf(SECTION_193_PAGES))
    return run_section_195(root, pdf, NAME)


def check_clean_close(result: dict) -> None:
    """Close RUDRA: the extraction process exited cleanly after its commit."""
    extract = result["extract"]
    assert extract.returncode == 0, extract.stderr
    assert "FIRST_EXTRACTION" in extract.stdout
    assert result["stored_hash"] is not None
    # Nothing committed was left waiting in the WAL when the process ended.
    assert result["wal_after_close"] in (None, 0)


def test_import_extract_store_then_close_rudra(section_195):
    check_clean_close(section_195)


def test_restart_rudra_and_query_the_same_knowledge(section_195):
    check_restart_answers(section_195, NAME)


def test_every_restart_answers_identically(section_195):
    check_identical_after_restart(section_195)


def test_the_knowledge_traces_back_to_the_preserved_source(section_195):
    match = check_restart_answers(section_195, NAME)
    check_trace_to_source(section_195, match)


def test_the_database_is_intact_after_the_restarts(section_195):
    check_integrity(section_195)


def test_the_lookups_wrote_nothing(section_195):
    check_lookups_wrote_nothing(section_195)
