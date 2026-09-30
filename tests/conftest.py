"""Shared test fixtures.

Every test works inside a temporary project root. No test writes to the real
`data/` or `logs/` directories, and no test touches a user file (Part 4 section 168:
destructive tests must never run against the user's real files).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from app.core.logging_setup import shutdown_logging
from app.documents import ocr

# Windows OCR is switched off for the suite, so every machine gives the same results
# (with or without an OCR language) and a short fixture page is never sent to the
# recognizer. The OCR tests switch it back on for themselves.
os.environ.setdefault(ocr.SWITCH, "1")

#: The real project root, used by tests that read the source tree.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """An empty, writable project root for one test."""
    return tmp_path


@pytest.fixture
def config_file(tmp_path: Path):
    """Write a config file into the temporary project root."""

    def write(body: str) -> Path:
        directory = tmp_path / "config"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "rudra.toml"
        path.write_text(body, encoding="utf-8")
        return path

    return write


@pytest.fixture(autouse=True)
def _close_log_handlers():
    """Release log files after each test so temporary directories can be removed."""
    yield
    shutdown_logging()


# --------------------------------------------------------------- Phase 2 storage


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A path for a knowledge database, with the directories a backup needs."""
    (tmp_path / "database").mkdir(parents=True, exist_ok=True)
    (tmp_path / "backups").mkdir(parents=True, exist_ok=True)
    return tmp_path / "database" / "knowledge.db"


@pytest.fixture
def connection(db_path: Path):
    """An open, migrated knowledge database."""
    from app.storage import connect, migrate

    conn = connect(db_path)
    migrate(conn, database_path=db_path)
    yield conn
    conn.close()


@pytest.fixture
def repo(connection: sqlite3.Connection):
    """A repository over a migrated database."""
    from app.storage import Repository

    return Repository(connection)


@pytest.fixture
def graph(repo):
    """A Phase 3 working set: a service, plus the document and source evidence needs.

    Part 5 section 187 step 6 requires provenance, and provenance requires a
    document. Phase 4 owns PDFs, so the document here is a fixture - exactly as
    Phase 2's were - and no file is read.
    """
    from app.knowledge import ConceptService, Evidence
    from app.models import (
        Authorization,
        Document,
        DocumentProcessingStatus,
        Source,
        SourceAvailability,
        SourceCategory,
    )
    from app.models.base import utc_now

    now = utc_now()
    document = repo.add(
        Document(
            id=repo.new_id(Document),
            created_at=now,
            updated_at=now,
            filename="analog.pdf",
            original_filename="Analog.pdf",
            source_type="PDF",
            file_path="/documents/analog.pdf",
            file_hash="hash-analog",
            file_size=4096,
            mime_type="application/pdf",
            ingested_at=now,
            processing_status=DocumentProcessingStatus.PROCESSED,
            processing_version=1,
        )
    )
    source = repo.add(
        Source(
            id=repo.new_id(Source),
            created_at=now,
            updated_at=now,
            name="Microelectronic Circuits",
            source_category=SourceCategory.USER_PROVIDED_SOURCE,
            authorization=Authorization.AUTHORIZED,
            availability=SourceAvailability.AVAILABLE,
            document_id=document.id,
            file_hash="hash-analog",
        )
    )

    def evidence(text: str, page: int | None = None) -> Evidence:
        return Evidence(
            source_id=source.id,
            document_id=document.id,
            text=text,
            page_number=page,
        )

    return {
        "repo": repo,
        "service": ConceptService(repo),
        "document": document,
        "source": source,
        "evidence": evidence,
    }


@pytest.fixture
def sample(repo):
    """A minimal but complete provenance chain.

    document -> source -> knowledge object -> source occurrence

    This is the shape Part 3 sections 69-75 describe: one canonical knowledge
    object supported by evidence that points back to a document and a source.
    """
    from app.models import (
        Authorization,
        CertaintyState,
        Document,
        DocumentProcessingStatus,
        KnowledgeObject,
        KnowledgeType,
        LifecycleStatus,
        Source,
        SourceAvailability,
        SourceCategory,
        SourceOccurrence,
    )
    from app.models.base import utc_now

    now = utc_now()
    document = repo.add(
        Document(
            id=repo.new_id(Document),
            created_at=now,
            updated_at=now,
            filename="digital_electronics.pdf",
            original_filename="Digital_Electronics.pdf",
            source_type="PDF",
            file_path="/documents/digital_electronics.pdf",
            file_hash="8ae115adcf106c42",
            file_size=10_485_760,
            mime_type="application/pdf",
            ingested_at=now,
            processing_status=DocumentProcessingStatus.PROCESSED,
            processing_version=1,
            document_title="Digital Electronics",
            page_count=520,
        )
    )
    source = repo.add(
        Source(
            id=repo.new_id(Source),
            created_at=now,
            updated_at=now,
            name="Digital Electronics, 2nd edition",
            source_category=SourceCategory.USER_PROVIDED_SOURCE,
            authorization=Authorization.AUTHORIZED,
            availability=SourceAvailability.AVAILABLE,
            document_id=document.id,
            file_hash="8ae115adcf106c42",
        )
    )
    knowledge = repo.add(
        KnowledgeObject(
            id=repo.new_id(KnowledgeObject),
            created_at=now,
            updated_at=now,
            knowledge_type=KnowledgeType.DEFINITION,
            canonical_name="Flip-flop",
            statement="A flip-flop is a bistable circuit.",
            lifecycle_status=LifecycleStatus.ACTIVE,
            certainty=CertaintyState.REPORTED_BY_SOURCE,
            knowledge_version=1,
        )
    )
    occurrence = repo.add(
        SourceOccurrence(
            id=repo.new_id(SourceOccurrence),
            created_at=now,
            updated_at=now,
            knowledge_id=knowledge.id,
            source_id=source.id,
            document_id=document.id,
            original_text="A flip-flop is a bistable circuit.",
            extraction_method="manual",
            extraction_timestamp=now,
            page_number=214,
            section="5.3",
        )
    )
    repo.connection.commit()
    return {
        "document": document,
        "source": source,
        "knowledge": knowledge,
        "occurrence": occurrence,
    }
