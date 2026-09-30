"""Schema versioning and migrations (Part 4 sections 150-151).

Section 151 states the sequence, and this module follows it literally::

    Detect existing version -> Validate database -> Backup where appropriate
    -> Apply migration -> Verify

Two rules the specification is emphatic about:

* "Do not destroy the existing knowledge database merely to install a new schema."
  Nothing here drops or recreates a database. Migrations are forward-only and each
  runs in its own transaction, so a failure leaves the previous version intact.
* A database written by a *newer* RUDRA is refused rather than opened, because
  downgrading silently would risk misreading data whose meaning has changed.

The applied version is recorded twice: in `schema_migrations` (with a checksum of
the script that ran) and in `PRAGMA user_version` (cheap to read without a query).
"""

import hashlib
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import FailureCategory, StorageError
from app.models.base import utc_now
from app.models.identifiers import EntityKind

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def available_migrations() -> tuple[Migration, ...]:
    """Every migration script on disk, in version order."""
    found: list[Migration] = []
    for path in sorted(SCHEMA_DIR.glob("*.sql")):
        stem = path.stem
        number, _, name = stem.partition("_")
        try:
            version = int(number)
        except ValueError as exc:
            raise StorageError.of(
                "A migration script has an unusable file name.",
                f"{path.name} must start with a version number, as in 0001_initial.sql.",
                stage="storage.migrations.discover",
                data_changed=False,
                retry_safe=False,
                cause=repr(exc),
                next_options=("Rename the script to <number>_<name>.sql.",),
            ) from exc
        found.append(
            Migration(version=version, name=name or stem, sql=path.read_text(encoding="utf-8"))
        )
    return tuple(found)


#: The schema version this build of RUDRA expects.
CODE_SCHEMA_VERSION: int = max((m.version for m in available_migrations()), default=0)


@dataclass(frozen=True, slots=True)
class MigrationReport:
    """What `migrate()` actually did, so callers report facts rather than intent."""

    database: Path
    version_before: int
    version_after: int
    applied: tuple[int, ...]
    backup_path: Path | None
    integrity: str
    created: bool

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def schema_version(connection: sqlite3.Connection) -> int:
    """The database's current schema version. 0 means empty or unmigrated."""
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def applied_migrations(connection: sqlite3.Connection) -> tuple[dict[str, object], ...]:
    """Rows of `schema_migrations`, oldest first. Empty on an unmigrated database."""
    try:
        rows = connection.execute(
            "SELECT version, name, checksum, applied_at FROM schema_migrations "
            "ORDER BY version"
        ).fetchall()
    except sqlite3.OperationalError:
        return ()
    return tuple(dict(row) for row in rows)


def migrate(connection: sqlite3.Connection, *, database_path: Path | None = None) -> MigrationReport:
    """Bring a database up to `CODE_SCHEMA_VERSION`, following Part 4 section 151."""
    migrations = available_migrations()

    # 1. Detect the existing version.
    current = schema_version(connection)
    created = current == 0

    if current > CODE_SCHEMA_VERSION:
        raise StorageError.of(
            "This database was created by a newer version of RUDRA.",
            f"The database is at schema version {current}; this build understands "
            f"{CODE_SCHEMA_VERSION}.",
            category=FailureCategory.INVALID_INPUT,
            stage="storage.migrate.detect",
            data_changed=False,
            retry_safe=False,
            next_options=(
                "Use the newer version of RUDRA with this database.",
                "Restore a backup made by this version from data/backups.",
            ),
        )

    # 2. Validate the database before touching it.
    integrity = _integrity_check(connection, stage="storage.migrate.validate")

    pending = [m for m in migrations if m.version > current]
    if not pending:
        return MigrationReport(
            database=Path(database_path) if database_path else Path(":memory:"),
            version_before=current,
            version_after=current,
            applied=(),
            backup_path=None,
            integrity=integrity,
            created=False,
        )

    # 3. Back up where appropriate: only when there is existing data to lose.
    backup_path = None
    if current > 0 and database_path is not None:
        backup_path = _backup(connection, database_path)

    # 4. Apply each migration in its own transaction.
    for migration in pending:
        _apply(connection, migration)

    # 5. Verify.
    integrity = _integrity_check(connection, stage="storage.migrate.verify")
    final = schema_version(connection)
    if final != CODE_SCHEMA_VERSION:  # pragma: no cover - guard against a bad script
        raise StorageError.of(
            "The database did not reach the expected schema version.",
            f"Expected {CODE_SCHEMA_VERSION}, found {final} after migrating.",
            stage="storage.migrate.verify",
            data_changed=True,
            retry_safe=False,
            next_options=("Restore the backup and report this.",),
        )

    return MigrationReport(
        database=Path(database_path) if database_path else Path(":memory:"),
        version_before=current,
        version_after=final,
        applied=tuple(m.version for m in pending),
        backup_path=backup_path,
        integrity=integrity,
        created=created,
    )


