"""Persistence (L1 adapters).

SQL lives here and nowhere else, so the database can be replaced without touching
the conceptual model (master specification Part 1 section 4). Everything above
this layer works with the dataclasses in `app.models`.

Contents:

* `connection` - the single factory that opens a database with the pragmas
  decision D-01 requires, including `foreign_keys=ON`.
* `migrator`   - schema versioning and forward-only migrations.
* `ids`        - the identifier allocator of ADR 0006.
* `mapping`    - conversion between dataclasses and database rows.
* `repository` - create, retrieve, update.
"""

from app.storage.connection import (
    DatabaseRole,
    connect,
    database_path,
    read_metadata,
)
from app.storage.ids import IdAllocator
from app.storage.mapping import from_row, to_row
from app.storage.migrator import (
    CODE_SCHEMA_VERSION,
    MigrationReport,
    applied_migrations,
    migrate,
    schema_version,
)
from app.storage.repository import Repository

__all__ = [
    "CODE_SCHEMA_VERSION",
    "DatabaseRole",
    "IdAllocator",
    "MigrationReport",
    "Repository",
    "applied_migrations",
    "connect",
    "database_path",
    "from_row",
    "migrate",
    "read_metadata",
    "schema_version",
    "to_row",
]
