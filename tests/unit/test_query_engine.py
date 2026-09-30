"""Phase 9 step 8: the engine facade, the answer trace and deterministic output.

ADR 0036 P9-19 (the trace is returned, never persisted), P9-22 (deterministic order;
no score), ADR 0037 P9-29 (these modes never open the index), P9-31 (read-only, never
migrating, a schema mismatch refused).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

from app.core.errors import StorageError
from app.models import RelationshipOrigin, RelationType
from app.query import AnswerStatus, QueryEngine, QueryRequest, as_plain, to_json
from app.query.engine import INDEX_NOT_USED
from app.query.results import EdgeEnd, TraceLink
from app.storage import CODE_SCHEMA_VERSION, connect
from tests.unit.query_rows import QueryRows, mosfet_library


def _table_counts(connection) -> dict[str, int]:
    tables = [r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    return {t: connection.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}


@pytest.fixture
def library(repo, db_path):
    """The shared library, committed, and a read-only connection to it."""
    names = mosfet_library(QueryRows(repo))
    reader = connect(db_path, read_only=True)
    yield names, reader
    reader.close()


def test_the_trace_records_request_database_links_evidence_and_withholding(library, db_path):
    n, reader = library
    request = QueryRequest.concept("MOSFET")
    result = QueryEngine(reader, database_path=db_path).run(request)
    trace = result.trace

    assert trace.request == request
    assert (trace.database.path, trace.database.schema_version, trace.database.read_only) == (
        str(db_path), CODE_SCHEMA_VERSION, True,
    )
    assert trace.index == INDEX_NOT_USED and trace.persisted is False
    assert [(t.concept_id, t.normalized_alias) for t in trace.resolved] == [
        (n.mosfet_a.id, "mosfet"), (n.mosfet_b.id, "mosfet"),
    ]
    assert TraceLink(
        item_id=n.definition.id, concept_id=n.mosfet_a.id, relationship_id=n.defined_a.id,
        relation_type=RelationType.DEFINED_BY, origin=RelationshipOrigin.EXPLICIT,
        concept_end=EdgeEnd.FROM,
    ) in trace.links
    assert {n.def_a.id, n.def_b.id, n.bjt_def_a.id} <= set(trace.evidence_ids)
    rows = {row: trace.evidence_ids.index(row) for row in trace.evidence_ids}
    assert len(rows) == len(trace.evidence_ids)  # each once
    assert trace.widened == (n.mos.id,)
    assert trace.withheld == result.withheld and trace.withheld.unauthorized_evidence > 0


def test_the_engine_writes_nothing_and_persists_no_trace(repo, db_path):
    mosfet_library(QueryRows(repo))
    before, changes = _table_counts(repo.connection), repo.connection.total_changes
    engine = QueryEngine(repo.connection, database_path=db_path)
    for request in (
        QueryRequest.concept("MOSFET"),
        QueryRequest.exact("K-00000001"),
        QueryRequest.page("DOC-00000001", 1),
    ):
        engine.run(request)
    assert repo.connection.total_changes == changes
    assert _table_counts(repo.connection) == before  # no query, audit or trace row
    assert engine.run(QueryRequest.concept("MOSFET")).trace.database.read_only is False


def test_equal_queries_give_byte_identical_output(library, db_path):
    n, reader = library
    first = to_json(QueryEngine(reader, database_path=db_path).run(QueryRequest.concept("MOSFET")))
    again = to_json(QueryEngine(reader, database_path=db_path).run(QueryRequest.concept("MOSFET")))
    other = connect(db_path, read_only=True)
    try:
        fresh = to_json(QueryEngine(other, database_path=db_path).run(QueryRequest.concept("MOSFET")))
    finally:
        other.close()
    assert first == again == fresh
    plain = json.loads(first)
    assert plain["status"] == "FOUND" and plain["trace"]["persisted"] is False
    assert "score" not in first and "rank" not in first


def test_as_plain_gives_json_values_and_stored_names(library, db_path):
    n, reader = library
    plain = as_plain(QueryEngine(reader, database_path=db_path).run(QueryRequest.exact(n.definition.id)))

    assert plain["exact"]["knowledge"]["knowledge"]["statement"] == n.definition.statement
    assert plain["exact"]["knowledge"]["knowledge"]["knowledge_type"] == "DEFINITION"
    json.dumps(plain)  # nothing left that JSON cannot hold


def test_every_mode_is_dispatched(library, db_path):
    n, reader = library
    engine = QueryEngine(reader, database_path=db_path)
    concept = engine.run(QueryRequest.concept("MOSFET"))
    exact = engine.run(QueryRequest.exact(n.uses.id))
    page = engine.run(QueryRequest.page(n.book_a.id, 1))

    assert (concept.concept is not None, concept.exact, concept.page) == (True, None, None)
    assert (exact.concept, exact.exact is not None, exact.page) == (None, True, None)
    assert (page.concept, page.exact, page.page is not None) == (None, None, True)
    assert {concept.status, exact.status, page.status} == {AnswerStatus.FOUND}
    assert exact.trace.resolved == () and page.trace.index == INDEX_NOT_USED


def test_a_database_this_build_cannot_read_is_refused_and_not_migrated(tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    reader = connect(path, read_only=True)
    try:
        with pytest.raises(StorageError) as caught:
            QueryEngine(reader, database_path=path).run(QueryRequest.concept("MOSFET"))
    finally:
        reader.close()
    assert caught.value.report.data_changed is False
    with closing(sqlite3.connect(path)) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 0
