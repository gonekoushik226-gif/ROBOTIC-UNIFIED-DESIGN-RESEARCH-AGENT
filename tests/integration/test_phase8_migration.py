"""Migration 0006 on every path a real installation can take (ADR 0034, P8-22).

Fresh 0 -> 6; upgrade to 6 from each earlier version, including a populated
version-5 database holding Phase 5 knowledge and Phase 6 inferred edges with their
bases; a deliberate failure at the last statement that must leave version 5 intact;
a foreign-key violation that must roll the migration back; and the rebuild of
`relationship`: every row and identifier kept, every dependant recreated, the
foreign keys of `relationship_occurrence` and `relationship_inference` still
resolving and still enforced. Plus the two new tables' own rules.

Every migration here stops at schema version 6 (Option 1, ADR 0030), so these tests
stay about migration 0006 when later migrations exist.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from app.classification import Classifier
from app.core.errors import StorageError
from app.documents import IngestionPipeline, PypdfParser
from app.extraction import ExtractionPipeline
from app.storage import Repository, connect, migrate, migrator, queries
from app.storage.migrator import CODE_SCHEMA_VERSION, schema_version
from tests.integration.test_phase6_acceptance import SECTION_193_PAGES
from tests.unit.pdf_fixtures import make_pdf

NOW = "2026-09-23T00:00:00.000Z"

_REAL_MIGRATIONS = migrator.available_migrations()

#: What migration 0006 adds, and all it adds (the rebuilt relationship's objects keep
#: their names, so they are not "added").
NEW_OBJECTS = {
    ("table", "knowledge_equivalence"),
    ("index", "sqlite_autoindex_knowledge_equivalence_1"),
    ("index", "ix_knowledge_equivalence_canonical"),
    ("index", "ix_knowledge_equivalence_other"),
    ("index", "ix_knowledge_equivalence_occurrence"),
    ("index", "ix_knowledge_equivalence_run"),
    ("index", "ix_knowledge_equivalence_conflict"),
    ("index", "ux_knowledge_equivalence_comparison"),
    ("trigger", "trg_knowledge_equivalence_other_insert"),
    ("trigger", "trg_knowledge_equivalence_other_update"),
    ("table", "concept_equivalence"),
    ("index", "sqlite_autoindex_concept_equivalence_1"),
    ("index", "ux_concept_equivalence_pair"),
    ("index", "ix_concept_equivalence_b"),
    ("index", "ix_concept_equivalence_run"),
}

#: Every object that belongs to `relationship` (enumerated from sqlite_master before
#: the migration was written, as ADR 0034 requires).
RELATIONSHIP_DEPENDANTS = {
    ("index", "sqlite_autoindex_relationship_1"),
    ("index", "ix_relationship_from_concept"),
    ("index", "ix_relationship_from_knowledge"),
    ("index", "ix_relationship_to_concept"),
    ("index", "ix_relationship_to_knowledge"),
    ("index", "ix_relationship_type"),
    ("index", "ux_relationship_active_edge"),
    ("trigger", "trg_relationship_from_exactly_one_insert"),
    ("trigger", "trg_relationship_to_exactly_one_insert"),
    ("trigger", "trg_relationship_no_self_edge_insert"),
    ("trigger", "trg_relationship_from_exactly_one_update"),
    ("trigger", "trg_relationship_to_exactly_one_update"),
    ("trigger", "trg_relationship_no_self_edge_update"),
    ("trigger", "trg_relationship_origin_never_downgrades"),
}


def _migrations_through(version: int):
    return tuple(m for m in _REAL_MIGRATIONS if m.version <= version)


def _migrate_through(connection: sqlite3.Connection, db_path: Path, version: int):
    with mock.patch.object(migrator, "available_migrations", lambda: _migrations_through(version)), \
         mock.patch.object(migrator, "CODE_SCHEMA_VERSION", version):
        return migrate(connection, database_path=db_path)


def _build_at(db_path: Path, version: int) -> sqlite3.Connection:
    connection = connect(db_path)
    _migrate_through(connection, db_path, version)
    connection.commit()
    assert schema_version(connection) == version
    return connection


def _objects(connection) -> dict[tuple[str, str], str | None]:
    return {(r[0], r[1]): r[2] for r in connection.execute("SELECT type, name, sql FROM sqlite_master")}


def _rows(connection) -> dict[str, list[tuple]]:
    tables = [r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {t: sorted(tuple(r) for r in connection.execute(f'SELECT * FROM "{t}"')) for t in tables}


def _counters(connection) -> dict[str, int]:
    return dict(connection.execute("SELECT entity_kind, next_value FROM id_sequence").fetchall())


def _squash(sql: str | None) -> str:
    return re.sub(r"\s+", " ", sql or "").strip()


def _verify(connection) -> None:
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    report = queries.graph_integrity(connection)
    assert report.is_clean, report
    assert report.inferred_without_basis == ()


@pytest.fixture
def populated_v5(tmp_path: Path):
    """A version-5 database with real Phase 5 knowledge and Phase 6 edges and bases.

    Built by the real migrations and pipelines on a synthetic PDF. The two Phase 8
    counters are removed, because a database the Phase 6/7 build migrated never had
    them (the current enum seeds them on every migration, ADR 0013).
    """
    db_path = tmp_path / "data" / "database" / "knowledge.db"
    db_path.parent.mkdir(parents=True)
    connection = _build_at(db_path, 5)
    repository = Repository(connection)
    pdf = tmp_path / "adders.pdf"
    pdf.write_bytes(make_pdf(SECTION_193_PAGES))
    document = IngestionPipeline(repository, PypdfParser(), tmp_path / "documents").ingest(pdf).document
    connection.commit()
    pipeline = ExtractionPipeline(repository)
    run = pipeline.start(document.id)
    connection.commit()
    report = pipeline.execute(run)
    connection.commit()
    assert Classifier(repository).classify(report.run).edges_created >= 1
    connection.execute("DELETE FROM id_sequence WHERE entity_kind IN ('KE', 'CE')")
    connection.commit()
    assert connection.execute("SELECT count(*) FROM relationship_inference").fetchone()[0] >= 1
    yield {"connection": connection, "db_path": db_path}
    connection.close()


# ------------------------------------------------------------------ the paths


def test_this_build_includes_migration_0006():
    assert 6 in {m.version for m in _REAL_MIGRATIONS}
    assert CODE_SCHEMA_VERSION >= 6


def test_fresh_0_to_6(db_path: Path):
    connection = connect(db_path)
    try:
        report = _migrate_through(connection, db_path, 6)
        assert report.applied == (1, 2, 3, 4, 5, 6) and report.version_after == 6
        assert NEW_OBJECTS <= set(_objects(connection))
        assert RELATIONSHIP_DEPENDANTS <= set(_objects(connection))
        assert _counters(connection)["KE"] == 1 and _counters(connection)["CE"] == 1
        _verify(connection)
    finally:
        connection.close()


@pytest.mark.parametrize("start", [1, 2, 3, 4, 5])
def test_upgrade_to_6_from(db_path: Path, start: int):
    connection = _build_at(db_path, start)
    try:
        report = _migrate_through(connection, db_path, 6)
        assert report.version_before == start and report.version_after == 6
        assert report.applied == tuple(range(start + 1, 7))
        assert report.backup_path is not None and report.backup_path.is_file()
        with sqlite3.connect(report.backup_path) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == start
        _verify(connection)
    finally:
        connection.close()


def test_a_populated_version_5_database_keeps_every_row_and_identifier(populated_v5):
    connection, db_path = populated_v5["connection"], populated_v5["db_path"]
    before_rows = _rows(connection)
    before_counters = _counters(connection)

    report = _migrate_through(connection, db_path, 6)

    assert (report.version_before, report.version_after, report.applied) == (5, 6, (6,))
    after_rows = _rows(connection)
    for table, rows in before_rows.items():
        if table in ("schema_migrations", "id_sequence"):
            continue
        assert after_rows[table] == rows, table  # relationship included: identical rows
    assert after_rows["knowledge_equivalence"] == [] and after_rows["concept_equivalence"] == []
    after_counters = _counters(connection)
    assert {k: v for k, v in after_counters.items() if k not in ("KE", "CE")} == before_counters
    assert after_counters["KE"] == after_counters["CE"] == 1
    _verify(connection)
    with sqlite3.connect(report.backup_path) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 5


def test_the_rebuild_recreates_every_dependant_and_changes_nothing_else(populated_v5):
    connection = populated_v5["connection"]
    before = _objects(connection)
    _migrate_through(connection, populated_v5["db_path"], 6)
    after = _objects(connection)

    assert set(after) - set(before) == NEW_OBJECTS
    assert set(before) <= set(after)
    for key in RELATIONSHIP_DEPENDANTS:
        assert _squash(after[key]) == _squash(before[key]), key  # recreated exactly
    # The table itself: the relation-type list gains HAS_PROPERTY, and nothing else
    # about the definition changes (comments aside).
    def definition(sql: str) -> tuple[set[str], str]:
        body = _squash(re.sub(r"--[^\n]*", "", sql))
        listed = re.search(r"relation_type IN \(([^)]*)\)", body).group(1)
        return {v.strip("' ") for v in listed.split(",")}, body.replace(listed, "")

    types_before, rest_before = definition(before[("table", "relationship")])
    types_after, rest_after = definition(after[("table", "relationship")])
    assert types_after == types_before | {"HAS_PROPERTY"} and "HAS_PROPERTY" not in types_before
    assert rest_after == rest_before
    untouched = {k for k in before if k not in RELATIONSHIP_DEPENDANTS and k != ("table", "relationship")}
    assert {k: after[k] for k in untouched} == {k: before[k] for k in untouched}


def test_foreign_keys_into_the_rebuilt_table_still_resolve_and_are_enforced(populated_v5):
    connection = populated_v5["connection"]
    _migrate_through(connection, populated_v5["db_path"], 6)
    for table in ("relationship_occurrence", "relationship_inference", "concept_equivalence"):
        parents = {row[2] for row in connection.execute(f"PRAGMA foreign_key_list('{table}')")}
        assert "relationship" in parents, table
    referenced = connection.execute(
        "SELECT relationship_id FROM relationship_inference LIMIT 1").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM relationship WHERE id = ?", (referenced,))
    connection.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO relationship_occurrence(id, created_at, updated_at, relationship_id, "
            "source_id, document_id, original_text, extraction_method, extraction_timestamp) "
            "SELECT 'RO-99999999', ?, ?, 'REL-99999999', source_id, document_id, 'x', 'm', ? "
            "FROM relationship_occurrence LIMIT 1", (NOW, NOW, NOW))
    connection.rollback()


def test_has_property_is_admitted_only_from_version_6(populated_v5):
    connection = populated_v5["connection"]
    concept, knowledge = connection.execute(
        "SELECT from_concept_id, to_knowledge_id FROM relationship "
        "WHERE relation_type = 'DEFINED_BY' LIMIT 1").fetchone()
    insert = (
        "INSERT INTO relationship(id, created_at, updated_at, relation_type, origin, "
        "lifecycle_status, from_concept_id, to_knowledge_id) "
        "VALUES ('REL-99999999', ?, ?, 'HAS_PROPERTY', 'EXPLICIT', 'ACTIVE', ?, ?)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(insert, (NOW, NOW, concept, knowledge))
    connection.rollback()
    _migrate_through(connection, populated_v5["db_path"], 6)
    connection.execute(insert, (NOW, NOW, concept, knowledge))
    connection.rollback()


def test_the_rebuilt_table_keeps_its_rules(populated_v5):
    """D-24's unique index, the shape triggers and the origin trigger all still fire."""
    connection = populated_v5["connection"]
    _migrate_through(connection, populated_v5["db_path"], 6)
    edge = dict(connection.execute(
        "SELECT * FROM relationship WHERE relation_type = 'DEFINED_BY' LIMIT 1").fetchone())
    columns = ", ".join(edge)
    marks = ", ".join("?" for _ in edge)
    twin = dict(edge, id="REL-99999999")
    with pytest.raises(sqlite3.IntegrityError):  # D-24: one ACTIVE edge per endpoints and type
        connection.execute(f"INSERT INTO relationship({columns}) VALUES ({marks})", tuple(twin.values()))
    connection.rollback()
    with pytest.raises(sqlite3.IntegrityError):  # exactly one to_* endpoint
        connection.execute("UPDATE relationship SET to_concept_id = from_concept_id WHERE id = ?", (edge["id"],))
    connection.rollback()
    with pytest.raises(sqlite3.IntegrityError):  # EXPLICIT never downgrades
        connection.execute("UPDATE relationship SET origin = 'INFERRED' WHERE id = ?", (edge["id"],))
    connection.rollback()


