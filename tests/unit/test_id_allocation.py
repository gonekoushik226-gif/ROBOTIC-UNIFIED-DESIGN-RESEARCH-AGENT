"""Identifier allocation and its transaction behaviour (ADR 0006).

The allocator shares the caller's transaction on purpose, which is what makes
"a rollback leaves no gap" true. That is asserted here rather than assumed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import StorageError
from app.models.identifiers import EntityKind, parse_id
from app.storage import IdAllocator, connect, migrate


def test_allocation_starts_at_one(connection):
    allocator = IdAllocator(connection)
    assert allocator.next_id(EntityKind.KNOWLEDGE_OBJECT) == "K-00000001"
    assert allocator.next_id(EntityKind.KNOWLEDGE_OBJECT) == "K-00000002"


def test_counters_are_independent_per_kind(connection):
    allocator = IdAllocator(connection)
    assert allocator.next_id(EntityKind.KNOWLEDGE_OBJECT) == "K-00000001"
    assert allocator.next_id(EntityKind.SOURCE) == "SRC-00000001"
    assert allocator.next_id(EntityKind.KNOWLEDGE_OBJECT) == "K-00000002"


def test_every_entity_kind_has_a_counter(connection):
    counters = IdAllocator(connection).counters()
    assert set(counters) == {kind.value for kind in EntityKind}
    assert all(value == 1 for value in counters.values())


def test_many_allocations_are_unique_and_monotonic(connection):
    allocator = IdAllocator(connection)
    issued = [allocator.next_id(EntityKind.KNOWLEDGE_OBJECT) for _ in range(500)]
    assert len(set(issued)) == 500
    assert issued == sorted(issued)
    assert [parse_id(i)[1] for i in issued] == list(range(1, 501))


def test_peek_does_not_consume(connection):
    allocator = IdAllocator(connection)
    assert allocator.peek(EntityKind.CONCEPT) == 1
    assert allocator.peek(EntityKind.CONCEPT) == 1
    assert allocator.next_id(EntityKind.CONCEPT) == "CPT-00000001"
    assert allocator.peek(EntityKind.CONCEPT) == 2


def test_rollback_leaves_no_gap(connection):
    """The counter shares the caller's transaction, so an abandoned operation
    does not burn an identifier."""
    allocator = IdAllocator(connection)
    allocator.next_id(EntityKind.QUERY)
    connection.commit()

    before = allocator.peek(EntityKind.QUERY)
    allocator.next_id(EntityKind.QUERY)
    connection.rollback()

    assert allocator.peek(EntityKind.QUERY) == before
    assert allocator.next_id(EntityKind.QUERY) == f"Q-{before:08d}"


def test_commit_keeps_the_allocation(connection):
    allocator = IdAllocator(connection)
    first = allocator.next_id(EntityKind.QUERY)
    connection.commit()
    second = allocator.next_id(EntityKind.QUERY)
    assert first != second
    assert parse_id(second)[1] == parse_id(first)[1] + 1


def test_identifiers_survive_reopening_the_database(db_path: Path):
    """The counter is in the database, not in memory."""
    first = connect(db_path)
    try:
        migrate(first, database_path=db_path)
        IdAllocator(first).next_id(EntityKind.KNOWLEDGE_OBJECT)
        IdAllocator(first).next_id(EntityKind.KNOWLEDGE_OBJECT)
        first.commit()
    finally:
        first.close()

    second = connect(db_path)
    try:
        assert IdAllocator(second).next_id(EntityKind.KNOWLEDGE_OBJECT) == "K-00000003"
    finally:
        second.close()


def test_identifiers_are_never_reused_after_deletion(connection, repo):
    """A gap means nothing; it must never be refilled with a fresh entity."""
    from app.models import Concept, LifecycleStatus
    from app.models.base import utc_now

    now = utc_now()
    first = repo.add(
        Concept(
            id=repo.new_id(Concept),
            created_at=now,
            updated_at=now,
            canonical_name="Temporary",
            lifecycle_status=LifecycleStatus.ACTIVE,
        )
    )
    connection.commit()
    connection.execute("DELETE FROM concept WHERE id = ?", (first.id,))
    connection.commit()

    second = repo.new_id(Concept)
    assert second != first.id
    assert parse_id(second)[1] == parse_id(first.id)[1] + 1


def test_allocation_before_migration_reports_actionably(db_path: Path):
    connection = connect(db_path)
    try:
        with pytest.raises(StorageError) as caught:
            IdAllocator(connection).next_id(EntityKind.KNOWLEDGE_OBJECT)
        report = caught.value.report
        assert report.stage == "storage.ids.allocate"
        assert report.data_changed is False
        assert report.next_options
    finally:
        connection.close()


def test_a_missing_counter_row_is_reported_not_guessed(connection):
    connection.execute("DELETE FROM id_sequence WHERE entity_kind = 'K'")
    with pytest.raises(StorageError) as caught:
        IdAllocator(connection).next_id(EntityKind.KNOWLEDGE_OBJECT)
    report = caught.value.report
    assert "no identifier counter" in report.summary
    assert report.available  # the kinds that do have counters are listed
    connection.rollback()


def test_allocated_identifiers_satisfy_the_database_check(connection, repo):
    """The format the allocator produces must satisfy the schema's own CHECK."""
    from app.models import Concept, LifecycleStatus
    from app.models.base import utc_now

    now = utc_now()
    for _ in range(3):
        repo.add(
            Concept(
                id=repo.new_id(Concept),
                created_at=now,
                updated_at=now,
                canonical_name="Concept",
                lifecycle_status=LifecycleStatus.ACTIVE,
            )
        )
    connection.commit()
    assert repo.count(Concept) == 3


def test_database_rejects_a_badly_formed_identifier(connection):
    """The schema enforces the identifier shape independently of Python."""
    import sqlite3

    now = "2026-09-20T04:46:14.030Z"
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO concept(id, created_at, updated_at, canonical_name, "
            "lifecycle_status) VALUES ('CPT-123', ?, ?, 'x', 'ACTIVE')",
            (now, now),
        )
    connection.rollback()
