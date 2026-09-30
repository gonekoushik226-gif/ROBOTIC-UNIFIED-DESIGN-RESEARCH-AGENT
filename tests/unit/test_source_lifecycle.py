"""Phase 23: the source-file lifecycle's guards (ADR 0055; Part 7 sections 10-20).

What every test holds the deletion to: only RUDRA's own verified copy is ever deleted; a
deletion that cannot complete changes nothing; and nothing learnt from the document is lost.
"""

from __future__ import annotations

import os
import stat
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.errors import InvalidInputError
from app.documents.lifecycle import SourceLifecycle
from app.models.entities import Document
from app.storage import Repository, connect
from app.ui.cli.main import main
from tests.unit.pdf_fixtures import make_pdf


@pytest.fixture
def project(tmp_path) -> Path:
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(make_pdf(["Resistance\nResistance is defined as the opposition to the flow of current.\n"]))
    root = tmp_path / "project"
    assert main(["extract", str(pdf), "--project-root", str(root)]) == 0
    return root


def _lifecycle(root: Path, connection) -> SourceLifecycle:
    return SourceLifecycle(Repository(connection), root / "data" / "documents")


def test_a_copy_that_does_not_match_its_hash_is_never_deleted(project):
    with closing(connect(project / "data" / "database" / "knowledge.db")) as connection:
        lifecycle = _lifecycle(project, connection)
        stored = Path(lifecycle.status("DOC-00000001").stored_path)
        stored.write_bytes(b"%PDF-1.4 something else")
        with pytest.raises(InvalidInputError):
            lifecycle.delete_file("DOC-00000001", confirmed=True)
        assert stored.exists() and lifecycle.status("DOC-00000001").status == "SOURCE_FILE_AVAILABLE"


def test_a_file_outside_rudras_store_is_never_deleted(project, tmp_path):
    outside = tmp_path / "elsewhere.pdf"
    with closing(connect(project / "data" / "database" / "knowledge.db")) as connection:
        repository = Repository(connection)
        document = repository.get(Document, "DOC-00000001")
        outside.write_bytes(Path(document.file_path).read_bytes())
        repository.update(replace(document, file_path=str(outside)))
        with pytest.raises(InvalidInputError):
            _lifecycle(project, connection).delete_file("DOC-00000001", confirmed=True)
        assert outside.exists()


def test_a_deletion_that_cannot_complete_changes_nothing(project):
    with closing(connect(project / "data" / "database" / "knowledge.db")) as connection:
        lifecycle = _lifecycle(project, connection)
        stored = Path(lifecycle.status("DOC-00000001").stored_path)
        os.chmod(stored, stat.S_IREAD)  # Windows refuses to delete a read-only file
        try:
            with pytest.raises(OSError):
                lifecycle.delete_file("DOC-00000001", confirmed=True)
            status = lifecycle.status("DOC-00000001")
            assert stored.exists() and status.status == "SOURCE_FILE_AVAILABLE"
            assert connection.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 0
        finally:
            os.chmod(stored, stat.S_IREAD | stat.S_IWRITE)


def test_unconfirmed_says_what_would_happen_and_changes_nothing(project):
    with closing(connect(project / "data" / "database" / "knowledge.db")) as connection:
        deletion = _lifecycle(project, connection).delete_file("DOC-00000001", confirmed=False)
        assert deletion.outcome == "REFUSED" and "Run again with --confirm" in deletion.message
        assert "keep the document record, its SHA-256" in deletion.message
        assert Path(deletion.before.stored_path).exists()