def test_a_failure_at_the_last_statement_of_0006_leaves_version_5_intact(populated_v5):
    connection, db_path = populated_v5["connection"], populated_v5["db_path"]
    before_rows, before_objects = _rows(connection), _objects(connection)
    m6 = next(m for m in _REAL_MIGRATIONS if m.version == 6)
    broken = migrator.Migration(version=6, name=m6.name, sql=m6.sql + "\nCREATE TABLE ;\n")

    with mock.patch.object(migrator, "available_migrations", lambda: _migrations_through(5) + (broken,)), \
         mock.patch.object(migrator, "CODE_SCHEMA_VERSION", 6):
        with pytest.raises(StorageError) as caught:
            migrate(connection, database_path=db_path)
    assert caught.value.report.data_changed is False

    assert schema_version(connection) == 5
    assert _objects(connection) == before_objects  # the old relationship table is back
    assert _rows(connection) == before_rows
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # restored
    report = _migrate_through(connection, db_path, 6)
    assert report.version_after == 6
    _verify(connection)


def test_a_foreign_key_violation_rolls_the_migration_back(populated_v5):
    """Enforcement is off while a migration runs; foreign_key_check is the guard."""
    connection, db_path = populated_v5["connection"], populated_v5["db_path"]
    before_rows = _rows(connection)
    m6 = next(m for m in _REAL_MIGRATIONS if m.version == 6)
    dangling = (
        "\nINSERT INTO relationship_inference(id, created_at, updated_at, relationship_id, rule, "
        f"rule_version, basis_occurrence_id) SELECT 'RI-99999999', '{NOW}', '{NOW}', "
        "'REL-99999999', 'R1', '1', basis_occurrence_id FROM relationship_inference LIMIT 1;\n"
    )
    broken = migrator.Migration(version=6, name=m6.name, sql=m6.sql + dangling)
    with mock.patch.object(migrator, "available_migrations", lambda: _migrations_through(5) + (broken,)), \
         mock.patch.object(migrator, "CODE_SCHEMA_VERSION", 6):
        with pytest.raises(StorageError) as caught:
            migrate(connection, database_path=db_path)
    assert "foreign key violation" in caught.value.report.reason
    assert schema_version(connection) == 5
    assert _rows(connection) == before_rows
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_identifier_counters_converge_on_every_path_to_6(tmp_path: Path):
    results = {}
    for start in (0, 1, 2, 3, 4, 5):
        path = tmp_path / f"from{start}" / "database" / "knowledge.db"
        path.parent.mkdir(parents=True)
        connection = connect(path) if start == 0 else _build_at(path, start)
        try:
            _migrate_through(connection, path, 6)
            results[start] = _counters(connection)
        finally:
            connection.close()
    assert all(counters == results[0] for counters in results.values())