def _apply(connection: sqlite3.Connection, migration: Migration) -> None:
    """Run one migration atomically - script AND bookkeeping - then record it.

    **Corrected in Phase 5.** This previously ran the script through
    `executescript`, which commits any open transaction and then runs each
    statement *outside* one, with only the bookkeeping inside `with connection:`.
    A migration that failed partway therefore left its earlier statements
    committed: measured on migration 0004 with a failing statement appended, the
    database stayed at version 3 but already had `extraction_run` and
    `source_occurrence.char_start` - half migrated - while the error report said
    `data_changed=False`, and every retry then failed on "table already exists".
    Migrations 0001-0003 never exposed it because each succeeded; the existing
    failure test used a script that fails on its *first* statement.

    The script now runs inside one explicit transaction opened by its own
    `BEGIN IMMEDIATE`, which `executescript` leaves open; the bookkeeping joins the
    same transaction; one `COMMIT` ends it. SQLite DDL is transactional, so any
    failure rolls the whole migration back and the previous version really is
    intact. No migration script contains transaction control (their `BEGIN`s are
    trigger bodies) or a pragma, which is what makes the wrapping safe.

    **Foreign keys (Phase 8, migration 0006).** Rebuilding a table that other tables
    reference - `relationship`, referenced `ON DELETE RESTRICT` - is refused while
    enforcement is on, and deferring enforcement only moves the refusal to COMMIT
    (measured 2026-09-23). Every migration therefore runs with enforcement switched
    off *outside* its transaction (the pragma is a no-op inside one), checks the
    whole database with `PRAGMA foreign_key_check` before committing, and rolls
    back on any violation - SQLite's documented procedure for a table rebuild.
    Enforcement is switched back on afterwards, whatever happened, and confirmed.
    """
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + migration.sql)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                f"{len(violations)} foreign key violation(s) after migration "
                f"{migration.version}; first: {tuple(violations[0])}"
            )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, checksum, applied_at) "
            "VALUES (?, ?, ?, ?)",
            (migration.version, migration.name, migration.checksum, utc_now()),
        )
        connection.execute(f"PRAGMA user_version = {int(migration.version)}")
        _seed(connection, migration.version)
        connection.commit()
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.rollback()
        raise StorageError.of(
            "A database migration failed and was rolled back.",
            f"{type(exc).__name__}: {exc}",
            stage="storage.migrate.apply",
            completed=(f"schema version {migration.version - 1}",),
            not_completed=(f"migration {migration.version} ({migration.name})",),
            data_changed=False,
            retry_safe=True,
            cause=repr(exc),
            next_options=(
                "Fix the migration script and try again.",
                "The previous schema version is intact; no knowledge was destroyed.",
            ),
        ) from exc
    finally:
        _restore_foreign_keys(connection)


