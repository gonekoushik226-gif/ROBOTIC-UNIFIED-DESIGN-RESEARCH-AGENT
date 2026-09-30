"""The Part 6 section 21 rehearsal (decision D-48, ADR 0025).

    Backup -> Corruption/failure simulation -> Restore -> Integrity check ->
    Application verification

Every simulation runs on a **throwaway copy** in a temporary directory. The
project's real database is never truncated, corrupted or deleted here.

What this rehearsal does NOT establish, stated so it is not oversold: protection
against drive failure (backups and database share one physical SSD - D-15 is open),
or a restore-time budget for a large database.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.documents import IngestionPipeline, PypdfParser
from app.extraction import ExtractionPipeline
from app.knowledge.concepts import ConceptService
from app.models import ENTITIES
from app.storage import Repository, connect, migrate, queries
from app.storage.backup import backup_database, restore_database, sidecars
from app.storage.migrator import CODE_SCHEMA_VERSION, schema_version
from tests.conftest import PROJECT_ROOT
from tests.integration.test_phase5_extraction import TECHNICAL_PAGES
from tests.unit.pdf_fixtures import make_pdf


def _snapshot(connection: sqlite3.Connection) -> dict:
    """What 'the application still works' means: real rows through real code."""
    repository = Repository(connection)
    counts = {entity.__name__: repository.count(entity) for entity in ENTITIES}
    concept_id = connection.execute(
        "SELECT id FROM concept WHERE canonical_name = 'Full adder'").fetchone()[0]
    view = ConceptService(repository).retrieve(concept_id)
    return {
        "counts": counts,
        "concept": view.concept.canonical_name,
        "definitions": sorted(d.knowledge.statement for d in view.definitions),
        "occurrences": sorted((o.page_number, o.char_start, o.char_end) for o in view.occurrences),
        "relationships": sorted(r.relation_type.value for r in view.relationships),
    }


def _verify(path: Path, expected: dict, version: int) -> None:
    connection = connect(path)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        # foreign_key_check is blind to trigger-held rules; graph_integrity is not.
        integrity = queries.graph_integrity(connection)
        assert integrity.explicit_without_evidence == () and integrity.orphaned_aliases == ()
        assert schema_version(connection) == version
        assert _snapshot(connection) == expected
    finally:
        connection.close()


@pytest.fixture
def live(tmp_path: Path):
    """A database at the current schema version with real extracted knowledge, then a
    backup of it. Expectations use `CODE_SCHEMA_VERSION`, not a literal (Option 1,
    ADR 0030): this rehearsal validates the current schema, whatever it is."""
    db = tmp_path / "data" / "database" / "knowledge.db"
    db.parent.mkdir(parents=True)
    connection = connect(db)
    migrate(connection, database_path=db)
    repository = Repository(connection)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(make_pdf(TECHNICAL_PAGES))
    document = IngestionPipeline(repository, PypdfParser(), tmp_path / "data" / "documents").ingest(pdf).document
    connection.commit()
    pipeline = ExtractionPipeline(repository)
    run = pipeline.start(document.id)
    connection.commit()
    pipeline.execute(run)
    connection.commit()

    expected = _snapshot(connection)
    backup = backup_database(connection, db)
    connection.close()
    assert not sidecars(db)  # a clean close checkpoints the WAL away
    return {"db": db, "backup": backup, "expected": expected, "tmp": tmp_path}


def _copy_of_live(live, name: str) -> Path:
    target = live["tmp"] / name / "data" / "database" / "knowledge.db"
    target.parent.mkdir(parents=True)
    shutil.copyfile(live["db"], target)
    return target


# ------------------------------------------------------------------- 1. backup


def test_the_backup_is_a_consistent_readable_database(live):
    backup = live["backup"]
    assert backup.is_file() and not sidecars(backup)
    _verify(backup, live["expected"], version=CODE_SCHEMA_VERSION)


# ----------------------------------------------- 2-5. simulate, restore, verify


def test_restore_after_truncation(live):
    target = _copy_of_live(live, "truncated")
    size = target.stat().st_size
    with target.open("r+b") as handle:
        handle.truncate(size // 3)
    damaged = sqlite3.connect(target)
    try:
        result = [r[0] for r in damaged.execute("PRAGMA integrity_check").fetchall()]
    except sqlite3.DatabaseError:
        result = ["unreadable"]
    finally:
        damaged.close()
    assert result != ["ok"]  # the damage is real, not cosmetic
    restore_database(live["backup"], target)
    _verify(target, live["expected"], version=CODE_SCHEMA_VERSION)


def test_restore_after_byte_corruption(live):
    target = _copy_of_live(live, "corrupted")
    with target.open("r+b") as handle:
        handle.seek(4096 * 3)
        handle.write(os.urandom(4096 * 2))
    damaged = sqlite3.connect(target)
    try:
        result = [r[0] for r in damaged.execute("PRAGMA integrity_check").fetchall()]
    except sqlite3.DatabaseError:
        result = ["unreadable"]
    finally:
        damaged.close()
    assert result != ["ok"]  # the damage is real, not cosmetic
    restore_database(live["backup"], target)
    _verify(target, live["expected"], version=CODE_SCHEMA_VERSION)


def test_restore_after_deletion(live):
    target = _copy_of_live(live, "deleted")
    target.unlink()
    assert not target.exists()
    restore_database(live["backup"], target)
    _verify(target, live["expected"], version=CODE_SCHEMA_VERSION)


def _stale_wal_beside(target: Path) -> tuple[Path, Path]:
    """Leave the WAL of a crashed session beside `target`.

    A connection writes without checkpointing, and its -wal/-shm are copied aside
    while it is still open - which is what a crash leaves on disk.
    """
    connection = sqlite3.connect(target)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute(
        "UPDATE concept SET canonical_name = 'WRITTEN BY A CRASHED SESSION' "
        "WHERE canonical_name = 'Full adder'"
    )
    connection.commit()
    saved = []
    for suffix in ("-wal", "-shm"):
        source = target.with_name(target.name + suffix)
        copy = target.with_name(target.name + suffix + ".saved")
        shutil.copyfile(source, copy)
        saved.append(copy)
    connection.close()
    for copy, suffix in zip(saved, ("-wal", "-shm")):
        shutil.copyfile(copy, target.with_name(target.name + suffix))
    return tuple(saved)


def test_restore_with_stale_wal_sidecars_removes_them_first(live):
    """The case a clean-shutdown rehearsal would never reveal (ADR 0025)."""
    target = _copy_of_live(live, "crashed")
    _stale_wal_beside(target)
    assert len(sidecars(target)) == 2
    removed = restore_database(live["backup"], target)
    assert {p.name for p in removed} == {"knowledge.db-wal", "knowledge.db-shm"}
    _verify(target, live["expected"], version=CODE_SCHEMA_VERSION)


def test_a_naive_copy_over_stale_sidecars_would_restore_the_wrong_state(live):
    """Why the sidecar step exists: without it, SQLite replays the crashed
    session's frames onto the restored file and the 'restore' is silently wrong."""
    target = _copy_of_live(live, "naive")
    _stale_wal_beside(target)
    shutil.copyfile(live["backup"], target)  # naive: sidecars left in place
    connection = sqlite3.connect(target)
    try:
        names = {r[0] for r in connection.execute("SELECT canonical_name FROM concept")}
    finally:
        connection.close()
    assert "WRITTEN BY A CRASHED SESSION" in names


