"""The database connection factory (decision D-01, ADR 0004).

Every connection in RUDRA must come from `connect()`. This is not a style
preference. SQLite disables foreign keys **per connection**, and the default is
OFF - verified on this machine. A single connection opened without the pragma
would silently drop the Part 7 protection that stops a deleted source file from
cascading into knowledge. Centralising the pragmas is what makes that guarantee
real, and `tests/unit/test_connection.py` asserts it.

Two databases (ADR 0004):

* `knowledge.db` - authoritative: knowledge, provenance, procedures and the audit
  trail, so an audit record commits with the change it describes.
* `index.db` - rebuildable: FTS5 and derived indexes. **Not created in Phase 2**,
  because nothing indexes anything yet. The role exists here so the factory
  treats both databases identically when Phase 9 needs it.

A **read-only** mode (`connect(..., read_only=True)`, ADR 0031 I7-B) serves lookups
that must never write: it opens an existing file with SQLite's `mode=ro`, adds
`query_only`, and never issues the journal-mode or synchronous pragmas, because
changing the journal mode is itself a write.
"""

import sqlite3
from enum import StrEnum
from pathlib import Path

from app.core.errors import StorageError
from app.core.paths import PathLayout


class DatabaseRole(StrEnum):
    """Which of the two databases a connection is for."""

    #: Authoritative store. Losing it loses knowledge.
    KNOWLEDGE = "knowledge"
    #: Derived indexes. Safe to delete and rebuild.
    INDEX = "index"


#: File name for each role.
FILENAMES = {
    DatabaseRole.KNOWLEDGE: "knowledge.db",
    DatabaseRole.INDEX: "index.db",
}


def database_path(layout: PathLayout, role: DatabaseRole) -> Path:
    """Where a database lives, given the project path layout."""
    if role is DatabaseRole.KNOWLEDGE:
        return layout.database_dir / FILENAMES[role]
    return layout.indexes_dir / FILENAMES[role]


def connect(
    path: Path | str,
    *,
    role: DatabaseRole = DatabaseRole.KNOWLEDGE,
    read_only: bool = False,
) -> sqlite3.Connection:
    """Open a database with RUDRA's required settings.

    Applies, in order:

    * `foreign_keys=ON`   - per connection, off by default; enforces Part 7.
    * `journal_mode=WAL`  - crash safety; persists in the file itself.
    * `synchronous=NORMAL`- the usual companion to WAL.
    * `busy_timeout`      - wait rather than fail if another writer holds the lock.

    Rows come back as `sqlite3.Row` so columns can be read by name.

    With `read_only=True` none of the above is applied: see `_connect_read_only`.
    """
    if read_only:
        return _connect_read_only(Path(path))
    target = str(path)
    try:
        connection = sqlite3.connect(target, timeout=30.0, isolation_level="DEFERRED")
    except sqlite3.Error as exc:
        raise StorageError.of(
            "RUDRA could not open its database.",
            f"{type(exc).__name__}: {exc}",
            stage="storage.connect",
            missing=(target,),
            data_changed=False,
            retry_safe=True,
            cause=repr(exc),
            next_options=(
                f"Check that {Path(target).parent} exists and is writable.",
                "Run 'python -m app' to create the directory layout.",
            ),
        ) from exc

    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        # An in-memory database has no file to journal; asking for WAL there is
        # meaningless and SQLite reports "memory".
        if target != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 30000")
    except sqlite3.Error as exc:
        connection.close()
        raise StorageError.of(
            "RUDRA opened the database but could not configure it safely.",
            f"{type(exc).__name__}: {exc}",
            stage="storage.configure",
            data_changed=False,
            retry_safe=True,
            cause=repr(exc),
            next_options=(
                "Check that the database file is not corrupt.",
                "Restore the most recent backup from data/backups.",
            ),
        ) from exc

    _assert_foreign_keys(connection, target)
    return connection


def _connect_read_only(path: Path) -> sqlite3.Connection:
    """Open an existing database so that nothing can be written through it.

    ADR 0031 I7-B. Three guards:

    * the file must already exist - a read-only open never creates a database;
    * SQLite's URI `mode=ro` - the connection itself cannot write;
    * `query_only=ON` - a second, independent refusal of any statement that would
      change the file.

    The journal-mode and synchronous pragmas are deliberately **not** issued:
    changing the journal mode writes to the file, and neither setting matters to a
    reader. `foreign_keys` is not set either; it only constrains writes. To read a
    WAL database SQLite may create its `-wal`/`-shm` sidecar files; the database
    file itself is never written.
    """
    if not path.is_file():
        raise StorageError.of(
            "RUDRA has no knowledge yet: no document has been added.",
            f"There is no knowledge database at {path}; reading never creates one.",
            stage="storage.connect.read_only",
            missing=(str(path),),
            data_changed=False,
            retry_safe=True,
            next_options=(
                "Add a document first (the Add document page, or: extract <path-to-document>).",
                "If you expected knowledge here, check that RUDRA is using the right data folder.",
            ),
        )
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=30.0)
    except sqlite3.Error as exc:
        raise StorageError.of(
            "RUDRA could not open its database for reading.",
            f"{type(exc).__name__}: {exc}",
            stage="storage.connect.read_only",
            detail=str(path),
            data_changed=False,
            retry_safe=True,
            cause=repr(exc),
            next_options=(
                f"Check that {path} is readable.",
                "Close any other program that holds the file exclusively.",
            ),
        ) from exc

    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        # Reads the file header, so a file that is not an SQLite database fails
        # here, while the failure can still be reported as a storage problem.
        connection.execute("PRAGMA user_version").fetchone()
    except sqlite3.Error as exc:
        connection.close()
        raise StorageError.of(
            "RUDRA could not read that file as a knowledge database.",
            f"{type(exc).__name__}: {exc}",
            stage="storage.connect.read_only",
            detail=str(path),
            data_changed=False,
            retry_safe=True,
            cause=repr(exc),
            next_options=(
                "Check that --project-root names a RUDRA project.",
                "Restore the most recent backup from data/backups.",
            ),
        ) from exc
    return connection


def _assert_foreign_keys(connection: sqlite3.Connection, target: str) -> None:
    """Confirm the pragma took effect rather than assuming it did.

    A build of SQLite compiled without foreign key support would accept the
    pragma silently and ignore it, which would remove the Part 7 guarantee
    without any visible failure.
    """
    enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    if enabled != 1:  # pragma: no cover - would need a crippled SQLite build
        connection.close()
        raise StorageError.of(
            "RUDRA refuses to use a database without foreign key enforcement.",
            "PRAGMA foreign_keys did not take effect, so deleting a source could "
            "silently cascade into knowledge records.",
            stage="storage.configure",
            data_changed=False,
            retry_safe=False,
            detail=f"database={target}",
            next_options=(
                "Use a build of SQLite compiled with foreign key support.",
                "Report this together with the output of 'python -m app env'.",
            ),
        )


def read_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    """Return the database metadata table, including `instance_id` (ADR 0006).

    Returns an empty mapping when the table does not exist yet, which is the
    normal state of a database before its first migration.
    """
    try:
        rows = connection.execute("SELECT key, value FROM database_metadata").fetchall()
    except sqlite3.OperationalError:
        # The table is absent on an unmigrated database. That is a state, not a
        # failure, so it is reported as emptiness rather than raised.
        return {}
    return {row["key"]: row["value"] for row in rows}
