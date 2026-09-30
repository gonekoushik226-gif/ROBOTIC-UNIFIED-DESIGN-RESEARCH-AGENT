"""Schema versioning and migration tests (Part 4 sections 150-151).

The rule these protect is blunt: "Do not destroy the existing knowledge database
merely to install a new schema."
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.errors import StorageError
from app.models import ALL_ENTITIES
from app.storage import connect, migrate
from app.storage.migrator import (
    CODE_SCHEMA_VERSION,
    applied_migrations,
    available_migrations,
    schema_version,
)


def test_a_fresh_database_starts_at_version_zero(db_path: Path):
    connection = connect(db_path)
    try:
        assert schema_version(connection) == 0
        assert applied_migrations(connection) == ()
    finally:
        connection.close()


def test_migrating_reaches_the_version_this_build_expects(db_path: Path):
    connection = connect(db_path)
    try:
        report = migrate(connection, database_path=db_path)
        assert report.created is True
        assert report.version_before == 0
        assert report.version_after == CODE_SCHEMA_VERSION
        # Every migration on disk, in order - not a hard-coded list, so adding
        # 0003 does not silently make this test wrong.
        assert report.applied == tuple(range(1, CODE_SCHEMA_VERSION + 1))
        assert report.integrity == "ok"
        assert schema_version(connection) == CODE_SCHEMA_VERSION
    finally:
        connection.close()


def test_migration_is_recorded_with_a_checksum(connection):
    history = applied_migrations(connection)
    assert len(history) == CODE_SCHEMA_VERSION
    assert [row["version"] for row in history] == list(
        range(1, CODE_SCHEMA_VERSION + 1)
    )
    assert history[0]["name"] == "initial"
    for row in history:
        assert len(str(row["checksum"])) == 64, row  # sha256 hex
        assert str(row["applied_at"]).endswith("Z"), row


def test_running_again_changes_nothing(db_path: Path):
    connection = connect(db_path)
    try:
        migrate(connection, database_path=db_path)
        second = migrate(connection, database_path=db_path)
        assert second.applied == ()
        assert second.changed is False
        assert second.created is False
        assert second.version_after == CODE_SCHEMA_VERSION
        assert len(applied_migrations(connection)) == CODE_SCHEMA_VERSION
    finally:
        connection.close()


def test_every_entity_gets_a_table(connection):
    """All 22 entities of Part 5 section 184 must be persistable."""
    names = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = [e.TABLE for e in ALL_ENTITIES if e.TABLE not in names]
    assert not missing, f"entities without a table: {missing}"
    assert len(ALL_ENTITIES) == 22


def test_infrastructure_tables_exist(connection):
    names = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"schema_migrations", "database_metadata", "id_sequence"} <= names


def test_every_entity_table_is_strict(connection):
    """ADR 0004: STRICT stops SQLite putting text in an INTEGER column."""
    lax = []
    for entity in ALL_ENTITIES:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (entity.TABLE,),
        ).fetchone()[0]
        if "STRICT" not in sql.upper():
            lax.append(entity.TABLE)
    assert not lax, f"tables without STRICT: {lax}"


def test_a_newer_database_is_refused_not_downgraded(db_path: Path):
    """Opening a database from a future version could misread its data."""
    connection = connect(db_path)
    try:
        migrate(connection, database_path=db_path)
        connection.execute(f"PRAGMA user_version = {CODE_SCHEMA_VERSION + 5}")
        with pytest.raises(StorageError) as caught:
            migrate(connection, database_path=db_path)
        report = caught.value.report
        assert report.stage == "storage.migrate.detect"
        assert report.data_changed is False
        assert report.retry_safe is False
        assert "newer version" in report.summary
    finally:
        connection.close()


def test_existing_data_is_backed_up_before_a_later_migration(db_path: Path, monkeypatch):
    """Part 4 section 151: back up where appropriate, before applying."""
    from app.storage import migrator

    connection = connect(db_path)
    try:
        migrate(connection, database_path=db_path)
        connection.commit()

        next_version = CODE_SCHEMA_VERSION + 1
        extra = migrator.Migration(
            version=next_version, name="probe", sql="CREATE TABLE probe(x TEXT) STRICT;"
        )
        original = migrator.available_migrations
        monkeypatch.setattr(migrator, "available_migrations", lambda: original() + (extra,))
        monkeypatch.setattr(migrator, "CODE_SCHEMA_VERSION", next_version)

        report = migrate(connection, database_path=db_path)

        assert report.applied == (next_version,)
        assert report.backup_path is not None
        assert report.backup_path.is_file()
        assert report.integrity == "ok"

        # The backup is a real, readable database still at the OLD version.
        backup = sqlite3.connect(report.backup_path)
        try:
            assert (
                backup.execute("PRAGMA user_version").fetchone()[0]
                == CODE_SCHEMA_VERSION
            )
            assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            backup.close()
    finally:
        connection.close()


def test_a_failing_migration_leaves_the_previous_version_intact(
    db_path: Path, sample, monkeypatch
):
    """Nothing is destroyed to install a schema, even a broken one.

    `sample` has already stored a provenance chain, so this asserts against a
    database with real content rather than an empty one.
    """
    from app.storage import migrator

    connection = connect(db_path)
    try:
        before = schema_version(connection)
        assert connection.execute("SELECT count(*) FROM knowledge_object").fetchone()[0] == 1

        next_version = CODE_SCHEMA_VERSION + 1
        broken = migrator.Migration(
            version=next_version, name="broken", sql="CREATE TABLE ;"
        )
        original = migrator.available_migrations
        monkeypatch.setattr(migrator, "available_migrations", lambda: original() + (broken,))
        monkeypatch.setattr(migrator, "CODE_SCHEMA_VERSION", next_version)

        with pytest.raises(StorageError) as caught:
            migrate(connection, database_path=db_path)

        report = caught.value.report
        assert report.stage == "storage.migrate.apply"
        assert report.data_changed is False
        assert schema_version(connection) == before
        # The knowledge that was already there is untouched.
        assert connection.execute("SELECT count(*) FROM knowledge_object").fetchone()[0] == 1
    finally:
        connection.close()


def test_migration_scripts_are_discovered_in_order():
    versions = [m.version for m in available_migrations()]
    assert versions == sorted(versions)
    assert versions[0] == 1


def test_checksum_changes_when_a_script_changes():
    from app.storage.migrator import Migration

    a = Migration(version=1, name="x", sql="CREATE TABLE a(x TEXT) STRICT;")
    b = Migration(version=1, name="x", sql="CREATE TABLE b(x TEXT) STRICT;")
    assert a.checksum != b.checksum
