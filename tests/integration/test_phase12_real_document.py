"""Phase 12 on the real document, in a fresh scratch project (ADR 0044 P12-17).

A **copy** of the accepted `network theory .pdf` is extracted into a new temporary project
root; then `provenance` is asked about a sample of stored items - the first knowledge
objects, concepts and relationships by identifier, and the document - each in its own
process. The live database is never used.

Not a gate: skipped when the document is not on this machine, as in Phases 5 to 11. Only
the command's invariants are asserted - every run answers, nothing is written, the output
is deterministic, no citation appears without evidence - and the check outcomes are
printed, never asserted: they are the extractor's record, measured here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from contextlib import closing

import pytest

from tests.conftest import PROJECT_ROOT
from tests.integration.test_phase5_acceptance import ACCEPTANCE_PDF, ACCEPTANCE_SHA256

pytestmark = pytest.mark.skipif(
    not ACCEPTANCE_PDF.exists(),
    reason=f"the section 191 acceptance document is not on this machine: {ACCEPTANCE_PDF}",
)

SAMPLE = 6


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rudra(root, *args: str, timeout: int = 300) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUDRA_")}
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, "-m", "app", *args, "--project-root", str(root)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(PROJECT_ROOT), timeout=timeout,
    )
    return done.returncode, done.stdout, done.stderr


@pytest.fixture(scope="module")
def real(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("phase12_real")
    copy = base / ACCEPTANCE_PDF.name
    shutil.copyfile(ACCEPTANCE_PDF, copy)
    assert _sha256(copy).upper() == ACCEPTANCE_SHA256, "this is not the accepted document"
    root = base / "project"
    db = root / "data" / "database" / "knowledge.db"
    code, out, err = _rudra(root, "extract", str(copy), timeout=900)
    assert code == 0, err or out
    extracted = _sha256(db)
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as check:
        ids = []
        for table in ("knowledge_object", "concept", "relationship", "document"):
            ids += [r[0] for r in check.execute(
                f"SELECT id FROM {table} ORDER BY CAST(substr(id, instr(id, '-') + 1) AS INTEGER) LIMIT ?",
                (SAMPLE,))]
    runs = {i: _rudra(root, "provenance", i, "--json") for i in ids}
    again = _rudra(root, "provenance", ids[0], "--json")
    outcomes = Counter()
    for code, out, _ in runs.values():
        if code == 0:
            data = json.loads(out)
            outcomes[f"item {data['status']}"] += 1
            for citation in data["citations"]:
                outcomes[f"quote {citation['quote']['status']}"] += 1
            for document in data["documents"]:
                outcomes[f"file {document['file']['status']}"] += 1
    print(f"\n  Phase 12 real-document run (scratch project): {len(ids)} items asked; "
          f"outcomes {dict(sorted(outcomes.items()))}")
    return {"root": root, "db": db, "copy": copy, "ids": ids, "runs": runs, "again": again,
            "extracted": extracted, "after": _sha256(db)}


def test_every_run_answers(real):
    for identifier, (code, out, err) in real["runs"].items():
        assert code == 0, (identifier, err)
        data = json.loads(out)
        assert data["status"] in ("AVAILABLE", "UNAVAILABLE"), identifier
        if data["status"] == "UNAVAILABLE":
            assert data["message"].startswith("Provenance unavailable.") and data["citations"] == []


def test_every_citation_comes_from_an_authorised_source_with_a_check(real):
    for identifier, (_, out, _) in real["runs"].items():
        for citation in json.loads(out)["citations"]:
            assert citation["source"]["authorization"] == "AUTHORIZED", identifier
            assert citation["quote"]["status"] in ("VERIFIED", "FAILED", "INCONCLUSIVE")


def test_answers_are_deterministic_and_nothing_is_written(real):
    first = real["runs"][real["ids"][0]]
    assert real["again"][:2] == first[:2]
    assert real["after"] == real["extracted"]
    assert not (real["root"] / "data" / "indexes" / "index.db").exists()
    assert _sha256(ACCEPTANCE_PDF).upper() == ACCEPTANCE_SHA256
    assert _sha256(real["copy"]).upper() == ACCEPTANCE_SHA256
