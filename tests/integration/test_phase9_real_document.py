"""Phase 9 on the real document, in a fresh scratch project (step 12; ADRs 0035-0037).

A **copy** of the accepted `network theory .pdf` is extracted into a new temporary
project root and classified (rule R1); `index` builds the derived index there; then every
query mode runs, one process per command. The live database is never used: this project is created here and
discarded.

Skipped when the document is not on this machine, as in Phases 5 to 8. Only the
query engine's invariants are asserted - scope, provenance, labels, determinism, and
that nothing is written. Counts are printed, never asserted: they are the extractor's
yield, not Phase 9's.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys

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
    base = tmp_path_factory.mktemp("phase9_real")
    copy = base / ACCEPTANCE_PDF.name
    shutil.copyfile(ACCEPTANCE_PDF, copy)
    assert _sha256(copy).upper() == ACCEPTANCE_SHA256, "this is not the accepted document"
    root = base / "project"
    db = root / "data" / "database" / "knowledge.db"

    code, out, err = _rudra(root, "extract", str(copy), timeout=900)
    assert code == 0, err or out
    code, out, err = _rudra(root, "classify", "--run", "RUN-00000001")  # the fresh project's one run
    assert code == 0, err or out
    extracted = _sha256(db)
    code, out, err = _rudra(root, "index", "--json")
    assert code == 0, err or out
    built = json.loads(out)
    after_index = _sha256(db)

    def query(*args):
        code, out, err = _rudra(root, "query", *args, "--json")
        return {"code": code, "out": out, "err": err, "json": json.loads(out) if code in (0, 3) else None}

    answers = {"concept": query("--name", NAME), "concept_again": query("--name", NAME),
               "keyword": query("--keyword", NAME), "prefix": query("--keyword", "volt", "--prefix")}
    concept = answers["concept"]["json"]
    first = next(item for group in concept["concept"]["groups"] for item in group["items"] if "knowledge" in item)
    row = first["evidence"][0]
    answers["exact"] = query(first["knowledge"]["id"])
    answers["page"] = query("--document", row["document_id"], "--page", str(row["page_number"]))
    answers["keyword_again"] = query("--keyword", NAME)

    groups = ", ".join(f"{g['name']} {len(g['items'])}" for g in concept["concept"]["groups"])
    inferred = sum(1 for g in concept["concept"]["groups"] for i in g["items"] for link in i["links"]
                   if link["edge"]["relationship"]["origin"] == "INFERRED")
    entries = built["index"]["status"]["entries"]
    print(f"\n  Phase 9 real-document run (scratch project): concepts named {NAME!r}: "
          f"{len(concept['concept']['concepts'])}; groups: {groups}; inferred links: {inferred}; "
          f"keyword hits: {len(answers['keyword']['json']['keyword']['knowledge'])} knowledge, "
          f"{len(answers['keyword']['json']['keyword']['concepts'])} concepts, "
          f"{len(answers['keyword']['json']['keyword']['pages'])} pages; index entries {entries}, "
          f"pages {built['index']['status']['pages']}, {built['index']['size_bytes']} bytes "
          f"(estimate {built['index']['estimate_bytes']}); "
          f"ingestion: {[d['reason'] for d in built['documents']]}")
    return {"root": root, "db": db, "copy": copy, "built": built, "answers": answers,
            "extracted": extracted, "after_index": after_index, "after_queries": _sha256(db)}


def test_the_index_is_built_fresh_from_the_real_database_without_changing_it(real):
    built = real["built"]
    assert built["index"]["status"]["state"] == "FRESH"
    assert built["knowledge_database"]["read_only"] is True
    assert real["extracted"] == real["after_index"]
    (document,) = built["documents"]
    assert document["parsed"] is True and document["indexed"] is True


def test_every_mode_answers_on_the_real_document(real):
    for key in ("concept", "keyword", "prefix", "exact", "page"):
        answer = real["answers"][key]
        assert answer["code"] == 0 and answer["json"]["status"] == "FOUND", (key, answer["err"])


def test_only_authorised_evidence_from_my_books_is_returned(real):
    for key in ("concept", "keyword", "exact", "page"):
        answer = real["answers"][key]["json"]
        part = answer.get("concept") or answer.get("exact") or answer.get("page") or answer.get("keyword")
        for source in part["provenance"]["sources"]:
            assert source["authorization"] == "AUTHORIZED", key
            assert source["source_category"] in ("USER_PROVIDED_SOURCE", "LOCAL_SOURCE"), key


def test_every_returned_item_carries_its_evidence_and_labels(real):
    concept = real["answers"]["concept"]["json"]
    for group in concept["concept"]["groups"]:
        for item in group["items"]:
            if "knowledge" in item:
                assert item["evidence"], item["knowledge"]["id"]
            for link in item["links"]:
                edge = link["edge"]
                assert edge["evidence"] or edge["bases"], edge["relationship"]["id"]
                if edge["relationship"]["origin"] == "INFERRED":
                    assert edge["label"].startswith("INFERRED")
    for hit in real["answers"]["keyword"]["json"]["keyword"]["knowledge"]:
        assert hit["label"].startswith("KEYWORD HIT") and hit["evidence"]
    for key in ("concept", "keyword", "exact", "page"):
        trace = real["answers"][key]["json"]["trace"]
        assert trace["persisted"] is False and trace["database"]["read_only"] is True


def test_the_page_is_read_from_stored_segments_in_text_order(real):
    page = real["answers"]["page"]["json"]["page"]
    assert page["segments"]
    starts = [o["evidence"]["char_start"] for o in page["occurrences"] if o["evidence"]["char_start"] is not None]
    assert starts == sorted(starts)


def test_answers_are_deterministic_and_nothing_is_written(real):
    answers = real["answers"]
    assert answers["concept"]["out"] == answers["concept_again"]["out"]
    assert answers["keyword"]["out"] == answers["keyword_again"]["out"]
    assert real["after_queries"] == real["extracted"]
    assert _sha256(ACCEPTANCE_PDF).upper() == ACCEPTANCE_SHA256
    assert _sha256(real["copy"]).upper() == ACCEPTANCE_SHA256
