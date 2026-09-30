"""Create, retrieve and update entities (Part 5 section 185).

The repository is the only place that writes entity rows, which gives one place
to enforce the rule that matters: **nothing invalid is ever persisted**. Every
`add` and `update` calls the model's `validate()` first and refuses on `Err`.

Validation therefore happens twice, deliberately:

1. Here, in Python, producing a report that lists every problem at once.
2. In the database, through `STRICT`, `NOT NULL`, `CHECK` and `FOREIGN KEY`.

The second is the one that cannot be bypassed, because it applies to any writer.
The first is the one that explains itself to a person.

Part 7 note: this class offers no `delete`. Removing knowledge is a consequential,
dependency-aware operation (Part 7 sections 22-23) and deleting a *source file* is
a different operation again (Part 7 section 10). Neither belongs in Phase 2, and
neither should arrive as a convenience method on a generic repository.
"""

import sqlite3
from typing import TypeVar

from app.core.errors import InvalidInputError, RudraError, StorageError
from app.core.result import Err
from app.models.base import utc_now
from app.storage.ids import IdAllocator
from app.storage.mapping import columns, from_row, to_row

E = TypeVar("E")


class Repository:
    """Persistence for the Phase 2 entities.

    Holds a connection but never opens one: connections come from
    `app.storage.connection.connect`, which is what guarantees `foreign_keys=ON`.
    """

    __slots__ = ("_connection", "_ids")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._ids = IdAllocator(connection)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    @property
    def ids(self) -> IdAllocator:
        return self._ids

    def new_id(self, entity_type: type) -> str:
        """Allocate an identifier of the kind `entity_type` uses."""
        return self._ids.next_id(entity_type.KIND)  # type: ignore[attr-defined]

    def add(self, entity: E) -> E:
        """Validate and insert. Returns the entity that was stored."""
        self._require_valid(entity)
        names = columns(type(entity))
        row = to_row(entity)
        placeholders = ", ".join(f":{name}" for name in names)
        sql = (
            f"INSERT INTO {type(entity).TABLE} "  # type: ignore[attr-defined]
            f"({', '.join(names)}) VALUES ({placeholders})"
        )
        self._execute(sql, row, entity=entity, operation="add")
        return entity

    def get(self, entity_type: type[E], identifier: str) -> E | None:
        """Retrieve one entity by identifier, or None when it does not exist.

        Absence is returned, not raised: "no such row" is an ordinary answer.
        """
        sql = f"SELECT * FROM {entity_type.TABLE} WHERE id = ?"  # type: ignore[attr-defined]
        try:
            row = self._connection.execute(sql, (identifier,)).fetchone()
        except sqlite3.Error as exc:
            raise self._storage_error(exc, "get", entity_type.__name__) from exc
        if row is None:
            return None
        return from_row(entity_type, row)  # type: ignore[return-value]

    def update(self, entity: E) -> E:
        """Validate and update an existing row, refreshing `updated_at`.

        Refuses if the row does not exist, rather than silently inserting it:
        an update that changes nothing must not look like a success.
        """
        from dataclasses import replace

        refreshed = replace(entity, updated_at=utc_now())  # type: ignore[type-var]
        self._require_valid(refreshed)

        names = [name for name in columns(type(refreshed)) if name != "id"]
        assignments = ", ".join(f"{name} = :{name}" for name in names)
        row = to_row(refreshed)
        sql = f"UPDATE {type(refreshed).TABLE} SET {assignments} WHERE id = :id"  # type: ignore[attr-defined]
        cursor = self._execute(sql, row, entity=refreshed, operation="update")

        if cursor.rowcount == 0:
            raise InvalidInputError.of(
                f"There is no {type(refreshed).__name__} with that identifier to update.",
                f"No row in {type(refreshed).TABLE} has id "  # type: ignore[attr-defined]
                f"{refreshed.id!r}.",  # type: ignore[attr-defined]
                stage="storage.repository.update",
                missing=(str(refreshed.id),),  # type: ignore[attr-defined]
                data_changed=False,
                retry_safe=True,
                next_options=(
                    "Check the identifier.",
                    "Use add() if the entity is new.",
                ),
            )
        return refreshed  # type: ignore[return-value]

    def count(self, entity_type: type) -> int:
        """How many rows of this entity exist."""
        sql = f"SELECT count(*) FROM {entity_type.TABLE}"  # type: ignore[attr-defined]
        return int(self._connection.execute(sql).fetchone()[0])

    def exists(self, entity_type: type, identifier: str) -> bool:
        sql = f"SELECT 1 FROM {entity_type.TABLE} WHERE id = ?"  # type: ignore[attr-defined]
        return self._connection.execute(sql, (identifier,)).fetchone() is not None

    # ------------------------------------------------------------------ helpers

    def _require_valid(self, entity: object) -> None:
        """Refuse anything the model reports as invalid."""
        outcome = entity.validate()  # type: ignore[attr-defined]
        if isinstance(outcome, Err):
            # The model already produced a report listing every problem; raising
            # it unchanged keeps that detail instead of flattening it to a string.
            raise RudraError(outcome.failure)

    def _execute(
        self, sql: str, row: dict[str, object], *, entity: object, operation: str
    ) -> sqlite3.Cursor:
        try:
            return self._connection.execute(sql, row)
        except sqlite3.IntegrityError as exc:
            raise self._integrity_error(exc, operation, entity) from exc
        except sqlite3.Error as exc:
            raise self._storage_error(exc, operation, type(entity).__name__) from exc

    def _integrity_error(
        self, exc: sqlite3.IntegrityError, operation: str, entity: object
    ) -> RudraError:
        message = str(exc)
        if "FOREIGN KEY" in message:
            reason = (
                "A referenced record does not exist, or a record this one depends "
                "on is protected from deletion."
            )
        elif "UNIQUE" in message:
            reason = "A record with the same unique value already exists."
        else:
            reason = "The database rejected the values as invalid."
        return InvalidInputError.of(
            f"The {type(entity).__name__} was not saved.",
            f"{reason} SQLite reported: {message}",
            stage=f"storage.repository.{operation}",
            data_changed=False,
            retry_safe=True,
            cause=repr(exc),
            detail=f"id={getattr(entity, 'id', None)!r}",
            next_options=(
                "Check that referenced identifiers exist.",
                "Run 'python -m app db' to inspect the schema.",
            ),
        )

    def _storage_error(
        self, exc: sqlite3.Error, operation: str, entity_name: str
    ) -> StorageError:
        return StorageError.of(
            f"A database operation on {entity_name} failed.",
            f"{type(exc).__name__}: {exc}",
            stage=f"storage.repository.{operation}",
            data_changed=None,
            retry_safe=None,
            cause=repr(exc),
            next_options=(
                "Run 'python -m app db' to check the database.",
                "Restore a backup from data/backups if the database is damaged.",
            ),
        )
