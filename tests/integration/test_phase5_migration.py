"""Migration 0004 on every path a real installation can take (ADR 0018, ADR 0019).

The approved test list: fresh 0->4, v1->4, v2->4, v3->4, a deliberate failure
that must preserve the previous database and its data, and identifier-counter
convergence. One more is here because the failure test found a real defect: a
migration that fails at its LAST statement must leave no trace of its FIRST.

Every migration in this file stops at schema version 4 (Option 1, approved
2026-09-21, ADR 0030): these tests stay about migration 0004 when later migrations
exist. Tests of later migrations belong to the phases that add them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from app.core.errors import StorageError
from app.models import EntityKind
from app.storage import connect, migrate, migrator
from app.storage.migrator import CODE_SCHEMA_VERSION, schema_version

NOW = "2026-09-21T00:00:00.000Z"


#: The real scripts, captured once - a patched `available_migrations` must never
#: call itself.
_REAL_MIGRATIONS = migrator.available_migrations()


def _migrations_through(version: int):
    return tuple(m for m in _REAL_MIGRATIONS if m.version <= version)


def _build_at(db_path: Path, version: int) -> sqlite3.Connection:
    """A database genuinely at an older schema version, built by the real scripts."""
    connection = connect(db_path)
    with mock.patch.object(migrator, "available_migrations", lambda: _migrations_through(version)), \
         mock.patch.object(migrator, "CODE_SCHEMA_VERSION", version):
        migrate(connection, database_path=db_path)
    connection.commit()
    assert schema_version(connection) == version
    return connection


def _migrate_through_4(connection: sqlite3.Connection, db_path: Path):
    """Run the real migrator, stopped at schema version 4 (Option 1, ADR 0030).

    An unpatched `migrate()` would apply every later migration too, and these tests
    would silently stop being about migration 0004.
    """
    with mock.patch.object(migrator, "available_migrations", lambda: _migrations_through(4)), \
         mock.patch.object(migrator, "CODE_SCHEMA_VERSION", 4):
        return migrate(connection, database_path=db_path)


def _seed_v3_content(connection: sqlite3.Connection) -> None:
    """Real rows at version 3: a document, a page, a source, a claim and its evidence."""
    connection.executescript(f"""
        INSERT INTO document (id, created_at, updated_at, filename, original_filename,
            source_type, file_path, file_hash, file_size, mime_type, ingested_at,
            processing_status, processing_version)
        VALUES ('DOC-00000001','{NOW}','{NOW}','a.pdf','a.pdf','PDF','x','h',1,
                'application/pdf','{NOW}','PROCESSED',1);
        INSERT INTO document_segment (id, created_at, updated_at, document_id, page_number,
            ordinal, text, extraction_method, text_origin)
        VALUES ('SEG-00000001','{NOW}','{NOW}','DOC-00000001',7,0,
                'A point is called node.','pypdf','NATIVE_TEXT');
        INSERT INTO source (id, created_at, updated_at, name, source_category,
            authorization, availability, document_id)
        VALUES ('SRC-00000001','{NOW}','{NOW}','a','USER_PROVIDED_SOURCE','AUTHORIZED',
                'AVAILABLE','DOC-00000001');
        INSERT INTO knowledge_object (id, created_at, updated_at, knowledge_type,
            canonical_name, statement, lifecycle_status, certainty, knowledge_version)
        VALUES ('K-00000001','{NOW}','{NOW}','DEFINITION','Definition: Node',
                'A point is called node.','ACTIVE','REPORTED_BY_SOURCE',1);
        INSERT INTO source_occurrence (id, created_at, updated_at, knowledge_id, source_id,
            document_id, original_text, extraction_method, extraction_timestamp,
            segment_id, page_number)
        VALUES ('S-00000001','{NOW}','{NOW}','K-00000001','SRC-00000001','DOC-00000001',
                'A point is called node.','manual','{NOW}','SEG-00000001',7);
        INSERT INTO variable (id, created_at, updated_at, symbol, lifecycle_status)
        VALUES ('VAR-00000001','{NOW}','{NOW}','V','ACTIVE');
        UPDATE id_sequence SET next_value = 2
            WHERE entity_kind IN ('DOC','SEG','SRC','K','S','VAR');
    """)
    connection.commit()


def _counters(connection: sqlite3.Connection) -> dict[str, int]:
    return dict(connection.execute("SELECT entity_kind, next_value FROM id_sequence").fetchall())


# --------------------------------------------------------------------- four paths


def test_this_build_includes_migration_0004():
    """Migration 0004 stays in every later build (Option 1, ADR 0030).

    Asserting `CODE_SCHEMA_VERSION == 4` would break at the next migration, and
    comparing the constant to itself would prove nothing. What this file relies on
    is that migration 0004 is available and the build has not gone backwards.
    """
    assert 4 in {m.version for m in migrator.available_migrations()}
    assert CODE_SCHEMA_VERSION >= 4


def test_fresh_0_to_4(db_path: Path):
    connection = connect(db_path)
    try:
        report = _migrate_through_4(connection, db_path)
        assert report.applied == (1, 2, 3, 4) and report.version_after == 4
        assert report.integrity == "ok"
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"extraction_run", "extraction_issue"} <= tables
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


@pytest.mark.parametrize("start", [1, 2, 3])
def test_upgrade_to_4_from(db_path: Path, start: int):
    connection = _build_at(db_path, start)
    try:
        report = _migrate_through_4(connection, db_path)
        assert report.version_before == start and report.version_after == 4
        assert report.applied == tuple(range(start + 1, 5))
        assert report.integrity == "ok"
        assert report.backup_path is not None and report.backup_path.is_file()
        backup = sqlite3.connect(report.backup_path)
        try:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == start
        finally:
            backup.close()
    finally:
        connection.close()


def test_v3_to_4_keeps_every_row_and_backfills_new_columns_with_null(db_path: Path):
    """The path the real installation takes, with real content in it."""
    connection = _build_at(db_path, 3)
    try:
        _seed_v3_content(connection)
        _migrate_through_4(connection, db_path)
        row = connection.execute(
            "SELECT original_text, char_start, char_end, extraction_run_id "
            "FROM source_occurrence WHERE id = 'S-00000001'"
        ).fetchone()
        assert tuple(row) == ("A point is called node.", None, None, None)
        assert connection.execute(
            "SELECT knowledge_id FROM variable WHERE id = 'VAR-00000001'"
        ).fetchone()[0] is None
        # The rebuilt evidence view still answers for pre-existing rows.
        view = connection.execute(
            "SELECT subject_id, page_number, char_start FROM evidence WHERE id = 'S-00000001'"
        ).fetchone()
        assert tuple(view) == ("K-00000001", 7, None)
    finally:
        connection.close()


# ----------------------------------------------------------- counter convergence


def test_identifier_counters_converge_on_every_path(tmp_path: Path):
    """A-8 / ADR 0013: fresh and upgraded databases hold the same counters, and a
    counter already in use is never reset."""
    results = {}
    for start in (0, 1, 2, 3):
        path = tmp_path / f"v{start}" / "database" / "k.db"
        path.parent.mkdir(parents=True)
        connection = connect(path) if start == 0 else _build_at(path, start)
        try:
            _migrate_through_4(connection, path)
            results[start] = _counters(connection)
        finally:
            connection.close()
    kinds = {kind.value for kind in EntityKind}
    for start, counters in results.items():
        assert set(counters) == kinds, start
        assert counters["RUN"] == 1 and counters["XIS"] == 1
    assert results[0] == results[1] == results[2] == results[3]


def test_a_live_counter_is_not_reset_by_migration_0004(db_path: Path):
    connection = _build_at(db_path, 3)
    try:
        _seed_v3_content(connection)
        _migrate_through_4(connection, db_path)
        assert _counters(connection)["DOC"] == 2
    finally:
        connection.close()


# -------------------------------------------------------- deliberate failure


def test_a_failure_at_the_last_statement_leaves_no_trace_of_the_first(db_path: Path):
    """Regression test for a defect this phase found in the migrator.

    `_apply` used `executescript`, which runs each statement outside a transaction,
    so a migration failing part-way left earlier statements committed: version 3
    with `extraction_run` already present, while the report claimed nothing had
    changed. The whole of migration 0004 plus a failing final statement must now
    roll back completely - and the real 0004 must then apply cleanly.
    """
    connection = _build_at(db_path, 3)
    try:
        _seed_v3_content(connection)
        m4 = next(m for m in _REAL_MIGRATIONS if m.version == 4)
        broken = migrator.Migration(version=4, name=m4.name, sql=m4.sql + "\nCREATE TABLE ;\n")
        with mock.patch.object(migrator, "available_migrations",
                               lambda: _migrations_through(3) + (broken,)), \
             mock.patch.object(migrator, "CODE_SCHEMA_VERSION", 4):
            with pytest.raises(StorageError) as caught:
                migrate(connection, database_path=db_path)
        assert caught.value.report.data_changed is False

        assert schema_version(connection) == 3
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "extraction_run" not in tables and "extraction_issue" not in tables
        columns = {r[1] for r in connection.execute("PRAGMA table_info(source_occurrence)")}
        assert "char_start" not in columns
        view_columns = {r[1] for r in connection.execute("PRAGMA table_info(evidence)")}
        assert "char_start" not in view_columns  # the view rebuild rolled back too
        assert connection.execute("SELECT count(*) FROM knowledge_object").fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

        # And the real migration 0004 then succeeds - no "table already exists".
        report = _migrate_through_4(connection, db_path)
        assert report.version_after == 4
    finally:
        connection.close()


# ---------------------------------------------------------- the new constraints


@pytest.fixture
def v4(db_path: Path):
    connection = _build_at(db_path, 3)
    _seed_v3_content(connection)
    _migrate_through_4(connection, db_path)
    connection.commit()
    yield connection
    connection.close()


def _insert_occurrence(connection, **columns):
    base = dict(id="S-00000009", created_at=NOW, updated_at=NOW, knowledge_id="K-00000001",
                source_id="SRC-00000001", document_id="DOC-00000001", original_text="x",
                extraction_method="m", extraction_timestamp=NOW)
    base.update(columns)
    names = ", ".join(base)
    marks = ", ".join("?" for _ in base)
    connection.execute(f"INSERT INTO source_occurrence ({names}) VALUES ({marks})", tuple(base.values()))


@pytest.mark.parametrize(
    "columns",
    [
        {"segment_id": "SEG-00000001", "char_start": -1},  # negative offset
        {"char_start": 3},  # an offset into no segment
        {"segment_id": "SEG-00000001", "char_end": 5},  # end without start
        {"segment_id": "SEG-00000001", "char_start": 10, "char_end": 4},  # end before start
        {"segment_id": "SEG-00000001", "char_start": "abc"},  # STRICT typing
    ],
)
def test_span_constraints_are_enforced_by_the_schema(v4, columns):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_occurrence(v4, **columns)


def test_a_valid_span_is_accepted(v4):
    _insert_occurrence(v4, segment_id="SEG-00000001", char_start=0, char_end=23)
    assert v4.execute("SELECT char_end FROM source_occurrence WHERE id='S-00000009'").fetchone()[0] == 23


def test_the_known_coercion_gap_still_applies_to_span_columns(v4):
    """Recorded, not mitigated: D-17 (static typing) is deferred. STRICT rejects
    'abc' but coerces a numeric string."""
    _insert_occurrence(v4, segment_id="SEG-00000001", char_start="4", char_end=9)
    stored = v4.execute(
        "SELECT char_start, typeof(char_start) FROM source_occurrence WHERE id='S-00000009'"
    ).fetchone()
    assert tuple(stored) == (4, "integer")


def test_run_numbers_are_unique_per_document_and_running_runs_are_unfinished(v4):
    insert = ("INSERT INTO extraction_run (id, created_at, updated_at, document_id, run_number, "
              "trigger, extractor_version, parser_name, started_at, completed_at, status) "
              "VALUES (?,?,?,?,?,?,?,?,?,?,?)")
    v4.execute(insert, ("RUN-00000001", NOW, NOW, "DOC-00000001", 1, "FIRST_EXTRACTION", "1",
                        "pypdf", NOW, NOW, "COMPLETED"))
    with pytest.raises(sqlite3.IntegrityError):
        v4.execute(insert, ("RUN-00000002", NOW, NOW, "DOC-00000001", 1, "USER_REQUESTED", "1",
                            "pypdf", NOW, NOW, "COMPLETED"))
    with pytest.raises(sqlite3.IntegrityError):
        v4.execute(insert, ("RUN-00000003", NOW, NOW, "DOC-00000001", 2, "USER_REQUESTED", "1",
                            "pypdf", NOW, NOW, "RUNNING"))
    with pytest.raises(sqlite3.IntegrityError):
        v4.execute(insert, ("RUN-00000004", NOW, NOW, "DOC-00000001", 3, "MOON_PHASE", "1",
                            "pypdf", NOW, None, "RUNNING"))


def test_variable_knowledge_links_are_real_foreign_keys(v4):
    with pytest.raises(sqlite3.IntegrityError):
        v4.execute("UPDATE variable SET knowledge_id = 'K-99999999' WHERE id = 'VAR-00000001'")
    v4.execute("UPDATE variable SET knowledge_id = 'K-00000001' WHERE id = 'VAR-00000001'")
    with pytest.raises(sqlite3.IntegrityError):
        v4.execute("DELETE FROM knowledge_object WHERE id = 'K-00000001'")
