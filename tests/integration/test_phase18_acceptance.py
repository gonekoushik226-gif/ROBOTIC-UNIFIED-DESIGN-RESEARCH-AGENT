"""Phase 18 acceptance: Part 5 section 217 through the `research` command (ADR 0050 P18-14).

    Ask a question for which local knowledge is insufficient. Expected: "Insufficient
    authorized information." Then request Internet authorization. After authorization:
    Search authorized scope -> Retrieve -> Record provenance -> Clearly mark external
    information.

Each step is one `python -m app` process in a temporary project root; the live database is
never opened. **The "website" is a loopback server on 127.0.0.1 inside this test process:
no step connects beyond this machine** (ADR 0050 P18-13). The expectations are written by
hand.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.ui.cli import main as cli_main
from tests.conftest import PROJECT_ROOT
from tests.unit.loopback import MEMRISTOR_PAGE, Loopback, closed_port, site_routes
from tests.unit.pdf_fixtures import make_pdf
from tests.unit.test_internet import LOCAL_PAGE

QUESTION = "What is a memristor?"


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


@pytest.fixture(scope="module")
def web():
    with Loopback(site_routes()) as server:
        yield server


@pytest.fixture
def root(tmp_path) -> Path:
    """A project whose documents say nothing about memristors."""
    pdf = tmp_path / "circuits.pdf"
    pdf.write_bytes(make_pdf(["Resistance\nResistance is defined as the opposition to the flow of current."]))
    project = tmp_path / "project"
    code, out, err = cli(project, "extract", str(pdf))
    assert code == 0, err or out
    return project


def _digest(root: Path) -> str:
    return hashlib.sha256((root / "data" / "database" / "knowledge.db").read_bytes()).hexdigest()


def test_the_command_set_gains_exactly_research():
    before = {"start", "env", "config", "paths", "db", "extract", "classify", "lookup", "review", "edition",
              "merge", "query", "index", "reason", "calculate", "provenance", "interpret", "act", "do",
              "procedure", "manual", "version"}
    # Later phases add their own commands (ADR 0047 onward); Phase 18's own addition is unchanged.
    assert set(cli_main._COMMANDS) - {"diagram", "voice", "ask", "source"} == before | {"research"}


def test_section_217(root, web):
    # 1. Local knowledge is insufficient: said so, the authorization named, nothing fetched.
    before, requests = _digest(root), len(web.requests)
    code, answer = _json(root, "research", QUESTION)
    assert code == 3 and answer["status"] == "INSUFFICIENT_AUTHORIZED_INFORMATION"
    assert answer["message"].startswith("Insufficient authorized information.\n")
    assert "--site URL" in answer["message"] and answer["record"] is None
    assert _digest(root) == before and len(web.requests) == requests

    # 2. After authorization: search the authorized scope, retrieve, record, mark external.
    url = web.url("/docs/memristor.html")
    code, answer = _json(root, "research", QUESTION, "--site", url)
    assert code == 0 and answer["status"] == "EXTERNAL_INFORMATION"
    assert answer["site"] == web.url("/docs/")
    assert dict(map(tuple, answer["labels"])) == {"SOURCE": "AUTHORIZED_EXTERNAL_SOURCE", "URL": url,
                                                  "Retrieved": answer["record"]["retrieved_at"],
                                                  "Status": "EXTERNAL_INFORMATION"}
    record = answer["record"]
    assert record["sha256"] == hashlib.sha256(MEMRISTOR_PAGE).hexdigest()
    assert record["authorization"] == "AUTHORIZED" and record["new"] is True
    assert [c["statement"] for c in answer["external"]][1] == "A memristor is a passive element with 2 terminals."

    # 3. Provenance recorded: available in scope authorized, checks VERIFIED; never my-books.
    claim = answer["external"][1]["knowledge_id"]
    code, provenance = _json(root, "provenance", claim, "--scope", "authorized")
    (citation,) = provenance["citations"]
    assert code == 0 and provenance["status"] == "AVAILABLE" and provenance["verification"] == "VERIFIED"
    assert citation["source"]["source_category"] == "AUTHORIZED_EXTERNAL_SOURCE"
    assert citation["source"]["url"] == url and citation["source"]["availability"] == "EXTERNAL_ONLY"
    assert citation["evidence"]["extraction_timestamp"] == record["retrieved_at"]
    assert citation["quote"]["status"] == "VERIFIED"
    code, mine = _json(root, "provenance", claim)
    assert mine["status"] != "AVAILABLE" and mine["citations"] == []

    # 4. Asked again locally: still insufficient locally; the stored external claims appear
    #    only in the authorized scope, with their retrieval time.
    code, again = _json(root, "research", QUESTION)
    assert code == 3 and again["cached"] == []
    code, again = _json(root, "research", QUESTION, "--scope", "authorized")
    assert len(again["cached"]) == 3 and again["cached"][0][2] == record["retrieved_at"]


def test_the_text_form_marks_external_information(root, web):
    code, out, _ = cli(root, "research", QUESTION, "--site", web.url("/docs/memristor.html"))
    assert code == 0
    for line in ("SOURCE      : AUTHORIZED_EXTERNAL_SOURCE", f"URL         : {web.url('/docs/memristor.html')}",
                 "Status      : EXTERNAL_INFORMATION", "Retrieved   : ", "Authorized  : "):
        assert line in out


@pytest.mark.parametrize(("path", "code"), [("/docs/away.html", 7), ("/docs/huge.html", 7), ("/docs/binary.bin", 7),
                                            ("/docs/unrelated.html", 3)])
def test_failures_store_nothing(root, web, path, code):
    before = _digest(root)
    assert cli(root, "research", QUESTION, "--site", web.url(path))[0] == code
    assert _digest(root) == before


def test_a_refused_connection_stores_nothing(root):
    before = _digest(root)
    code, answer = _json(root, "research", QUESTION, "--site", f"http://127.0.0.1:{closed_port()}/docs/x.html")
    assert code == 7 and answer["status"] == "RETRIEVAL_FAILED" and _digest(root) == before


def test_a_conflict_with_a_local_definition_is_recorded_and_both_shown(tmp_path, web):
    pdf = tmp_path / "notes.pdf"
    pdf.write_bytes(make_pdf([LOCAL_PAGE]))
    project = tmp_path / "project"
    assert cli(project, "extract", str(pdf))[0] == 0
    code, local = _json(project, "research", QUESTION)
    assert code == 0 and local["status"] == "LOCAL"
    code, out, _ = cli(project, "research", QUESTION, "--site", web.url("/docs/memristor.html"))
    assert code == 0 and "Conflict    : CON-" in out and "neither replaces the other" in out
    assert 'LOCAL SOURCE' in out and "3 terminals" in out and "EXTERNAL SOURCE" in out and "2 terminals" in out


@pytest.mark.parametrize(
    "args",
    [("research",), ("research", "Open Calculator."), ("research", QUESTION, "--site", "ftp://example.org/a"),
     ("research", QUESTION, "--site", "http://user:pw@example.org/a"), ("research", QUESTION, "--name", "x"),
     ("research", QUESTION, "--scope", "everything"), ("reason", "X", "--site", "http://example.org/"),
     ("do", "Open Calculator.", "--dry-run", "--site", "http://example.org/")],
)
def test_invalid_requests_exit_2_and_never_connect(root, args):
    assert cli(root, *args)[0] == 2