# ------------------------------------------------------- the new tables' rules


@pytest.fixture
def v6(populated_v5):
    connection = populated_v5["connection"]
    _migrate_through(connection, populated_v5["db_path"], 6)
    k1, k2 = [r[0] for r in connection.execute("SELECT id FROM knowledge_object ORDER BY id LIMIT 2")]
    c1, c2 = [r[0] for r in connection.execute("SELECT id FROM concept ORDER BY id LIMIT 2")]
    occurrence = connection.execute("SELECT id FROM source_occurrence LIMIT 1").fetchone()[0]
    return {"connection": connection, "k": (k1, k2), "c": (c1, c2), "occurrence": occurrence}


def _assessment(connection, **values):
    row = {"id": "KE-00000001", "created_at": NOW, "updated_at": NOW,
           "outcome": "POSSIBLE_DUPLICATE", "rule": "P8-13", "rule_version": "1"}
    row.update(values)
    names = ", ".join(row)
    connection.execute(f"INSERT INTO knowledge_equivalence({names}) VALUES "
                       f"({', '.join('?' for _ in row)})", tuple(row.values()))


def test_an_assessment_has_exactly_one_other_side(v6):
    connection, (k1, k2) = v6["connection"], v6["k"]
    with pytest.raises(sqlite3.IntegrityError):
        _assessment(connection, canonical_knowledge_id=k1)
    with pytest.raises(sqlite3.IntegrityError):
        _assessment(connection, canonical_knowledge_id=k1, other_knowledge_id=k2,
                    linked_occurrence_id=v6["occurrence"], outcome="EXACT_DUPLICATE")
    _assessment(connection, canonical_knowledge_id=k1, other_knowledge_id=k2)
    with pytest.raises(sqlite3.IntegrityError):  # one outcome per comparison and rule version
        _assessment(connection, id="KE-00000002", canonical_knowledge_id=k1, other_knowledge_id=k2)
    connection.rollback()


