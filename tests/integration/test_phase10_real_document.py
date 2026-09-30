"""Phase 10 on the real document, in a fresh scratch project (Batch C; ADRs 0038-0040).

A **copy** of the accepted `network theory .pdf` is extracted into a new temporary
project root; then `reason` runs on it, one process per command: a target, the same
target given, forward from it, and - when extraction stored any dependency relationship -
the first concept that has one. The live database is never used: this project is
created here and discarded.

Not a gate (U12(c)): skipped when the document is not on this machine, as in Phases 5
to 9. Only the command's invariants are asserted - it answers, it writes nothing, it
opens no index, its output is deterministic. Counts are printed, never asserted: they
are the extractor's yield, not Phase 10's.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

from tests.conftest import PROJECT_ROOT
from tests.integration.test_phase5_acceptance import ACCEPTANCE_PDF, ACCEPTANCE_SHA256

pytestmark = pytest.mark.skipif(
    not ACCEPTANCE_PDF.exists(),
    reason=f"the section 191 acceptance document is not on this machine: {ACCEPTANCE_PDF}",
)

NAME = "Voltage"


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
    base = tmp_path_factory.mktemp("phase10_real")
    copy = base / ACCEPTANCE_PDF.name
    shutil.copyfile(ACCEPTANCE_PDF, copy)
    assert _sha256(copy).upper() == ACCEPTANCE_SHA256, "this is not the accepted document"
    root = base / "project"
    db = root / "data" / "database" / "knowledge.db"

    code, out, err = _rudra(root, "extract", str(copy), timeout=900)
    assert code == 0, err or out
    extracted = _sha256(db)
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as check:
        concepts = check.execute("SELECT count(*) FROM concept").fetchone()[0]
        dependencies = check.execute(
            "SELECT id, from_concept_id FROM relationship WHERE relation_type IN ('REQUIRES', 'DEPENDS_ON')"
            " AND from_concept_id IS NOT NULL AND to_concept_id IS NOT NULL ORDER BY id"
        ).fetchall()

    def reason(*args):
        code, out, err = _rudra(root, "reason", *args, "--json")
        return {"code": code, "out": out, "err": err, "json": json.loads(out) if code in (0, 3) else None}

    runs = {
        "target": reason(NAME), "target_again": reason(NAME),
        "given": reason(NAME, "--input", NAME),
        "forward": reason("--forward", "--input", NAME), "forward_again": reason("--forward", "--input", NAME),
        "authorized": reason(NAME, "--scope", "authorized"),
    }
    if dependencies:
        runs["dependent"] = reason(dependencies[0][1])
    print(f"\n  Phase 10 real-document run (scratch project): concepts {concepts}; stored REQUIRES/DEPENDS_ON "
          f"relationships between concepts {len(dependencies)}; answers: "
          + ", ".join(f"{key} {run['json']['status'] if run['json'] else run['code']}" for key, run in runs.items()))
    return {"root": root, "db": db, "copy": copy, "runs": runs, "extracted": extracted, "after": _sha256(db)}


def test_every_run_answers(real):
    for key, run in real["runs"].items():
        assert run["code"] in (0, 3), (key, run["err"])
        status = run["json"]["status"]
        assert (run["code"] == 0) == (status in ("DETERMINED", "CANNOT_DETERMINE")), key
        assert run["json"]["rule"] == "REQUIREMENT_SET" and run["json"]["rule_version"] == "1"


def test_a_target_the_request_supplies_is_determined_directly(real):
    given = real["runs"]["given"]["json"]
    assert given["status"] == "DETERMINED"
    (method,) = given["methods"]
    assert method["state"] == "AVAILABLE" and method["steps"] == []


def test_only_authorised_sources_from_my_books_back_any_requirement(real):
    for key, run in real["runs"].items():
        data = run["json"]
        nodes = [n for m in data["methods"] for n in m["nodes"]] + (data["forward"] or {}).get("nodes", [])
        for node in nodes:
            for link in node["requirements"]:
                assert link["evidence"], (key, link["relationship"]["id"])
                for source in link["provenance"]["sources"]:
                    assert source["authorization"] == "AUTHORIZED", key


def test_answers_are_deterministic_and_nothing_is_written(real):
    runs = real["runs"]
    assert runs["target"]["out"] == runs["target_again"]["out"]
    assert runs["forward"]["out"] == runs["forward_again"]["out"]
    assert real["after"] == real["extracted"]
    assert not (real["root"] / "data" / "indexes" / "index.db").exists()
    assert _sha256(ACCEPTANCE_PDF).upper() == ACCEPTANCE_SHA256
    assert _sha256(real["copy"]).upper() == ACCEPTANCE_SHA256
