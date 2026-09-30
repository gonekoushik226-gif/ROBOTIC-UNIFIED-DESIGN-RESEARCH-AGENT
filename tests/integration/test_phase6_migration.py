"""Migration 0005 on every path a real installation can take (ADR 0027).

ADR 0027's list: fresh 0 -> 5; upgrade to 5 from each earlier version, including a
populated version-4 database; a deliberate failure at the last statement that must
leave version 4 and its data intact; each checked with `integrity_check`,
`foreign_key_check`, `graph_integrity()` and counter convergence. Plus the table's
own rules: the basis trigger, the idempotence index (tested with a deliberate
duplicate for every nullable variant, `ARCHITECTURE.md` section 6.4 rule 7) and
`ON DELETE RESTRICT`.

Every migration here stops at schema version 5, as the Phase 5 migration tests stop
at 4 (Option 1, ADR 0030): these tests stay about migration 0005 when later
migrations exist.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from app.core.errors import StorageError
from app.documents import IngestionPipeline, PypdfParser
from app.extraction import ExtractionPipeline
from app.models import EntityKind
from app.storage import Repository, connect, migrate, migrator, queries
from app.storage.migrator import CODE_SCHEMA_VERSION, schema_version
from tests.integration.test_phase5_extraction import TECHNICAL_PAGES
from tests.unit.pdf_fixtures import make_pdf

NOW = "2026-09-23T00:00:00.000Z"

#: The real scripts, captured once - a patched `available_migrations` must never
#: call itself.
_REAL_MIGRATIONS = migrator.available_migrations()

#: What migration 0005 adds, and all it adds.
NEW_OBJECTS = {
    ("table", "relationship_inference"),
    ("index", "sqlite_autoindex_relationship_inference_1"),
    ("index", "ix_relationship_inference_basis"),
    ("index", "ux_relationship_inference_basis"),
    ("trigger", "trg_relationship_inference_basis_insert"),
    ("trigger", "trg_relationship_inference_basis_update"),
}


def _migrations_through(version: int):
    return tuple(m for m in _REAL_MIGRATIONS if m.version <= version)


def _migrate_through(connection: sqlite3.Connection, db_path: Path, version: int):
    with mock.patch.object(migrator, "available_migrations", lambda: _migrations_through(version)), \
         mock.patch.object(migrator, "CODE_SCHEMA_VERSION", version):
        return migrate(connection, database_path=db_path)


def _build_at(db_path: Path, version: int) -> sqlite3.Connection:
    """A database genuinely at an older schema version, built by the real scripts."""
    connection = connect(db_path)
    _migrate_through(connection, db_path, version)
    connection.commit()
    assert schema_version(connection) == version
    return connection


def _objects(connection: sqlite3.Connection) -> dict[tuple[str, str], str | None]:
    return {
        (row[0], row[1]): row[2]
        for row in connection.execute("SELECT type, name, sql FROM sqlite_master")
    }


def _rows(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
    tables = [r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {t: sorted(tuple(r) for r in connection.execute(f'SELECT * FROM "{t}"')) for t in tables}


def _counters(connection: sqlite3.Connection) -> dict[str, int]:
    return dict(connection.execute("SELECT entity_kind, next_value FROM id_sequence").fetchall())


def _verify(connection: sqlite3.Connection) -> None:
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    report = queries.graph_integrity(connection)
    assert report.is_clean, report
    assert report.inferred_without_basis == ()


@pytest.fixture
def populated_v4(tmp_path: Path):
    """A version-4 database holding real Phase 5 output, as the Phase 5 build left it.

    Built by the real migrations and the real ingestion and extraction pipelines on
    a synthetic PDF. The `RI` counter is then removed, because a database the Phase
    5 build migrated never had one - the current enum seeds it on every migration
    (ADR 0013), so this reproduces the live upgrade path exactly.
    """
    db_path = tmp_path / "data" / "database" / "knowledge.db"
    db_path.parent.mkdir(parents=True)
    connection = _build_at(db_path, 4)
    repository = Repository(connection)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(make_pdf(TECHNICAL_PAGES))
    document = IngestionPipeline(repository, PypdfParser(), tmp_path / "documents").ingest(pdf).document
    connection.commit()
    pipeline = ExtractionPipeline(repository)
    run = pipeline.start(document.id)
    connection.commit()
    pipeline.execute(run)
    connection.execute("DELETE FROM id_sequence WHERE entity_kind = 'RI'")
    connection.commit()
    assert "RI" not in _counters(connection)
    yield {"connection": connection, "db_path": db_path}
    connection.close()


# ------------------------------------------------------------------ the paths


def test_this_build_includes_migration_0005():
    assert 5 in {m.version for m in _REAL_MIGRATIONS}
    assert CODE_SCHEMA_VERSION >= 5


def test_fresh_0_to_5(db_path: Path):
    connection = connect(db_path)
    try:
        report = _migrate_through(connection, db_path, 5)
        assert report.applied == (1, 2, 3, 4, 5) and report.version_after == 5
        assert report.integrity == "ok"
        assert NEW_OBJECTS <= set(_objects(connection))
        assert _counters(connection)["RI"] == 1
        _verify(connection)
    finally:
        connection.close()


@pytest.mark.parametrize("start", [1, 2, 3, 4])
def test_upgrade_to_5_from(db_path: Path, start: int):
    connection = _build_at(db_path, start)
    try:
        report = _migrate_through(connection, db_path, 5)
        assert report.version_before == start and report.version_after == 5
        assert report.applied == tuple(range(start + 1, 6))
        assert report.integrity == "ok"
        # A backup is taken before migrating a database that already has a schema.
        assert report.backup_path is not None and report.backup_path.is_file()
        with sqlite3.connect(report.backup_path) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == start
        _verify(connection)
    finally:
        connection.close()


def test_a_populated_version_4_database_keeps_every_row(populated_v4):
    connection, db_path = populated_v4["connection"], populated_v4["db_path"]
    before_rows = _rows(connection)
    before_counters = _counters(connection)
    assert before_rows["concept"] and before_rows["relationship"] and before_rows["source_occurrence"]

    report = _migrate_through(connection, db_path, 5)

    assert (report.version_before, report.version_after, report.applied) == (4, 5, (5,))
    after_rows = _rows(connection)
    for table, rows in before_rows.items():
        if table in ("schema_migrations", "id_sequence"):
            continue
        assert after_rows[table] == rows, table
    assert after_rows["relationship_inference"] == []
    assert [r[0] for r in after_rows["schema_migrations"]][-1] == 5
    # Counters: every existing one untouched, the new one seeded at 1 (ADR 0013).
    after_counters = _counters(connection)
    assert {k: v for k, v in after_counters.items() if k != "RI"} == before_counters
    assert after_counters["RI"] == 1
    _verify(connection)

    # The backup holds the version-4 database with its data.
    with sqlite3.connect(report.backup_path) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 4
        assert backup.execute("SELECT count(*) FROM concept").fetchone()[0] == len(before_rows["concept"])
        assert backup.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'relationship_inference'"
        ).fetchone()[0] == 0


def test_migration_0005_is_additive(populated_v4):
    """No existing table, index, trigger or view changes; only the new objects appear."""
    connection = populated_v4["connection"]
    before = _objects(connection)
    _migrate_through(connection, populated_v4["db_path"], 5)
    after = _objects(connection)
    assert {key: after[key] for key in before} == before
    assert set(after) - set(before) == NEW_OBJECTS


def test_a_failure_at_the_last_statement_of_0005_leaves_version_4_intact(populated_v4):
    connection, db_path = populated_v4["connection"], populated_v4["db_path"]
    before_rows = _rows(connection)
    before_objects = _objects(connection)
    m5 = next(m for m in _REAL_MIGRATIONS if m.version == 5)
    broken = migrator.Migration(version=5, name=m5.name, sql=m5.sql + "\nCREATE TABLE ;\n")

    with mock.patch.object(migrator, "available_migrations", lambda: _migrations_through(4) + (broken,)), \
         mock.patch.object(migrator, "CODE_SCHEMA_VERSION", 5):
        with pytest.raises(StorageError) as caught:
            migrate(connection, database_path=db_path)
    assert caught.value.report.data_changed is False

    assert schema_version(connection) == 4
    assert _objects(connection) == before_objects  # no trace of the first statement
    assert _rows(connection) == before_rows
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # And the real migration 0005 then succeeds - no "table already exists".
    report = _migrate_through(connection, db_path, 5)
    assert report.version_after == 5
    _verify(connection)


def test_identifier_counters_converge_on_every_path_to_5(tmp_path: Path):
    results = {}
    for start in (0, 1, 2, 3, 4):
        path = tmp_path / f"from{start}" / "database" / "knowledge.db"
        path.parent.mkdir(parents=True)
        connection = connect(path) if start == 0 else _build_at(path, start)
        try:
            _migrate_through(connection, path, 5)
            results[start] = _counters(connection)
        finally:
            connection.close()
    kinds = {kind.value for kind in EntityKind}
    for start, counters in results.items():
        assert set(counters) == kinds, start
        assert counters["RI"] == 1
    assert len({tuple(sorted(c.items())) for c in results.values()}) == 1


# -------------------------------------------------- the table's own rules


@pytest.fixture
def v5(populated_v4):
    connection = populated_v4["connection"]
    _migrate_through(connection, populated_v4["db_path"], 5)
    connection.commit()
    relationship = connection.execute("SELECT id FROM relationship ORDER BY id LIMIT 1").fetchone()[0]
    occurrences = [r[0] for r in connection.execute("SELECT id FROM source_occurrence ORDER BY id LIMIT 2")]
    return {"connection": connection, "relationship": relationship, "occurrences": occurrences}


def _insert(connection, **columns):
    base = dict(id="RI-00000001", created_at=NOW, updated_at=NOW, relationship_id=None,
                rule="R1", rule_version="1", basis_occurrence_id=None, matched_text="carry")
    base.update(columns)
    names = ", ".join(base)
    marks = ", ".join("?" for _ in base)
    connection.execute(f"INSERT INTO relationship_inference ({names}) VALUES ({marks})",
                       tuple(base.values()))


def test_a_basis_row_without_any_basis_column_is_refused_on_insert_and_update(v5):
    connection = v5["connection"]
    with pytest.raises(sqlite3.IntegrityError, match="at least one basis column"):
        _insert(connection, relationship_id=v5["relationship"])
    _insert(connection, relationship_id=v5["relationship"], basis_occurrence_id=v5["occurrences"][0])
    with pytest.raises(sqlite3.IntegrityError, match="at least one basis column"):
        connection.execute(
            "UPDATE relationship_inference SET basis_occurrence_id = NULL WHERE id = 'RI-00000001'"
        )


@pytest.mark.parametrize("matched_text", ["carry", None])
def test_the_idempotence_index_refuses_a_deliberate_duplicate(v5, matched_text):
    """COALESCE makes the index enforce even when a nullable column is NULL."""
    connection, rel, (occ, _) = v5["connection"], v5["relationship"], v5["occurrences"]
    _insert(connection, id="RI-00000001", relationship_id=rel, basis_occurrence_id=occ,
            matched_text=matched_text)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insert(connection, id="RI-00000002", relationship_id=rel, basis_occurrence_id=occ,
                matched_text=matched_text)


def test_a_different_basis_for_the_same_edge_is_accepted(v5):
    connection, rel, (occ_a, occ_b) = v5["connection"], v5["relationship"], v5["occurrences"]
    _insert(connection, id="RI-00000001", relationship_id=rel, basis_occurrence_id=occ_a)
    _insert(connection, id="RI-00000002", relationship_id=rel, basis_occurrence_id=occ_b)
    _insert(connection, id="RI-00000003", relationship_id=rel, basis_occurrence_id=occ_a,
            matched_text="half adder")
    _insert(connection, id="RI-00000004", relationship_id=rel, basis_occurrence_id=occ_a,
            rule_version="2")
    assert connection.execute("SELECT count(*) FROM relationship_inference").fetchone()[0] == 4


def test_the_edge_and_the_evidence_a_basis_names_cannot_be_deleted(v5):
    connection, rel, (occ, _) = v5["connection"], v5["relationship"], v5["occurrences"]
    _insert(connection, relationship_id=rel, basis_occurrence_id=occ)
    connection.commit()
    for statement in (f"DELETE FROM relationship WHERE id = '{rel}'",
                      f"DELETE FROM source_occurrence WHERE id = '{occ}'"):
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(statement)
        connection.rollback()


@pytest.mark.parametrize(
    "columns",
    [
        {"rule": " "},
        {"rule_version": ""},
        {"matched_text": "  "},
        {"id": "RI-1"},
        {"created_at": "yesterday"},
        {"relationship_id": "REL-99999999"},
        {"basis_occurrence_id": "S-99999999"},
    ],
)
def test_the_schema_refuses_malformed_basis_rows(v5, columns):
    base = {"relationship_id": v5["relationship"], "basis_occurrence_id": v5["occurrences"][0]}
    base.update(columns)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(v5["connection"], **base)
