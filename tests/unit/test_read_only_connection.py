"""The read-only connection mode (Phase 7, ADR 0031 I7-B).

A lookup must never write. These tests show that `connect(..., read_only=True)`
opens only an existing database, refuses every write, never issues the
journal-mode or synchronous pragmas, leaves the database file's bytes unchanged,
and still sees committed data that sits in the WAL. The default mode is unchanged.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.core.errors import FailureCategory, StorageError
from app.storage import CODE_SCHEMA_VERSION, connect, migrate, schema_version
from app.storage import connection as connection_module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def migrated(db_path: Path) -> Path:
    """A migrated database file, closed, holding one row to read back."""
    connection = connect(db_path)
    migrate(connection, database_path=db_path)
    connection.execute("INSERT INTO database_metadata (key, value) VALUES ('probe', 'kept')")
    connection.commit()
    connection.close()
    return db_path


def test_a_read_only_connection_reads_the_database(migrated: Path):
    connection = connect(migrated, read_only=True)
    try:
        assert schema_version(connection) == CODE_SCHEMA_VERSION
        row = connection.execute(
            "SELECT value FROM database_metadata WHERE key = 'probe'"
        ).fetchone()
        assert row["value"] == "kept"  # rows are sqlite3.Row, as in the default mode
    finally:
        connection.close()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO database_metadata (key, value) VALUES ('new', 'row')",
        "UPDATE database_metadata SET value = 'changed' WHERE key = 'probe'",
        "DELETE FROM database_metadata WHERE key = 'probe'",
        "CREATE TABLE intruder (x INTEGER)",
        "PRAGMA user_version = 99",
    ],
)
def test_every_write_is_refused(migrated: Path, statement: str):
    before = _sha256(migrated)
    connection = connect(migrated, read_only=True)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(statement)
            connection.commit()
    finally:
        connection.close()
    assert _sha256(migrated) == before
    check = connect(migrated, read_only=True)
    try:
        assert schema_version(check) == CODE_SCHEMA_VERSION
        assert check.execute(
            "SELECT value FROM database_metadata WHERE key = 'probe'"
        ).fetchone()["value"] == "kept"
    finally:
        check.close()


def test_query_only_is_on(migrated: Path):
    connection = connect(migrated, read_only=True)
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
    finally:
        connection.close()


def test_the_database_file_is_byte_identical_after_reading(migrated: Path):
    before = _sha256(migrated)
    connection = connect(migrated, read_only=True)
    try:
        for _ in range(3):
            connection.execute("SELECT count(*) FROM concept").fetchone()
            connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    assert _sha256(migrated) == before


def test_a_read_only_open_never_creates_a_database(tmp_path: Path):
    directory = tmp_path / "database"
    directory.mkdir()
    missing = directory / "knowledge.db"
    with pytest.raises(StorageError) as caught:
        connect(missing, read_only=True)
    assert caught.value.report.category is FailureCategory.DATABASE_FAILURE
    assert caught.value.report.data_changed is False
    assert not missing.exists()
    assert list(directory.iterdir()) == []


def test_a_file_that_is_not_a_database_is_refused(tmp_path: Path):
    junk = tmp_path / "knowledge.db"
    junk.write_bytes(b"this is not an SQLite database, only text pretending to be one" * 20)
    before = _sha256(junk)
    with pytest.raises(StorageError) as caught:
        connect(junk, read_only=True)
    assert caught.value.report.category is FailureCategory.DATABASE_FAILURE
    assert _sha256(junk) == before


def test_read_only_mode_never_issues_the_journal_or_synchronous_pragmas(
    migrated: Path, monkeypatch: pytest.MonkeyPatch
):
    statements: list[str] = []
    real_connect = sqlite3.connect

    def recording_connect(*args, **kwargs):
        opened = real_connect(*args, **kwargs)
        opened.set_trace_callback(statements.append)
        return opened

    monkeypatch.setattr(connection_module.sqlite3, "connect", recording_connect)

    # The recorder works: the default mode does issue both pragmas.
    connect(migrated).close()
    default_mode = " ".join(statements).lower()
    assert "journal_mode" in default_mode and "synchronous" in default_mode

    statements.clear()
    connect(migrated, read_only=True).close()
    read_only_mode = " ".join(statements).lower()
    assert "journal_mode" not in read_only_mode
    assert "synchronous" not in read_only_mode
    assert "query_only" in read_only_mode


def test_paths_with_spaces_and_parentheses_open(tmp_path: Path):
    """The project path has both - `RUDRA(ROBOTIC UNIFIED DESGIN RESEARCH AGENT)`."""
    directory = tmp_path / "RUDRA (ROBOTIC) project" / "data base"
    directory.mkdir(parents=True)
    path = directory / "knowledge.db"
    connection = connect(path)
    migrate(connection, database_path=path)
    connection.commit()
    connection.close()
    reader = connect(path, read_only=True)
    try:
        assert schema_version(reader) == CODE_SCHEMA_VERSION
    finally:
        reader.close()


def test_committed_data_still_in_the_wal_is_visible(migrated: Path):
    """`mode=ro` reads the WAL; `immutable=1` would not, which is why it was rejected."""
    writer = connect(migrated)
    try:
        writer.execute("INSERT INTO database_metadata (key, value) VALUES ('in_wal', 'yes')")
        writer.commit()  # committed, but the writer stays open: not checkpointed
        wal = Path(str(migrated) + "-wal")
        assert wal.exists() and wal.stat().st_size > 0
        reader = connect(migrated, read_only=True)
        try:
            row = reader.execute(
                "SELECT value FROM database_metadata WHERE key = 'in_wal'"
            ).fetchone()
            assert row is not None and row["value"] == "yes"
        finally:
            reader.close()
    finally:
        writer.close()


def test_the_default_mode_is_unchanged(db_path: Path):
    connection = connect(db_path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 0
    finally:
        connection.close()
