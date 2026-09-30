"""Directory layout and storage classes.

The specification treats the different kinds of stored data as separate layers with
separate lifecycles (Part 4 sections 153-155, Part 7 sections 2 and 16). The most
consequential rule is Part 7: deleting an original file must never delete the
knowledge extracted from it, and deleting a rebuildable artefact must never destroy
anything authoritative.

That distinction starts here, with each directory carrying an explicit
`StorageClass`, so later phases cannot quietly treat a source document as a cache.

Phase 1 creates these directories and nothing else. Most are empty until the phase
named in `first_used` arrives; `StorageClass` and `first_used` are recorded now so
the separation is real rather than aspirational.

RUDRA never deletes a directory here. Cleanup of rebuildable data is a later,
explicit, audited operation (Part 7 sections 15-19).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.config.schema import RudraConfig
from app.core.errors import StorageError


class StorageClass(StrEnum):
    """What may safely happen to the contents of a directory.

    SOURCE_DATA and PERSISTENT_KNOWLEDGE are authoritative: they cannot be
    regenerated. REBUILDABLE and TEMPORARY can be deleted and reconstructed.
    """

    SOURCE_DATA = "SOURCE_DATA"
    PERSISTENT_KNOWLEDGE = "PERSISTENT_KNOWLEDGE"
    REBUILDABLE = "REBUILDABLE"
    TEMPORARY = "TEMPORARY"
    BACKUP = "BACKUP"
    LOGS = "LOGS"
    CONFIG = "CONFIG"


@dataclass(frozen=True, slots=True)
class DirectorySpec:
    """One managed directory."""

    key: str
    path: Path
    storage_class: StorageClass
    purpose: str
    #: Phase that starts using it. "Phase 1" means it is in use now.
    first_used: str


@dataclass(frozen=True, slots=True)
class DirectoryReport:
    """Outcome of ensuring the directory layout exists."""

    created: tuple[Path, ...]
    existing: tuple[Path, ...]

    @property
    def total(self) -> int:
        return len(self.created) + len(self.existing)


@dataclass(frozen=True, slots=True)
class PathLayout:
    """Absolute paths RUDRA uses, derived from the project root and configuration."""

    project_root: Path
    config_dir: Path
    data_root: Path
    documents_dir: Path
    extracted_dir: Path
    knowledge_dir: Path
    database_dir: Path
    indexes_dir: Path
    cache_dir: Path
    procedures_dir: Path
    sources_dir: Path
    backups_dir: Path
    logs_dir: Path

    @classmethod
    def from_config(cls, project_root: Path, config: RudraConfig) -> "PathLayout":
        root = project_root.resolve()
        data_root = _resolve(root, config.paths.data_root)
        return cls(
            project_root=root,
            config_dir=root / "config",
            data_root=data_root,
            documents_dir=data_root / "documents",
            extracted_dir=data_root / "extracted",
            knowledge_dir=data_root / "knowledge",
            database_dir=data_root / "database",
            indexes_dir=data_root / "indexes",
            cache_dir=data_root / "cache",
            procedures_dir=data_root / "procedures",
            sources_dir=data_root / "sources",
            backups_dir=data_root / "backups",
            logs_dir=_resolve(root, config.paths.logs_dir),
        )

    def managed_directories(self) -> tuple[DirectorySpec, ...]:
        """Every directory RUDRA creates, with its class and purpose."""
        return (
            DirectorySpec(
                "config", self.config_dir, StorageClass.CONFIG,
                "User configuration. Never secrets.", "Phase 1",
            ),
            DirectorySpec(
                "logs", self.logs_dir, StorageClass.LOGS,
                "Application log and audit channel files.", "Phase 1",
            ),
            DirectorySpec(
                "data_root", self.data_root, StorageClass.PERSISTENT_KNOWLEDGE,
                "Root of all RUDRA data layers.", "Phase 1",
            ),
            DirectorySpec(
                "documents", self.documents_dir, StorageClass.SOURCE_DATA,
                "Original user documents, preserved unmodified. Not a cache.",
                "Phase 4",
            ),
            DirectorySpec(
                "extracted", self.extracted_dir, StorageClass.REBUILDABLE,
                "Bulky extraction intermediates such as page images and raw OCR output.",
                "Phase 4",
            ),
            DirectorySpec(
                "knowledge", self.knowledge_dir, StorageClass.PERSISTENT_KNOWLEDGE,
                "Portable snapshots of canonical knowledge, for inspection and backup.",
                "Phase 7",
            ),
            DirectorySpec(
                "database", self.database_dir, StorageClass.PERSISTENT_KNOWLEDGE,
                "Authoritative knowledge and audit databases.", "Phase 2",
            ),
            DirectorySpec(
                "indexes", self.indexes_dir, StorageClass.REBUILDABLE,
                "Search indexes, rebuildable from the authoritative database.",
                "Phase 9",
            ),
            DirectorySpec(
                "cache", self.cache_dir, StorageClass.TEMPORARY,
                "Disposable caches.", "Phase 4",
            ),
            DirectorySpec(
                "procedures", self.procedures_dir, StorageClass.PERSISTENT_KNOWLEDGE,
                "Procedure and application-adapter definitions.", "Phase 16",
            ),
            DirectorySpec(
                "sources", self.sources_dir, StorageClass.PERSISTENT_KNOWLEDGE,
                "Source registry exports and external-source snapshots.", "Phase 4",
            ),
            DirectorySpec(
                "backups", self.backups_dir, StorageClass.BACKUP,
                "Local backups. Never uploaded anywhere.", "Phase 7",
            ),
        )

    def ensure(self) -> DirectoryReport:
        """Create any missing managed directory. Never deletes or overwrites.

        Raises `StorageError` naming the exact directory if creation fails, rather
        than letting a bare OSError escape (Part 4 section 134).
        """
        created: list[Path] = []
        existing: list[Path] = []
        for spec in self.managed_directories():
            if spec.path.is_dir():
                existing.append(spec.path)
                continue
            try:
                spec.path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise StorageError.of(
                    "RUDRA could not create a directory it needs to start.",
                    f"{type(exc).__name__}: {exc}",
                    stage="startup.directories",
                    missing=(str(spec.path),),
                    completed=tuple(str(p) for p in created),
                    not_completed=(f"create {spec.path}",),
                    data_changed=bool(created),
                    retry_safe=True,
                    cause=repr(exc),
                    next_options=(
                        f"Check that {spec.path.parent} exists and is writable.",
                        "Check free disk space.",
                        "Set paths.data_root in config/rudra.toml to a writable location.",
                    ),
                ) from exc
            created.append(spec.path)
        return DirectoryReport(created=tuple(created), existing=tuple(existing))


#: A file of this name beside the packaged executable keeps all data beside it (portable use).
PORTABLE_MARKER = "portable.txt"
#: The folder under %LOCALAPPDATA% that holds an installed RUDRA's data.
USER_DATA_FOLDER = "RUDRA"


def packaged_project_root(executable: Path, environ: Mapping[str, str] | None = None) -> Path:
    """Where the packaged program keeps its configuration, data and logs.

    Installed, the program folder belongs to the application and is replaced by every
    upgrade, so the user's data lives apart from it, in ``%LOCALAPPDATA%\\RUDRA``: it
    survives upgrades, reinstalls and uninstalling. A folder that already holds RUDRA data
    beside the executable (a portable copy, or one marked by ``portable.txt``) keeps using
    it.
    """
    folder = Path(executable).resolve().parent
    if (folder / PORTABLE_MARKER).is_file() or (folder / "data").is_dir() or (folder / "config").is_dir():
        return folder
    env = os.environ if environ is None else environ
    local = env.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / USER_DATA_FOLDER


def _resolve(project_root: Path, value: str) -> Path:
    """Resolve a configured path, treating relative paths as project-relative."""
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (project_root / candidate).resolve()


def free_disk_mb(path: Path) -> int | None:
    """Free megabytes on the volume holding `path`, or None if it cannot be read.

    Returns None rather than guessing, so callers report "unknown" honestly
    (Part 4 section 158).
    """
    import shutil

    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free // (1024 * 1024)
    except OSError:
        return None
