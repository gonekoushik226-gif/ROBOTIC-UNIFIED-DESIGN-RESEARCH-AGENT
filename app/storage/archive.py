"""Knowledge-base backup files: export everything a user's knowledge needs, restore it safely.

A backup is one ``.rudrabackup`` file: a ZIP archive holding

    manifest.json               format, versions, contents, counts and SHA-256 of every file
    database/knowledge.db       a consistent snapshot of the knowledge database
    documents/<sha256>.<ext>    RUDRA's preserved copies of the imported documents
    extracted/<sha256>/page-N.ocr.json   OCR lines and word boxes of recognized pages
    extracted/<sha256>/page-N.math.json  display equations rebuilt from a PDF page's layout,
                                         with their evidence (the page's flat text is kept)

The database alone is not enough: provenance checks re-read the preserved copy of a
document and compare its SHA-256 with the one recorded at import, so the preserved
documents travel with it. Nothing else does. The keyword index is rebuilt from the
database (it is derived), caches and logs are disposable, configuration belongs to
the machine. Inside the snapshot, a document's location is stored relative
(``documents/<name>``) so the file names no folder of the machine it came from.

**Restoring** never trusts the file. Before anything of the current knowledge base is
touched, the archive is checked: format and version, every member's name (no absolute
paths, no ``..``, only the database and document names RUDRA itself writes, so nothing
executable can be placed anywhere), every size and checksum against the manifest, the
database's integrity and schema version, and the row counts the manifest promises. A
backup from a newer RUDRA is refused. An older one is migrated by the ordinary migrator,
on the staged copy. The document locations are pointed at this installation's document
store, and every document's file is checked against its recorded hash.

Only then is the current knowledge base replaced - and it is moved, not deleted, into
``backups/pre-restore-<time>/``. If anything fails part-way, it is moved back. Replacing
an existing knowledge base needs the caller's explicit ``replace_existing=True``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from app.core.errors import RudraError
from app.core.paths import PathLayout
from app.storage.connection import DatabaseRole, connect, database_path
from app.storage.migrator import CODE_SCHEMA_VERSION, migrate, schema_version

FORMAT = "rudra-knowledge-backup"
#: Version of the backup file format. Increase only with a reader for the older format.
FORMAT_VERSION = 1
SUFFIX = ".rudrabackup"
MANIFEST = "manifest.json"
DATABASE_MEMBER = "database/knowledge.db"
#: The stored-copy suffixes of every importable format (`app.documents.formats.STORED_SUFFIXES`,
#: kept equal by a test). Nothing executable is among them.
DOCUMENT_NAME = re.compile(
    r"^[0-9A-Fa-f]{64}\.(?:pdf|html|htm|txt|md|csv|rtf|docx|pptx|xlsx|epub|png|jpg|tiff)$", re.IGNORECASE)
#: Page layout records beside a document: OCR lines (.ocr.json) and rebuilt equations (.math.json).
OCR_MEMBER = re.compile(r"^extracted/[0-9A-Fa-f]{64}/page-\d{1,6}\.(?:ocr|math)\.json$")
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
#: Refuse archives whose members claim more than this (a guard against ZIP bombs).
MAX_TOTAL_BYTES = 64 * 1024 ** 3
CHUNK = 1024 * 1024
SIDECARS = ("-wal", "-shm", "-journal")


class BackupError(Exception):
    """A backup could not be written, or a file cannot be restored. Nothing was changed."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.message}\n{self.detail}".strip()


@dataclass(frozen=True)
class BackupContents:
    """What a backup holds, read from its validated manifest."""

    path: Path
    format_version: int
    app_version: str
    schema_version: int
    created_at: str
    components: tuple[str, ...]
    files: tuple[dict, ...]
    counts: dict[str, int]

    @property
    def document_count(self) -> int:
        return sum(1 for entry in self.files if entry["path"].startswith("documents/"))

    @property
    def size(self) -> int:
        return sum(int(entry["size"]) for entry in self.files)


@dataclass(frozen=True)
class ExportReport:
    path: Path
    size: int
    documents: int
    schema_version: int
    counts: dict[str, int]
    skipped: tuple[str, ...] = ()


@dataclass(frozen=True)
class RestoreReport:
    contents: BackupContents
    migrated_from: int | None
    replaced_existing: bool
    previous_saved_to: Path | None
    documents: int
    verified: dict[str, object] = field(default_factory=dict)


# ------------------------------------------------------------------ helpers


