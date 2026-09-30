"""Phase 11 on the real document, in a fresh scratch project (Batch C; ADR 0041 P11-8).

A **copy** of the accepted `network theory .pdf` is extracted into a new temporary
project root. Then `calculate` admits some of its stored equations, **one per request**
(N2), each in its own process, with the equation's own symbols given the value 1 (a
dimensionless placeholder that only exercises the command; nothing is claimed about the
circuit). The live database is never used.

Not a gate (P11-8): skipped when the document is not on this machine, as in Phases 5 to
10. Only the command's invariants are asserted: every run answers, output is
deterministic, nothing is written, no index is opened, and the original and the copy
keep their hash. Counts are printed, never asserted: many stored equations are expected
to be *not usable* (flattened fragments, implicit multiplication; I-C).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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

#: Stored equations admitted, one per request: the first ones by identifier order.
ADMITTED = 12


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


def _target(expression: str) -> str | None:
    left = expression.partition("=")[0].strip()
    return left if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", left) else None


@pytest.fixture(scope="module")
def real(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("phase11_real")
    copy = base / ACCEPTANCE_PDF.name
    shutil.copyfile(ACCEPTANCE_PDF, copy)
    assert _sha256(copy).upper() == ACCEPTANCE_SHA256, "this is not the accepted document"
    root = base / "project"
    db = root / "data" / "database" / "knowledge.db"

    code, out, err = _rudra(root, "extract", str(copy), timeout=900)
    assert code == 0, err or out
    extracted = _sha256(db)
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as check:
        stored = check.execute(
            "SELECT k.id, e.expression FROM knowledge_object k JOIN equation e ON e.knowledge_id = k.id "
            "WHERE k.knowledge_type = 'EQUATION' ORDER BY CAST(substr(k.id, 3) AS INTEGER)"
        ).fetchall()

    runs = []
    for knowledge_id, expression in stored[:ADMITTED]:
        target = _target(expression) or "X"
        symbols = sorted(set(re.findall(r"[A-Za-z][A-Za-z0-9_]*", expression.partition("=")[2])))
        inputs = [arg for s in symbols if s != target for arg in ("--input", f"{s}=1")]
        args = ("calculate", target, "--admit", knowledge_id, *inputs, "--json")
        first = _rudra(root, *args)
        again = _rudra(root, *args)
        runs.append({"id": knowledge_id, "args": args, "first": first, "again": again})
    statuses = Counter(
        json.loads(run["first"][1])["status"] if run["first"][0] in (0, 3) else f"exit {run['first'][0]}"
        for run in runs
    )
    print(f"\n  Phase 11 real-document run (scratch project): stored equations {len(stored)}; admitted one "
          f"per request {len(runs)}; answers {dict(sorted(statuses.items()))}")
    return {"root": root, "db": db, "copy": copy, "runs": runs, "extracted": extracted, "after": _sha256(db),
            "stored": stored}


def test_every_run_answers_or_refuses_a_request_it_cannot_take(real):
    for run in real["runs"]:
        code, out, err = run["first"]
        # 2 is a request the symbols extraction yields cannot form (a symbol spelled by
        # others, an unknown value); everything else is an answer.
        assert code in (0, 2, 3), (run["id"], err)
        if code in (0, 3):
            data = json.loads(out)
            assert data["verification_status"] == "PENDING"
            assert data["database_opened"] is True and data["read_only_connection"] is True


def test_answers_are_deterministic_and_nothing_is_written(real):
    for run in real["runs"]:
        assert run["first"][:2] == run["again"][:2], run["id"]  # exit code and stdout; stderr logs a time
    assert real["after"] == real["extracted"]
    assert not (real["root"] / "data" / "indexes" / "index.db").exists()
    assert _sha256(ACCEPTANCE_PDF).upper() == ACCEPTANCE_SHA256
    assert _sha256(real["copy"]).upper() == ACCEPTANCE_SHA256


def test_an_admitted_equation_is_never_shown_as_a_sourced_or_verified_value(real):
    for run in real["runs"]:
        code, out, _ = run["first"]
        if code != 0:
            continue
        data = json.loads(out)
        if data["admitted"] is not None:
            assert data["admitted"]["label"] == "UNCERTAIN"
        assert "VERIFIED" not in {s["state"] for s in data["symbols"]}