def test_a_contradiction_carries_its_conflict_and_nothing_else_does(v6):
    connection, (k1, k2) = v6["connection"], v6["k"]
    with pytest.raises(sqlite3.IntegrityError):
        _assessment(connection, canonical_knowledge_id=k1, other_knowledge_id=k2,
                    outcome="CONTRADICTORY", rule="C1")
    with pytest.raises(sqlite3.IntegrityError):  # a linked occurrence is only ever exact
        _assessment(connection, canonical_knowledge_id=k1, linked_occurrence_id=v6["occurrence"])
    with pytest.raises(sqlite3.IntegrityError):  # vocabulary
        _assessment(connection, canonical_knowledge_id=k1, other_knowledge_id=k2, outcome="SIMILAR")
    with pytest.raises(sqlite3.IntegrityError):  # an object is not compared with itself
        _assessment(connection, canonical_knowledge_id=k1, other_knowledge_id=k1)
    connection.rollback()


def test_a_concept_pair_is_stored_once_in_canonical_order_with_its_evidence(v6):
    connection, (c1, c2) = v6["connection"], v6["c"]
    insert = ("INSERT INTO concept_equivalence(id, created_at, updated_at, concept_a_id, "
              "concept_b_id, status, basis, shared_name, rule, rule_version) "
              "VALUES (?, ?, ?, ?, ?, 'POSSIBLE_EQUIVALENT', ?, ?, 'P8-16', '1')")
    with pytest.raises(sqlite3.IntegrityError):  # reversed order
        connection.execute(insert, ("CE-00000001", NOW, NOW, c2, c1, "SHARED_NAME_SAME_DOCUMENT", "x"))
    with pytest.raises(sqlite3.IntegrityError):  # a shared-name basis without its name
        connection.execute(insert, ("CE-00000001", NOW, NOW, c1, c2, "SHARED_NAME_SAME_DOCUMENT", None))
    with pytest.raises(sqlite3.IntegrityError):  # a stated basis without its edge
        connection.execute(insert, ("CE-00000001", NOW, NOW, c1, c2, "STATED_EQUIVALENT_TO", None))
    connection.execute(insert, ("CE-00000001", NOW, NOW, c1, c2, "SHARED_NAME_SAME_DOCUMENT", "x"))
    with pytest.raises(sqlite3.IntegrityError):  # one record per pair
        connection.execute(insert, ("CE-00000002", NOW, NOW, c1, c2, "SHARED_NAME_OTHER_DOCUMENT", "x"))
    connection.rollback()


def test_the_new_tables_protect_what_they_name(v6):
    connection, (k1, k2) = v6["connection"], v6["k"]
    _assessment(connection, canonical_knowledge_id=k1, other_knowledge_id=k2)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM knowledge_object WHERE id = ?", (k2,))
    connection.rollback()