def _utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {name: int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in tables}


def _integrity(connection: sqlite3.Connection) -> str:
    return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() \
        is not None


def _document_name(stored: str) -> str:
    """The file name of a stored document location, whichever machine wrote it."""
    return PurePosixPath(stored.replace("\\", "/")).name


def _safe_member(name: str) -> bool:
    """Only the names RUDRA writes: the manifest, the database, and hash-named documents."""
    if name in (MANIFEST, DATABASE_MEMBER):
        return True
    if "\\" in name or ":" in name or name.startswith("/"):
        return False
    if OCR_MEMBER.match(name):
        return True
    parts = PurePosixPath(name).parts
    return len(parts) == 2 and parts[0] == "documents" and bool(DOCUMENT_NAME.match(parts[1]))


def knowledge_base_exists(layout: PathLayout) -> bool:
    """True when this installation already holds a knowledge database or preserved documents."""
    database = database_path(layout, DatabaseRole.KNOWLEDGE)
    documents = layout.documents_dir
    return database.exists() or (documents.is_dir() and any(documents.iterdir()))


# ------------------------------------------------------------------ export


def export_knowledge(layout: PathLayout, destination: Path, *, app_version: str,
                     overwrite: bool = False) -> ExportReport:
    """Write the whole knowledge base to one backup file at `destination`."""
    from app.storage.connection import _connect_read_only

    source = database_path(layout, DatabaseRole.KNOWLEDGE)
    if not source.is_file():
        raise BackupError("There is no knowledge base to export yet.",
                          "Import a document first; the knowledge base is created by the first import.")
    target = Path(destination)
    if target.suffix.lower() != SUFFIX:
        target = target.with_name(target.name + SUFFIX)
    if target.exists() and not overwrite:
        raise BackupError(f"{target} already exists.", "Choose another name, or allow it to be replaced.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"The folder {target.parent} cannot be used.", str(exc)) from exc
    partial = target.with_name(target.name + ".partial")
    work = layout.cache_dir / f"export-{_stamp()}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        snapshot = work / "knowledge.db"
        live = _connect_read_only(source)
        try:
            copy = sqlite3.connect(snapshot)
            try:
                live.backup(copy)
            finally:
                copy.close()
        finally:
            live.close()
        documents_dir = layout.documents_dir.resolve()
        included: list[Path] = []
        skipped: list[str] = []
        connection = sqlite3.connect(snapshot)
        try:
            connection.execute("PRAGMA journal_mode = DELETE")
            if _integrity(connection) != "ok":
                raise BackupError("The knowledge database failed its integrity check; it was not exported.",
                                  "Nothing was written.")
            version = schema_version(connection)
            if _has_table(connection, "document"):
                for row_id, stored in connection.execute("SELECT id, file_path FROM document").fetchall():
                    name = _document_name(stored)
                    candidate = layout.documents_dir / name
                    inside = False
                    try:
                        Path(stored).resolve().relative_to(documents_dir)
                        inside = True
                    except (ValueError, OSError):
                        inside = candidate.is_file()
                    if inside and DOCUMENT_NAME.match(name):
                        connection.execute("UPDATE document SET file_path = ? WHERE id = ?",
                                           (f"documents/{name}", row_id))
                connection.commit()
            counts = _table_counts(connection)
        finally:
            connection.close()
        if layout.documents_dir.is_dir():
            for path in sorted(layout.documents_dir.iterdir()):
                if path.is_file() and DOCUMENT_NAME.match(path.name):
                    included.append(path)
                elif path.exists():
                    skipped.append(path.name)
        layouts = [path for document in included
                   for path in sorted((layout.extracted_dir / Path(document).stem).glob("page-*.json"))
                   if OCR_MEMBER.match(f"extracted/{path.parent.name}/{path.name}")]
        files = [{"path": DATABASE_MEMBER, "size": snapshot.stat().st_size, "sha256": _sha256_file(snapshot)}]
        files += [{"path": f"documents/{path.name}", "size": path.stat().st_size, "sha256": _sha256_file(path)}
                  for path in included]
        files += [{"path": f"extracted/{path.parent.name}/{path.name}", "size": path.stat().st_size,
                   "sha256": _sha256_file(path)} for path in layouts]
        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "app_version": app_version,
            "schema_version": version,
            "created_at": _utc(),
            "components": ["database", "documents"] + (["ocr"] if any(p.name.endswith(".ocr.json") for p in layouts)
                                                       else []) + (["math"] if any(p.name.endswith(".math.json")
                                                                                  for p in layouts) else []),
            "files": files,
            "counts": counts,
        }
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True))
            archive.write(snapshot, DATABASE_MEMBER)
            for path in included:
                archive.write(path, f"documents/{path.name}", compress_type=zipfile.ZIP_STORED)
            for path in layouts:
                archive.write(path, f"extracted/{path.parent.name}/{path.name}")
        inspect_backup(partial)  # the file just written must pass the same checks a restore makes
        os.replace(partial, target)
        return ExportReport(target, target.stat().st_size, len(included), version, counts, tuple(skipped))
    except BackupError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise BackupError("The backup could not be written.", f"{type(exc).__name__}: {exc}") from exc
    finally:
        if partial.exists():
            partial.unlink()
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ validation


