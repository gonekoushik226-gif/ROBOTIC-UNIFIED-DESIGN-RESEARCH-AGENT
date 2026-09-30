"""Connection factory tests (decision D-01, ADR 0004).

The single most important assertion in this file is that every connection from
the factory has `foreign_keys` ON. SQLite's default is OFF, per connection, so a
connection opened any other way would silently lose the Part 7 guarantee that a
deleted source cannot cascade into knowledge.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.errors import StorageError
from app.storage import DatabaseRole, connect, database_path, read_metadata


def test_factory_enables_foreign_keys(db_path: Path):
    connection = connect(db_path)
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


def test_a_raw_connection_does_not_enable_them(db_path: Path):
    """Shows what the factory is protecting against, rather than asserting it abstractly."""
    raw = sqlite3.connect(db_path)
    try:
        assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    finally:
        raw.close()


def test_factory_enables_wal(db_path: Path):
    connection = connect(db_path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        connection.close()


def test_in_memory_databases_skip_wal_without_failing(db_path: Path):
    """An in-memory database has no file to journal; asking for WAL is meaningless."""
    connection = connect(":memory:")
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    finally:
        connection.close()


def test_both_database_roles_get_identical_settings(tmp_path: Path):
    """Phase 9 will add index.db; the factory must already treat it the same way."""
    for role, name in ((DatabaseRole.KNOWLEDGE, "k.db"), (DatabaseRole.INDEX, "i.db")):
        connection = connect(tmp_path / name, role=role)
        try:
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            connection.close()


def test_rows_are_accessible_by_column_name(db_path: Path):
    connection = connect(db_path)
    try:
        connection.execute("CREATE TABLE t(a TEXT, b INTEGER) STRICT")
        connection.execute("INSERT INTO t VALUES ('x', 1)")
        row = connection.execute("SELECT a, b FROM t").fetchone()
        assert row["a"] == "x" and row["b"] == 1
    finally:
        connection.close()


def test_opening_an_impossible_path_reports_actionably(tmp_path: Path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(StorageError) as caught:
        connect(blocker / "nested" / "knowledge.db")
    report = caught.value.report
    assert report.stage == "storage.connect"
    assert report.data_changed is False
    assert report.next_options


def test_database_paths_follow_the_two_file_layout(tmp_path: Path):
    """ADR 0004: knowledge.db is authoritative, index.db is rebuildable."""
    from app.config.schema import defaults
    from app.core.paths import PathLayout

    layout = PathLayout.from_config(tmp_path, defaults())
    knowledge = database_path(layout, DatabaseRole.KNOWLEDGE)
    index = database_path(layout, DatabaseRole.INDEX)

    assert knowledge.name == "knowledge.db"
    assert knowledge.parent == layout.database_dir
    assert index.name == "index.db"
    assert index.parent == layout.indexes_dir
    assert knowledge != index


def test_metadata_is_empty_before_migration(db_path: Path):
    """An unmigrated database is a state, not a failure."""
    connection = connect(db_path)
    try:
        assert read_metadata(connection) == {}
    finally:
        connection.close()


def test_metadata_carries_an_instance_id_after_migration(connection):
    """ADR 0006: identifiers are unique per database, so the database is identified."""
    metadata = read_metadata(connection)
    assert "instance_id" in metadata
    assert len(metadata["instance_id"]) == 36
    assert metadata["role"] == "knowledge"
