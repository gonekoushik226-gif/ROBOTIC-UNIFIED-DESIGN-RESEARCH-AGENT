"""Phase 7 acceptance on the real document, in a fresh project (ADR 0031, P7-2, P7-3).

A **copy** of the accepted `network theory .pdf` is imported into a new temporary
project root and taken through section 195's sequence, one process per step. The
live database is never used: this project is created here and discarded.

Skipped when the document is not on this machine, as in Phases 5 and 6. Counts
are printed, never asserted - they are the extractor's yield, not Phase 7's.
"""

from __future__ import annotations

import json
import shutil

import pytest

from tests.integration.test_phase5_acceptance import ACCEPTANCE_PDF, ACCEPTANCE_SHA256
from tests.integration.test_phase7_acceptance import (
    check_clean_close,
    check_identical_after_restart,
    check_integrity,
    check_lookups_wrote_nothing,
    check_restart_answers,
    check_trace_to_source,
    run_section_195,
    sha256,
)

pytestmark = pytest.mark.skipif(
    not ACCEPTANCE_PDF.exists(),
    reason=f"the section 191 acceptance document is not on this machine: {ACCEPTANCE_PDF}",
)

NAME = "Voltage"


@pytest.fixture(scope="module")
def real_195(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("phase7_real")
    copy = root / ACCEPTANCE_PDF.name
    shutil.copyfile(ACCEPTANCE_PDF, copy)
    assert sha256(copy).upper() == ACCEPTANCE_SHA256, "this is not the accepted document"
    result = run_section_195(root, copy, NAME)
    summary = [line for line in result["extract"].stdout.splitlines()
               if line.startswith(("Document", "Run", "Status", "Pages"))]
    print("\n  Phase 7 real-document run (scratch project): " + " | ".join(summary))
    return result


def test_the_real_document_is_imported_extracted_and_stored_then_rudra_closes(real_195):
    check_clean_close(real_195)


def test_a_restarted_rudra_returns_the_same_real_knowledge(real_195):
    match = check_restart_answers(real_195, NAME)
    (item,) = match["documents"]
    assert item["document"]["original_filename"] == ACCEPTANCE_PDF.name
    assert item["document"]["file_hash"].upper() == ACCEPTANCE_SHA256
    assert any("voltage" in d["knowledge"]["statement"].casefold() for d in match["definitions"])


def test_every_restart_answers_identically(real_195):
    check_identical_after_restart(real_195)


def test_the_real_knowledge_traces_back_to_the_preserved_source(real_195):
    match = check_restart_answers(real_195, NAME)
    check_trace_to_source(real_195, match)


def test_the_real_database_is_intact(real_195):
    check_integrity(real_195)


def test_the_real_lookups_wrote_nothing(real_195):
    check_lookups_wrote_nothing(real_195)


def test_the_lookup_json_is_plain_ascii(real_195):
    """ASCII-escaped JSON survives any console encoding unchanged (U+FFFD, curly quotes)."""
    out = real_195["first"][0].stdout
    assert out.isascii()
    json.loads(out)
