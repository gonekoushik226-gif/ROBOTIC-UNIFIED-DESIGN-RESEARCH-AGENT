"""Part 7 section 27: the source-file lifecycle acceptance test - mandatory (ADR 0055).

    1. Upload Book A.pdf. 2. Ingest the book. 3. Extract and store knowledge. 4. Verify
    knowledge can be queried. 5. Verify provenance exists. 6. Verify source hash exists.
    7. Delete Book A.pdf. 8. Verify the physical PDF is gone. 9. Query the same knowledge.
    10. Verify knowledge is still available. 11. Verify provenance remains. 12. Verify source
    status says SOURCE_FILE_DELETED. 13. Verify RUDRA does not claim the PDF is currently
    available. 14. Upload the exact same Book A.pdf again. 15. Verify the system recognizes
    the previously known source. 16. Verify duplicate canonical knowledge is NOT
    unnecessarily created. 17. Verify the source becomes AVAILABLE again. 18. Verify
    provenance remains connected.

One test, in the section's order, each step one `python -m app` process in a temporary
project root; the live database is never opened. "Book A.pdf" is a generated one-page book
in pytest's temporary folder; the PDF deleted in step 7 is RUDRA's preserved copy of it.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from tests.conftest import PROJECT_ROOT
from tests.unit.pdf_fixtures import make_pdf

BOOK_A = ("Resistance\n"
          "Resistance is defined as the opposition offered by a material to the flow of current.\n")


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


def _counts(root: Path) -> dict[str, int]:
    with closing(sqlite3.connect(f"file:{root / 'data' / 'database' / 'knowledge.db'}?mode=ro", uri=True)) as db:
        return {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("document", "source", "knowledge_object", "source_occurrence", "extraction_run")}


def test_part7_section27_source_file_lifecycle(tmp_path):
    root = tmp_path / "project"
    book = tmp_path / "Book A.pdf"
    book.write_bytes(make_pdf([BOOK_A]))
    book_hash = hashlib.sha256(book.read_bytes()).hexdigest()

    # 1-3. Upload, ingest, extract and store knowledge.
    code, out, err = cli(root, "extract", str(book))
    assert code == 0, err or out

    # 4. Knowledge can be queried.
    code, found = _json(root, "query", "--name", "resistance")
    assert code == 0 and found["status"] == "FOUND"
    definition = found["concept"]["groups"][0]["items"][0]["knowledge"]
    assert definition["statement"].startswith("Resistance is defined as")

    # 5. Provenance exists. 6. The source hash exists.
    code, provenance = _json(root, "provenance", definition["id"])
    (citation,) = provenance["citations"]
    assert provenance["status"] == "AVAILABLE" and citation["quote"]["status"] == "VERIFIED"
    code, source = _json(root, "source", "DOC-00000001")
    assert code == 0 and source["file_hash"].casefold() == book_hash and source["preserved_file_present"]
    assert source["status"] == "SOURCE_FILE_AVAILABLE"
    stored = Path(source["stored_path"])
    before = _counts(root)

    # 7. Delete Book A.pdf: without confirmation nothing happens; with it, RUDRA's copy goes.
    code, refused = _json(root, "source", "DOC-00000001", "--delete-file")
    assert code == 6 and refused["outcome"] == "REFUSED" and stored.exists()
    code, deletion = _json(root, "source", "DOC-00000001", "--delete-file", "--confirm")
    assert code == 0 and deletion["outcome"] == "DELETED" and deletion["audit_id"].startswith("AUD-")
    assert all(ok for _, ok in deletion["checks"])

    # 8. The physical PDF is gone (and the user's original is untouched).
    assert not stored.exists() and book.exists()

    # 9-10. The same knowledge can be queried, and is still available.
    code, again = _json(root, "query", "--name", "resistance")
    assert code == 0 and again["status"] == "FOUND"
    assert again["concept"]["groups"][0]["items"][0]["knowledge"]["id"] == definition["id"]

    # 11. Provenance remains; 13. RUDRA does not claim the PDF is available.
    code, provenance = _json(root, "provenance", definition["id"])
    (citation,) = provenance["citations"]
    assert provenance["status"] == "AVAILABLE" and citation["evidence"]["page_number"] == 1
    assert citation["source"]["availability"] == "DELETED_BY_USER"
    (document,) = provenance["documents"]
    assert document["preserved_file_present"] is False and document["file"]["status"] != "VERIFIED"

    # 12. The source status says SOURCE_FILE_DELETED; its hash is kept.
    code, source = _json(root, "source", "DOC-00000001")
    assert source["status"] == "SOURCE_FILE_DELETED" and source["file_hash"].casefold() == book_hash
    assert _counts(root) == before  # nothing was removed from the database

    # The deletion was audited.
    with closing(sqlite3.connect(f"file:{root / 'data' / 'database' / 'knowledge.db'}?mode=ro", uri=True)) as db:
        (event,) = db.execute("SELECT event_type, authorization, verification_status, document_id "
                              "FROM audit_event").fetchall()
    assert event == ("SOURCE_FILE_DELETED", "the user's explicit --confirm", "VERIFIED", "DOC-00000001")

    # 14. Upload the exact same Book A.pdf again.
    code, out, err = cli(root, "extract", str(book))
    assert code == 0, err or out

    # 15. The previously known source is recognized; 16. no duplicate knowledge is created.
    assert "DOC-00000001 (already ingested)" in out
    assert _counts(root) == before

    # 17. The source is AVAILABLE again - and so is the file.
    code, source = _json(root, "source", "DOC-00000001")
    assert source["status"] == "SOURCE_FILE_AVAILABLE" and source["preserved_file_present"]
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == book_hash

    # 18. Provenance remains connected.
    code, provenance = _json(root, "provenance", definition["id"])
    (citation,) = provenance["citations"]
    assert provenance["status"] == "AVAILABLE" and provenance["verification"] == "VERIFIED"
    assert citation["source"]["availability"] == "AVAILABLE" and citation["quote"]["status"] == "VERIFIED"


def test_only_rudras_own_copy_can_be_deleted_and_only_once(tmp_path):
    root = tmp_path / "project"
    book = tmp_path / "Book A.pdf"
    book.write_bytes(make_pdf([BOOK_A]))
    assert cli(root, "extract", str(book))[0] == 0
    assert cli(root, "source", "DOC-00000001", "--delete-file", "--confirm")[0] == 0
    assert cli(root, "source", "DOC-00000001", "--delete-file", "--confirm")[0] == 2  # already gone
    assert cli(root, "source", "DOC-00000009")[0] == 3
    assert cli(root, "source", "K-00000001")[0] == 2
    assert cli(root, "source", "DOC-00000001", "--confirm")[0] == 2
    assert book.exists()