def _read_manifest(archive: zipfile.ZipFile) -> dict:
    try:
        info = archive.getinfo(MANIFEST)
    except KeyError:
        raise BackupError("This is not a RUDRA backup.", "It has no manifest.json.") from None
    if info.file_size > MAX_MANIFEST_BYTES:
        raise BackupError("This backup's manifest is not valid.", "It is far too large.")
    try:
        manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BackupError("This backup's manifest is not valid.", str(exc)) from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise BackupError("This is not a RUDRA backup.", "Its manifest does not name the RUDRA backup format.")
    return manifest


def inspect_backup(path: Path) -> BackupContents:
    """Validate a backup file completely without extracting it. Raises BackupError."""
    path = Path(path)
    if not path.is_file():
        raise BackupError(f"{path} does not exist.")
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BackupError("This file is not a readable RUDRA backup.", f"{type(exc).__name__}: {exc}") from exc
    with archive:
        manifest = _read_manifest(archive)
        version = manifest.get("format_version")
        if not isinstance(version, int) or version < 1:
            raise BackupError("This backup's manifest is not valid.", "Its format version is missing.")
        if version > FORMAT_VERSION:
            raise BackupError("This backup was made by a newer version of RUDRA.",
                              f"It uses backup format {version}; this version reads format {FORMAT_VERSION}. "
                              "Install the newer RUDRA to restore it.")
        schema = manifest.get("schema_version")
        if not isinstance(schema, int) or schema < 1:
            raise BackupError("This backup's manifest is not valid.", "Its database schema version is missing.")
        if schema > CODE_SCHEMA_VERSION:
            raise BackupError("This backup was made by a newer version of RUDRA.",
                              f"Its knowledge database has schema version {schema}; this version understands "
                              f"{CODE_SCHEMA_VERSION}. Install the newer RUDRA "
                              f"({manifest.get('app_version', 'unknown')}) to restore it.")
        files = manifest.get("files")
        counts = manifest.get("counts")
        if not isinstance(files, list) or not isinstance(counts, dict):
            raise BackupError("This backup's manifest is not valid.", "Its file list or counts are missing.")
        listed: dict[str, dict] = {}
        for entry in files:
            if not (isinstance(entry, dict) and isinstance(entry.get("path"), str)
                    and isinstance(entry.get("size"), int) and entry["size"] >= 0
                    and isinstance(entry.get("sha256"), str) and len(entry["sha256"]) == 64):
                raise BackupError("This backup's manifest is not valid.", f"A file entry is malformed: {entry!r}")
            if not _safe_member(entry["path"]) or entry["path"] == MANIFEST:
                raise BackupError("This backup contains a file RUDRA would never write, so it is refused.",
                                  f"Refused name: {entry['path']!r}")
            if entry["path"] in listed:
                raise BackupError("This backup's manifest is not valid.", f"{entry['path']} is listed twice.")
            listed[entry["path"]] = entry
        if DATABASE_MEMBER not in listed:
            raise BackupError("This backup is incomplete.", "It holds no knowledge database.")
        if sum(entry["size"] for entry in listed.values()) > MAX_TOTAL_BYTES:
            raise BackupError("This backup is larger than RUDRA accepts.", "Its files add up to more than 64 GB.")
        present = {}
        for info in archive.infolist():
            if info.is_dir():
                raise BackupError("This backup contains a folder entry RUDRA never writes.", info.filename)
            if not _safe_member(info.filename):
                raise BackupError("This backup contains a file RUDRA would never write, so it is refused.",
                                  f"Refused name: {info.filename!r}")
            if info.filename in present:
                raise BackupError("This backup contains the same file twice.", info.filename)
            present[info.filename] = info
        missing = sorted(set(listed) - set(present))
        extra = sorted(set(present) - set(listed) - {MANIFEST})
        if missing:
            raise BackupError("This backup is incomplete.", "Missing: " + ", ".join(missing))
        if extra:
            raise BackupError("This backup holds files its manifest does not list.", ", ".join(extra))
        for name, entry in listed.items():
            info = present[name]
            if info.file_size != entry["size"]:
                raise BackupError("This backup is damaged.", f"{name} has the wrong size.")
            digest = hashlib.sha256()
            try:
                with archive.open(info) as handle:
                    while block := handle.read(CHUNK):
                        digest.update(block)
            except (zipfile.BadZipFile, OSError, EOFError) as exc:
                raise BackupError("This backup is damaged.", f"{name}: {exc}") from exc
            if digest.hexdigest() != entry["sha256"].lower():
                raise BackupError("This backup is damaged: a checksum does not match.",
                                  f"{name} is not the file that was backed up.")
        clean_counts = {str(k): int(v) for k, v in counts.items() if isinstance(v, int)}
        return BackupContents(path, version, str(manifest.get("app_version", "")), schema,
                              str(manifest.get("created_at", "")),
                              tuple(str(c) for c in manifest.get("components", ()) or ()),
                              tuple(listed.values()), clean_counts)