def test_restoring_from_a_missing_backup_is_refused(live, tmp_path):
    from app.core.errors import StorageError

    with pytest.raises(StorageError):
        restore_database(tmp_path / "no-such-backup.db", _copy_of_live(live, "refused"))


# ------------------------------------------------- the user-facing path, restored


def test_the_cli_reports_a_restored_database_correctly(live):
    project = live["tmp"] / "restored-project"
    target = project / "data" / "database" / "knowledge.db"
    target.parent.mkdir(parents=True)
    started = time.perf_counter()
    restore_database(live["backup"], target)
    elapsed_ms = (time.perf_counter() - started) * 1000
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUDRA_")}
    result = subprocess.run(
        [sys.executable, "-m", "app", "db", "--project-root", str(project)],
        capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT), timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert f"already at schema version {CODE_SCHEMA_VERSION}" in result.stdout
    assert "Integrity  : ok" in result.stdout
    assert elapsed_ms < 5000


# ----------------------------------------------------- the real rollback artifact


REAL_V2_BACKUP = PROJECT_ROOT / "data" / "backups" / "knowledge-pre-migration-20260920T135846527Z.db"


@pytest.mark.skipif(not REAL_V2_BACKUP.exists(), reason="the real pre-0003 backup is not on this machine")
def test_the_real_pre_0003_backup_is_a_usable_rollback_artifact(tmp_path):
    """The backup migration 0003 actually took, read from a copy - never in place."""
    target = tmp_path / "database" / "knowledge.db"
    target.parent.mkdir(parents=True)
    restore_database(REAL_V2_BACKUP, target)
    connection = connect(target)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert schema_version(connection) == 2
        report = migrate(connection, database_path=target)
        assert (report.version_before, report.version_after) == (2, CODE_SCHEMA_VERSION)
        assert report.integrity == "ok"
    finally:
        connection.close()
