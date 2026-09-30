"""Backup and restore for the knowledge database (Part 6 section 21, ADR 0025).

Part 6 section 21:

    Backups are useful only if restoration works. ... Do not merely create backup
    files and assume they are valid.

Four phases created backups and none restored one. This module is the restore half,
and `tests/integration/test_backup_restore.py` is the rehearsal.

**Backup** reuses the migrator's `_backup()` - SQLite's online Backup API - rather
than a second mechanism, because the point is to verify what RUDRA actually
produces (ADR 0025).

**Restore** is a file copy back, with one step that is easy to omit and fatal to
skip. The live database runs in WAL mode; a backup is a single consistent file with
no `-wal` or `-shm` beside it. If a crashed session left a `-wal` next to the
target, SQLite would replay those stale frames onto the restored file on the next
open. The live database had no sidecars when this was designed - a clean shutdown
checkpoints them away - which is exactly why a rehearsal of only the clean case
would prove nothing. So restore removes them first.

What restore does **not** protect against: the backups live on D:, and C: and D:
are partitions of one physical SSD. A restore survives a mistake, not a failed
disk. Decision D-15 - a second physical backup device - remains open and is the
user's to make.

Restore is deliberately not a CLI command (ADR 0021 keeps the CLI minimal, and a
restore is a deliberate, human-confirmed act under Part 5 section 244).
"""

import shutil
import sqlite3
from pathlib import Path

from app.core.errors import StorageError
from app.storage.migrator import _backup

#: The files SQLite keeps beside a WAL-mode database.
SIDECAR_SUFFIXES: tuple[str, ...] = ("-wal", "-shm")


def backup_database(connection: sqlite3.Connection, database_path: Path) -> Path:
    """Take a consistent backup of an open database, as migration does."""
    return _backup(connection, Path(database_path))


def sidecars(database_path: Path) -> tuple[Path, ...]:
    """The WAL sidecar files that currently exist beside `database_path`."""
    base = Path(database_path)
    return tuple(
        path
        for path in (base.with_name(base.name + suffix) for suffix in SIDECAR_SUFFIXES)
        if path.exists()
    )


def restore_database(backup_path: Path, database_path: Path) -> tuple[Path, ...]:
    """Replace `database_path` with `backup_path`. Every connection must be closed.

    Returns the stale sidecar files that were removed, so the caller can report
    them - removing files is a fact the user is entitled to, not a silent step.
    """
    backup = Path(backup_path)
    target = Path(database_path)
    if not backup.is_file():
        raise StorageError.of(
            "The backup to restore from does not exist.",
            f"No file at {backup}.",
            stage="storage.backup.restore",
            data_changed=False,
            retry_safe=True,
            next_options=("List data/backups/ and choose an existing backup.",),
        )
    removed: list[Path] = []
    for stale in sidecars(target):
        stale.unlink()
        removed.append(stale)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(backup, target)
    return tuple(removed)
