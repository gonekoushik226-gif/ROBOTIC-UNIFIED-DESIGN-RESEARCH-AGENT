"""Phase 11 Batch A: the read-only query for one admitted stored equation (ADR 0043 P11-27).

`equations_of_knowledge` returns the stored `equation` row(s) of one knowledge object,
exactly as stored and by numeric identifier counter, so a caller can refuse zero or
several rather than choose one. It reads only. Every test works in its own freshly
migrated temporary database (`tests/conftest.py`); the live database is never opened.
"""

from __future__ import annotations

from app.models import Equation, KnowledgeType, LifecycleStatus
from app.models.identifiers import EntityKind, format_id
from app.storage import connect, queries
from tests.unit.query_rows import QueryRows


def _equation(rows: QueryRows, expression: str, *, identifier: str | None = None,
              knowledge_id: str | None = None, status: LifecycleStatus = LifecycleStatus.ACTIVE) -> Equation:
    if knowledge_id is None:
        knowledge_id = rows.knowledge(KnowledgeType.EQUATION, expression).id
    return rows._add(Equation, identifier, expression=expression, lifecycle_status=status,
                     knowledge_id=knowledge_id)


def test_the_equation_of_a_knowledge_object_is_returned_as_stored(repo):
    rows = QueryRows(repo)
    stored = _equation(rows, "I = V / Rtotal")
    _equation(rows, "Rtotal = R1 + R2")  # another object's equation is not returned
    assert queries.equations_of_knowledge(repo.connection, stored.knowledge_id) == (stored,)


def test_nothing_is_returned_for_an_object_without_an_equation_or_an_unknown_one(repo):
    rows = QueryRows(repo)
    definition = rows.knowledge(KnowledgeType.DEFINITION, "Resistance is opposition to current.")
    assert queries.equations_of_knowledge(repo.connection, definition.id) == ()
    assert queries.equations_of_knowledge(repo.connection, "K-99999999") == ()


def test_every_row_is_returned_so_that_several_are_never_chosen_between(repo):
    rows = QueryRows(repo)
    owner = rows.knowledge(KnowledgeType.EQUATION, "P = V * I")
    # Counters 10 and 9: the order is by numeric counter, never by identifier string.
    later = _equation(rows, "P = V * I", identifier=format_id(EntityKind.EQUATION, 10), knowledge_id=owner.id)
    earlier = _equation(rows, "P = I^2 * R", identifier=format_id(EntityKind.EQUATION, 9), knowledge_id=owner.id)
    assert queries.equations_of_knowledge(repo.connection, owner.id) == (earlier, later)


def test_the_lifecycle_is_returned_as_stored_for_the_caller_to_judge(repo):
    rows = QueryRows(repo)
    archived = _equation(rows, "V = I * R", status=LifecycleStatus.ARCHIVED)
    (found,) = queries.equations_of_knowledge(repo.connection, archived.knowledge_id)
    assert found.lifecycle_status is LifecycleStatus.ARCHIVED


def test_the_query_reads_only_and_works_on_a_read_only_connection(repo, db_path):
    rows = QueryRows(repo)
    stored = _equation(rows, "I = V / Rtotal")
    rows.commit()
    before = db_path.read_bytes()
    reader = connect(db_path, read_only=True)
    try:
        assert queries.equations_of_knowledge(reader, stored.knowledge_id) == (stored,)
    finally:
        reader.close()
    assert db_path.read_bytes() == before