# ------------------------------------------------------------------ restore


def _extract(archive_path: Path, contents: BackupContents, staging: Path) -> None:
    """Write each validated member under `staging`, re-checking its hash as it is written."""
    root = staging.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for entry in contents.files:
            relative = PurePosixPath(entry["path"])
            target = staging.joinpath(*relative.parts)
            if root not in target.resolve().parents:
                raise BackupError("This backup tries to write outside RUDRA's folder, so it is refused.",
                                  entry["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            written = 0
            with archive.open(entry["path"]) as source, open(target, "xb") as out:
                while block := source.read(CHUNK):
                    written += len(block)
                    if written > entry["size"]:
                        raise BackupError("This backup is damaged.", f"{entry['path']} is longer than listed.")
                    digest.update(block)
                    out.write(block)
            if digest.hexdigest() != entry["sha256"].lower() or written != entry["size"]:
                raise BackupError("This backup is damaged: a checksum does not match.", entry["path"])


def _prepare_database(staged: Path, contents: BackupContents, documents_dir: Path) -> tuple[int | None, dict]:
    """Check the staged database, migrate it if it is older, and point its documents here."""
    connection = sqlite3.connect(staged)
    try:
        if _integrity(connection) != "ok":
            raise BackupError("The database in this backup is damaged.", "Its integrity check failed.")
        found = schema_version(connection)
        if found != contents.schema_version:
            raise BackupError("This backup is inconsistent.",
                              f"Its manifest says schema {contents.schema_version}; the database is {found}.")
        counts = _table_counts(connection)
        if counts != contents.counts:
            differ = sorted(k for k in set(counts) | set(contents.counts) if counts.get(k) != contents.counts.get(k))
            raise BackupError("This backup is inconsistent: its database does not hold what the manifest lists.",
                              "Different: " + ", ".join(differ[:10]))
    finally:
        connection.close()
    migrated_from = None
    if contents.schema_version < CODE_SCHEMA_VERSION:
        connection = connect(staged)
        try:
            migrate(connection, database_path=staged)
            migrated_from = contents.schema_version
        finally:
            connection.close()
    connection = sqlite3.connect(staged)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if _has_table(connection, "document"):
            for row_id, stored in connection.execute("SELECT id, file_path FROM document").fetchall():
                name = _document_name(stored)
                if DOCUMENT_NAME.match(name):
                    connection.execute("UPDATE document SET file_path = ? WHERE id = ?",
                                       (str(documents_dir / name), row_id))
            connection.commit()
        problems = connection.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise BackupError("The database in this backup has broken links between its records.",
                              f"{len(problems)} foreign-key problem(s), first in table {problems[0][0]}.")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")
        return migrated_from, _table_counts(connection)
    finally:
        connection.close()


def _verify_documents(database: Path, documents: Path) -> dict[str, int]:
    """Every document whose file came with the backup must match its recorded SHA-256."""
    connection = sqlite3.connect(database)
    try:
        if not _has_table(connection, "document"):
            return {"documents": 0, "files_verified": 0, "files_absent": 0}
        rows = connection.execute("SELECT id, file_path, file_hash FROM document").fetchall()
    finally:
        connection.close()
    verified = absent = 0
    for row_id, stored, recorded in rows:
        path = documents / _document_name(stored)
        if not path.is_file():
            absent += 1  # deleted before the backup (Part 7 lifecycle): knowledge kept, file gone
            continue
        if _sha256_file(path).lower() != str(recorded).lower():
            raise BackupError("A document in this backup does not match its recorded fingerprint.",
                              f"{row_id}: {path.name}")
        verified += 1
    return {"documents": len(rows), "files_verified": verified, "files_absent": absent}


def _move_aside(paths: list[Path], holder: Path, root: Path) -> list[tuple[Path, Path]]:
    moved = []
    for path in paths:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()  # an empty folder holds nothing to keep
            continue
        if path.exists():
            target = holder / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
            moved.append((target, path))
    return moved


def restore_knowledge(layout: PathLayout, backup: Path, *, replace_existing: bool = False) -> RestoreReport:
    """Restore a backup into this installation's data folder, replacing what is there only if allowed.

    Every connection to the knowledge database must be closed.
    """
    contents = inspect_backup(backup)
    existing = knowledge_base_exists(layout)
    if existing and not replace_existing:
        raise BackupError("This installation already has a knowledge base.",
                          "Restoring would replace it. Confirm the replacement to continue; the current "
                          "knowledge base is then kept in the backups folder.")
    data_root = layout.data_root
    data_root.mkdir(parents=True, exist_ok=True)
    staging = data_root / f".restore-{_stamp()}"
    staging.mkdir()
    try:
        _extract(Path(backup), contents, staging)
        staged_db = staging / "database" / "knowledge.db"
        staged_documents = staging / "documents"
        staged_documents.mkdir(exist_ok=True)
        try:
            migrated_from, counts = _prepare_database(staged_db, contents, layout.documents_dir)
            verified = _verify_documents(staged_db, staged_documents)
        except (sqlite3.Error, RudraError) as exc:
            raise BackupError("The database in this backup is damaged or cannot be used; nothing was changed.",
                              f"{type(exc).__name__}: {exc}") from exc
        # ---- replace: the current knowledge base is moved aside, never deleted
        live_db = database_path(layout, DatabaseRole.KNOWLEDGE)
        index_db = database_path(layout, DatabaseRole.INDEX)
        staged_extracted = staging / "extracted"
        current = [live_db, *(live_db.with_name(live_db.name + s) for s in SIDECARS), layout.documents_dir,
                   index_db, *(index_db.with_name(index_db.name + s) for s in SIDECARS)]
        if staged_extracted.is_dir():
            current.append(layout.extracted_dir)
        holder = layout.backups_dir / f"pre-restore-{_stamp()}"
        moved: list[tuple[Path, Path]] = []
        placed: list[Path] = []
        try:
            if any(path.exists() for path in current):
                holder.mkdir(parents=True)
                moved = _move_aside(current, holder, data_root)
            live_db.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged_db), str(live_db))
            placed.append(live_db)
            shutil.move(str(staged_documents), str(layout.documents_dir))
            placed.append(layout.documents_dir)
            if staged_extracted.is_dir():
                shutil.move(str(staged_extracted), str(layout.extracted_dir))
                placed.append(layout.extracted_dir)
        except (OSError, shutil.Error) as exc:
            for path in placed:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.exists():
                    path.unlink()
            for saved, original in moved:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(saved), str(original))
            raise BackupError("The backup could not be put in place; the previous knowledge base was put back.",
                              f"{type(exc).__name__}: {exc}") from exc
        # ---- check the result where it now lives
        connection = connect(live_db)
        try:
            integrity = _integrity(connection)
            final_schema = schema_version(connection)
            final_counts = _table_counts(connection)
        finally:
            connection.close()
        if integrity != "ok" or final_schema != CODE_SCHEMA_VERSION or final_counts != counts:
            raise BackupError("The restored knowledge base did not pass its final check.",
                              f"integrity {integrity}, schema {final_schema}. The previous knowledge base is in "
                              f"{holder if moved else 'nowhere (there was none)'}.")
        verified = dict(verified, integrity=integrity, schema_version=final_schema,
                        knowledge_objects=final_counts.get("knowledge_object", 0),
                        concepts=final_counts.get("concept", 0),
                        provenance_links=final_counts.get("source_occurrence", 0))
        return RestoreReport(contents, migrated_from, existing, holder if moved else None,
                             contents.document_count, verified)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