def _restore_foreign_keys(connection: sqlite3.Connection) -> None:
    """Switch enforcement back on after a migration, and confirm it took effect.

    A connection left with enforcement off would silently drop the Part 7 protection
    `connect()` exists to guarantee, so a failure here is reported, never ignored.
    """
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:  # pragma: no cover
        raise StorageError.of(
            "Foreign key enforcement could not be restored after a migration.",
            "PRAGMA foreign_keys did not return to ON; the connection is unsafe to use.",
            stage="storage.migrate.foreign_keys",
            data_changed=None,
            retry_safe=False,
            next_options=("Close RUDRA and open the database again.",),
        )


def _seed(connection: sqlite3.Connection, version: int) -> None:
    """Populate rows the schema needs to function (ADR 0013).

    Identifier counters are seeded on **every** migration, from the live
    `EntityKind` registry, with `INSERT OR IGNORE`. Two properties follow, and both
    matter:

    * A fresh database and an upgraded one converge on identical `id_sequence`
      contents. Before this rule, `_seed` ran only at version 1 while iterating the
      *live* enum, so adding an entity kind broke the two paths in **opposite**
      directions - a fresh database already held the new counter (so a migration
      that inserted it hit the primary key), while an existing one did not (so
      allocation failed). No single migration statement could satisfy both.
    * A counter already in use is **never reset**. `OR IGNORE` leaves it alone.
      Resetting one would re-issue identifiers that already exist, breaking ADR
      0006's "never reused" rule.

    No migration script may seed `id_sequence`; the enum is the single source of
    truth. Database metadata stays version-1 only, because `instance_id` must not
    be regenerated.
    """
    connection.executemany(
        "INSERT OR IGNORE INTO id_sequence(entity_kind, next_value) VALUES (?, 1)",
        [(kind.value,) for kind in EntityKind],
    )
    if version != 1:
        return
    connection.executemany(
        "INSERT INTO database_metadata(key, value) VALUES (?, ?)",
        [
            # Identifiers are unique within a database, not across installations
            # (ADR 0006). This records which installation minted them.
            ("instance_id", str(uuid.uuid4())),
            ("created_at", utc_now()),
            ("role", "knowledge"),
        ],
    )


def _backup(connection: sqlite3.Connection, database_path: Path) -> Path:
    """Copy the database before migrating it (Part 4 section 151).

    Uses SQLite's online backup API rather than a file copy, so the copy is
    consistent even if something else is reading the database.
    """
    backups = database_path.parent.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().replace(":", "").replace("-", "").replace(".", "")
    target = backups / f"{database_path.stem}-pre-migration-{stamp}.db"
    # `with sqlite3.connect(...)` commits the transaction but does NOT close the
    # connection - a long-standing sqlite3 surprise. Left as a context manager, every
    # migration leaked a connection and a file handle, and on Windows the backup file
    # stayed locked until garbage collection. Phase 2 never noticed because only a
    # synthetic test reached this path; migration 0002 is the first real one.
    destination: sqlite3.Connection | None = None
    try:
        destination = sqlite3.connect(target)
        connection.backup(destination)
        destination.close()
        destination = None
    except (sqlite3.Error, OSError) as exc:
        raise StorageError.of(
            "RUDRA could not back up the database before migrating it.",
            f"{type(exc).__name__}: {exc}",
            stage="storage.migrate.backup",
            data_changed=False,
            retry_safe=True,
            cause=repr(exc),
            next_options=(
                f"Check free space and permissions for {backups}.",
                "The migration was not attempted; the database is unchanged.",
            ),
        ) from exc
    finally:
        if destination is not None:
            destination.close()
    return target


def _integrity_check(connection: sqlite3.Connection, *, stage: str) -> str:
    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise StorageError.of(
            "The database failed its integrity check.",
            f"SQLite reported: {result}",
            stage=stage,
            data_changed=False,
            retry_safe=False,
            next_options=(
                "Restore the most recent backup from data/backups.",
                "Do not continue writing to this database.",
            ),
        )
    return result
