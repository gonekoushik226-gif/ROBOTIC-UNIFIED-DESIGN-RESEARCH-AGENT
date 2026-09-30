"""Identifier allocation (ADR 0006).

The counter is incremented inside the **caller's** transaction::

    UPDATE id_sequence SET next_value = next_value + 1
     WHERE entity_kind = ? RETURNING next_value - 1

Two consequences follow from that single statement:

* If the caller's transaction rolls back, the counter rolls back with it, so an
  abandoned operation leaves no gap.
* Two allocations can never collide, because the `UPDATE` holds the row.

The `PRIMARY KEY` on each table is the real uniqueness guarantee; this allocator
is only what hands out the next number.

Identifiers are never reused. The counter does not move backwards, so a hard
delete does leave a gap - and a gap means nothing (ADR 0006).
"""

import sqlite3

from app.core.errors import StorageError
from app.models.identifiers import EntityKind, format_id


class IdAllocator:
    """Hands out canonical identifiers from the database's per-kind counters."""

    __slots__ = ("_connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def next_id(self, kind: EntityKind) -> str:
        """Allocate the next identifier for `kind`, in canonical form."""
        try:
            row = self._connection.execute(
                "UPDATE id_sequence SET next_value = next_value + 1 "
                "WHERE entity_kind = ? RETURNING next_value - 1",
                (kind.value,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError.of(
                "RUDRA could not allocate an identifier.",
                f"{type(exc).__name__}: {exc}",
                stage="storage.ids.allocate",
                data_changed=False,
                retry_safe=True,
                cause=repr(exc),
                next_options=(
                    "Run 'python -m app db' to check the schema version.",
                    "The database may need migrating.",
                ),
            ) from exc

        if row is None:
            raise StorageError.of(
                "RUDRA has no identifier counter for that entity kind.",
                f"No row in id_sequence for {kind.value!r}.",
                stage="storage.ids.allocate",
                missing=(f"id_sequence row for {kind.value}",),
                available=tuple(sorted(self.counters())),
                data_changed=False,
                retry_safe=False,
                next_options=(
                    "Migrate the database so the counter is created.",
                    "Run 'python -m app db' to see the current state.",
                ),
            )
        return format_id(kind, int(row[0]))

    def peek(self, kind: EntityKind) -> int:
        """The number the next allocation will use, without consuming it."""
        row = self._connection.execute(
            "SELECT next_value FROM id_sequence WHERE entity_kind = ?", (kind.value,)
        ).fetchone()
        if row is None:
            raise StorageError.of(
                "RUDRA has no identifier counter for that entity kind.",
                f"No row in id_sequence for {kind.value!r}.",
                stage="storage.ids.peek",
                data_changed=False,
                retry_safe=False,
                next_options=("Migrate the database so the counter is created.",),
            )
        return int(row[0])

    def counters(self) -> dict[str, int]:
        """Every counter, for reporting."""
        rows = self._connection.execute(
            "SELECT entity_kind, next_value FROM id_sequence ORDER BY entity_kind"
        ).fetchall()
        return {row["entity_kind"]: int(row["next_value"]) for row in rows}
