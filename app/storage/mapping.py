"""Conversion between dataclasses and database rows.

ADR 0005 chose standard-library dataclasses, which means serialisation is written
rather than generated. It is small because the type information is already there:
`dataclasses.fields()` reports each field's real type, and this module converts
on that basis.

SQLite's storage classes do not cover Python's types exactly, so three
conversions are needed:

* `StrEnum`  <-> `TEXT`    - the readable name is stored, and re-checked by a
  `CHECK` constraint, so the vocabulary is enforced in the database too.
* `bool`     <-> `INTEGER` - STRICT tables have no boolean type; 0 and 1 are used
  and constrained with `CHECK (x IN (0,1))`.
* `None`     <-> `NULL`    - unchanged, and meaningful: unknown stays unknown.
"""

import sqlite3
from dataclasses import fields
from enum import StrEnum
from types import UnionType
from typing import Any, get_args, get_origin

from app.core.errors import InvalidInputError

#: Cache of column names per entity class.
_COLUMNS: dict[type, tuple[str, ...]] = {}


def columns(entity_type: type) -> tuple[str, ...]:
    """Column names for an entity, in declaration order."""
    cached = _COLUMNS.get(entity_type)
    if cached is None:
        cached = tuple(f.name for f in fields(entity_type))
        _COLUMNS[entity_type] = cached
    return cached


def to_row(entity: object) -> dict[str, object]:
    """Convert a dataclass instance into column values ready for SQLite."""
    row: dict[str, object] = {}
    for field in fields(entity):  # type: ignore[arg-type]
        row[field.name] = _to_sql(getattr(entity, field.name))
    return row


def from_row(entity_type: type, row: sqlite3.Row | dict[str, Any]) -> object:
    """Rebuild a dataclass instance from a database row."""
    data = dict(row)
    kwargs: dict[str, object] = {}
    for field in fields(entity_type):
        if field.name not in data:
            raise InvalidInputError.of(
                f"A stored row is missing the column {field.name!r}.",
                f"{entity_type.__name__} expects columns "
                f"{', '.join(columns(entity_type))}.",
                stage="storage.mapping.from_row",
                missing=(field.name,),
                available=tuple(sorted(data)),
                data_changed=False,
                retry_safe=False,
                next_options=(
                    "The database schema may be older than this build; "
                    "run 'python -m app db' to migrate it.",
                ),
            )
        kwargs[field.name] = _from_sql(field.type, data[field.name])
    return entity_type(**kwargs)


def _to_sql(value: object) -> object:
    if isinstance(value, StrEnum):
        return str(value)
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def _from_sql(declared: Any, value: object) -> object:
    if value is None:
        return None
    target = _unwrap_optional(declared)
    if isinstance(target, type):
        if issubclass(target, StrEnum):
            return target(value)
        if target is bool:
            return bool(value)
    return value


def _unwrap_optional(declared: Any) -> Any:
    """Return `T` for a declared type of `T | None`, otherwise the type itself."""
    if isinstance(declared, UnionType) or get_origin(declared) is UnionType:
        candidates = [arg for arg in get_args(declared) if arg is not type(None)]
        if len(candidates) == 1:
            return candidates[0]
    return declared
