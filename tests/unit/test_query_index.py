"""Phase 9 step 9: the derived FTS5 index - explicit build and read-back verification.

ADR 0037 P9-25 ... P9-30 and ADR 0004 Amendment 1: `index.db` is derived and
rebuildable, built only explicitly from `knowledge.db` read-only, holds identifiers and
tokens but never the text, and is used only when its build marker equals the state
marker computed from `knowledge.db` now. Every database here is a scratch one.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from collections import namedtuple
from contextlib import closing
from dataclasses import replace

import pytest

from app.core.errors import ResourceLimitError, StorageError
from app.models import DocumentProcessingStatus, ExtractionRunStatus, KnowledgeType, LifecycleStatus
from app.models.naming import NORMALIZATION_VERSION
from app.query import IndexState, build_index, verify_index
from app.query.index import document_ingestion
from app.storage import connect
from app.storage import keyword_index as store
from tests.unit.query_rows import QueryRows


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _knowledge_bytes(db_path) -> str:
    """The database file and its WAL together: what a writer would change."""
    wal = db_path.with_name(db_path.name + "-wal")
    return _sha256(db_path) + (_sha256(wal) if wal.exists() else "")


@pytest.fixture
def library(repo, db_path, tmp_path):
    """A small committed store, a read-only connection to it, and an index path."""
    rows = QueryRows(repo)
    book = rows.document("book")
    source = rows.source(book)
    concept = rows.concept("MOSFET")
    rows.concept_occurrence(concept, source, page=1)
    definition = rows.knowledge(KnowledgeType.DEFINITION, "A MOSFET is a voltage-controlled transistor.")
    rows.knowledge_occurrence(definition, source, page=1)
    rows.segment(book, 1, text="Page one is about MOSFET devices.")
    rows.segment(book, 2, text="   ")  # nothing searchable: skipped, not indexed
    rows.commit()
    (tmp_path / "indexes").mkdir()
    reader = connect(db_path, read_only=True)
    names = namedtuple("Library", "rows reader index definition concept")(
        rows, reader, tmp_path / "indexes" / "index.db", definition, concept,
    )
    yield names
    reader.close()


def _read(index_path):
    with closing(connect(index_path, read_only=True)) as connection:
        return (store.read_metadata(connection), store.entry_rows(connection),
                store.page_rows(connection), store.stored_counts(connection))


# ------------------------------------------------------------------ the build


def test_the_build_writes_the_index_tables_and_its_metadata(library):
    report = build_index(library.reader, library.index)

    assert report.status.state is IndexState.FRESH and library.index.is_file()
    metadata, entries, pages, counts = _read(library.index)
    assert metadata["format_version"] == str(store.FORMAT_VERSION)
    assert metadata["tokenizer"] == "unicode61 remove_diacritics 0"
    assert metadata["normalization_version"] == str(NORMALIZATION_VERSION)
    assert metadata["build_marker"] == report.status.build_marker == report.status.state_marker
    assert dict(report.status.entries) == {"CONCEPT_ALIAS": 1, "KNOWLEDGE_OBJECT": 1, "SOURCE_OCCURRENCE": 1}
    assert [kind for _, kind, _ in entries] == ["KNOWLEDGE_OBJECT", "SOURCE_OCCURRENCE", "CONCEPT_ALIAS"]
    assert entries[0][2] == library.definition.id
    assert report.status.pages == 1 and len(pages) == 1 and report.skipped_empty == 1
    assert counts["entry_text"] == 3 and counts["page_text"] == 1
    assert report.size_bytes == library.index.stat().st_size > 0
    assert report.estimate_bytes > 0 and report.available_bytes >= report.estimate_bytes


def test_the_index_holds_identifiers_and_tokens_never_the_text(library):
    build_index(library.reader, library.index)
    with closing(sqlite3.connect(library.index)) as raw:
        bodies = raw.execute("SELECT body FROM entry_text").fetchall()
        definitions = dict(raw.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN ('entry_text', 'page_text')"))
    assert bodies and all(body is None for (body,) in bodies)  # contentless (P9-25)
    assert definitions == store.FTS_DEFINITIONS
    assert b"voltage-controlled transistor" not in library.index.read_bytes()


def test_a_build_is_deterministic(library, db_path, tmp_path):
    build_index(library.reader, library.index)
    again = tmp_path / "indexes" / "again.db"
    build_index(library.reader, again)
    library.rows.repo.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    copy = tmp_path / "copy" / "knowledge.db"
    copy.parent.mkdir()
    shutil.copyfile(db_path, copy)
    with closing(connect(copy, read_only=True)) as twin:
        from_copy = tmp_path / "indexes" / "copy.db"
        build_index(twin, from_copy)

    first = _read(library.index)
    assert _read(again) == first == _read(from_copy)  # metadata, entries, pages, counts


def test_the_build_writes_nothing_to_knowledge_db(library, db_path):
    connection = library.rows.repo.connection
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = _knowledge_bytes(db_path)
    build_index(library.reader, library.index)
    assert _knowledge_bytes(db_path) == before
    assert library.reader.total_changes == 0


def test_the_build_refuses_a_schema_it_cannot_read(tmp_path):
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    with closing(connect(empty, read_only=True)) as reader, pytest.raises(StorageError):
        build_index(reader, tmp_path / "index.db")
    assert not (tmp_path / "index.db").exists()


def test_the_build_refuses_before_writing_when_the_disk_lacks_space(library):
    Usage = namedtuple("Usage", "total used free")
    with pytest.raises(ResourceLimitError) as caught:
        build_index(library.reader, library.index, disk_usage=lambda _: Usage(1, 1, 0))
    assert caught.value.report.data_changed is False
    assert "estimate=" in caught.value.report.detail
    assert list(library.index.parent.iterdir()) == []  # nothing written


def test_a_failed_build_leaves_the_previous_index_and_no_temporary_file(library, monkeypatch):
    build_index(library.reader, library.index)
    before = _sha256(library.index)
    library.rows.knowledge(KnowledgeType.EXAMPLE, "A MOSFET switches a lamp.")
    library.rows.commit()

    def broken(connection, rows):
        raise sqlite3.OperationalError("disk I/O error (simulated)")

    monkeypatch.setattr(store, "insert_pages", broken)
    with pytest.raises(sqlite3.OperationalError):
        build_index(library.reader, library.index)
    assert _sha256(library.index) == before
    assert sorted(p.name for p in library.index.parent.iterdir()) == ["index.db"]
    assert verify_index(library.reader, library.index).state is IndexState.STALE  # never repaired


@pytest.mark.parametrize(
    ("term", "prefix", "expression"),
    [
        ("mosfet", False, '"mosfet"'),
        ("mosfet", True, '"mosfet"*'),
        ("gate-source voltage", False, '"gate-source voltage"'),
        ('a" or "b', False, '"a"" or ""b"'),
        ("not mosfet", True, '"not mosfet"*'),
    ],
)
def test_every_term_is_one_quoted_fts5_string(term, prefix, expression):
    assert store.match_expression(term, prefix=prefix) == expression


# ----------------------------------------------------------- read-back states


def test_a_fresh_index_verifies_and_a_missing_one_is_missing(library):
    missing = verify_index(library.reader, library.index)
    assert missing.state is IndexState.MISSING and not library.index.exists()
    assert verify_index(library.reader, None).state is IndexState.MISSING
    build_index(library.reader, library.index)
    fresh = verify_index(library.reader, library.index)
    assert fresh.state is IndexState.FRESH
    assert fresh.build_marker == fresh.state_marker


@pytest.mark.parametrize("change", ["insert", "update"])
def test_a_later_write_to_a_covered_table_makes_the_index_stale(library, change):
    build_index(library.reader, library.index)
    before = _sha256(library.index)
    if change == "insert":
        library.rows.knowledge(KnowledgeType.EXAMPLE, "A MOSFET switches a lamp.")
        table = "knowledge_object"
    else:
        library.rows.repo.update(replace(library.concept, lifecycle_status=LifecycleStatus.DEPRECATED))
        library.rows.repo.update(replace(library.definition, canonical_name="MOSFET definition"))
        table = "knowledge_object"
    library.rows.commit()

    status = verify_index(library.reader, library.index)
    assert status.state is IndexState.STALE
    assert f"{table} changed" in status.reason and status.build_marker != status.state_marker
    assert _sha256(library.index) == before  # verification never repairs


def test_an_index_of_another_knowledge_database_is_stale(library, tmp_path):
    build_index(library.reader, library.index)
    other = tmp_path / "other" / "knowledge.db"
    other.parent.mkdir()
    (other.parent / "backups").mkdir()
    from app.storage import migrate

    with closing(connect(other)) as writer:
        migrate(writer, database_path=other)
        writer.commit()
    with closing(connect(other, read_only=True)) as stranger:
        status = verify_index(stranger, library.index)
    assert status.state is IndexState.STALE and "another knowledge database" in status.reason


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("tokenizer", "porter unicode61"),
        ("format_version", "2"),
        ("normalization_version", "99"),
    ],
)
def test_an_index_built_another_way_is_incompatible(library, key, value):
    build_index(library.reader, library.index)
    with closing(sqlite3.connect(library.index)) as raw:
        raw.execute("UPDATE index_metadata SET value = ? WHERE key = ?", (value, key))
        raw.commit()
    status = verify_index(library.reader, library.index)
    assert status.state is IndexState.INCOMPATIBLE and "this build" in status.reason


@pytest.mark.parametrize(
    "damage",
    [
        "DELETE FROM index_metadata WHERE key = 'build_marker'",
        "DELETE FROM entry WHERE id = 1",
        "DELETE FROM index_metadata WHERE key = 'entries'",
    ],
)
def test_an_unfinished_or_damaged_index_is_incomplete(library, damage):
    build_index(library.reader, library.index)
    with closing(sqlite3.connect(library.index)) as raw:
        raw.execute(damage)
        raw.commit()
    assert verify_index(library.reader, library.index).state is IndexState.INCOMPLETE


def test_a_file_that_is_not_an_index_is_unreadable(library):
    library.index.write_bytes(b"this is not an SQLite database, " * 64)
    assert verify_index(library.reader, library.index).state is IndexState.UNREADABLE
    library.index.unlink()
    with closing(sqlite3.connect(library.index)) as raw:
        raw.execute("CREATE TABLE unrelated (x)")
    assert verify_index(library.reader, library.index).state is IndexState.UNREADABLE


def test_a_rebuild_replaces_a_stale_index_and_the_old_readers_sidecars(library):
    build_index(library.reader, library.index)
    with closing(connect(library.index, read_only=True)) as reader:
        store.read_metadata(reader)  # a WAL reader may leave -wal/-shm behind
    library.rows.knowledge(KnowledgeType.EXAMPLE, "A MOSFET switches a lamp.")
    library.rows.commit()
    assert verify_index(library.reader, library.index).state is IndexState.STALE

    report = build_index(library.reader, library.index)
    assert report.status.state is IndexState.FRESH
    assert verify_index(library.reader, library.index).state is IndexState.FRESH
    assert dict(report.status.entries)["KNOWLEDGE_OBJECT"] == 2


# ------------------------------------------------ "fully ingested" (P8-6, P9-10)


def test_fully_ingested_needs_parsing_a_completed_v4_run_and_a_fresh_index(library, db_path):
    """Reported, never stored; each missing condition is stated separately."""
    rows = library.rows
    complete, partial, old, failed = (rows.document(name) for name in ("complete", "partial", "old", "failed"))
    run = rows.run(complete)
    rows.run(partial, status=ExtractionRunStatus.PARTIAL)
    rows.run(old, version="3")
    failed_run = rows.run(failed)
    rows.repo.update(replace(failed, processing_status=DocumentProcessingStatus.FAILED))
    rows.commit()
    before = _knowledge_bytes(db_path)

    missing = document_ingestion(library.reader, verify_index(library.reader, library.index))
    assert not any(item.fully_ingested for item in missing)
    assert all("the derived index is MISSING" in item.reason for item in missing)

    by_id = {item.document_id: item for item in
             document_ingestion(library.reader, build_index(library.reader, library.index).status)}
    assert list(by_id) == ["DOC-00000001", complete.id, partial.id, old.id, failed.id]  # counter order
    assert by_id[complete.id].fully_ingested and by_id[complete.id].reason == "fully ingested"
    assert by_id[complete.id].qualifying_runs == (run.id,)
    no_run = "not fully ingested: no COMPLETED extraction run at extractor version 4 or later"
    for document_id in ("DOC-00000001", partial.id, old.id):  # none, PARTIAL, extractor v3
        assert by_id[document_id].reason == no_run
    assert by_id[failed.id].parsed is False and by_id[failed.id].qualifying_runs == (failed_run.id,)
    assert by_id[failed.id].reason == "not fully ingested: not parsed (processing status FAILED)"
    assert _knowledge_bytes(db_path) == before  # reads only
