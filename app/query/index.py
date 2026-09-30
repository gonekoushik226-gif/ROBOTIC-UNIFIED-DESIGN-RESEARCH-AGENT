"""The derived keyword index: explicit build and read-back verification (ADR 0037 P9-26 ... P9-29).

**Build** (`build_index`, P9-28) - only ever called explicitly; no query builds or
repairs an index. From `knowledge.db` opened read-only, inside one read snapshot:

1. refuse a `knowledge.db` whose schema version is not this build's;
2. estimate the space the index needs and refuse, before writing anything, when the
   disk lacks it (section 158): 3 x the UTF-8 bytes of the text indexed, plus 64
   bytes a row, plus 64 KiB - a conservative estimate, to be measured (P9-33);
3. build a complete index in `index.db.building` beside the target: the D-30 form of
   every stored text, the metadata, and - **last** - the build marker (P9-27);
4. read it back and verify it; only a verified index replaces `index.db`, by one
   atomic rename. A failed or interrupted build leaves the previous `index.db` as it
   was - fresh only if its own marker still matches - and removes its temporary file.

**Verify** (`verify_index`, `open_fresh_index`, P9-27, P9-29): an index is used only
when all of these hold, each read back from the file, never assumed:

* it is a RUDRA derived index of this build's format, tokenizer and D-30 version, and
  its FTS tables are defined exactly as this build defines them;
* it carries a build marker (the build finished) and its tables hold exactly the row
  counts its metadata records;
* its build marker equals the state marker computed read-only from `knowledge.db` now.

Otherwise it is MISSING, UNREADABLE, INCOMPATIBLE, INCOMPLETE or STALE, and keyword
search refuses with the reason. `index.db` keeps WAL like every RUDRA database
(ADR 0004); a reader may leave empty `-wal`/`-shm` files beside it in
`data/indexes/`, which a build removes before it replaces the file.
"""

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from app.core.errors import ResourceLimitError, StorageError
from app.models.enums import ExtractionRunStatus
from app.models.naming import NORMALIZATION_VERSION, normalize_alias
from app.query.results import DocumentIngestion, IndexBuildReport, IndexState, IndexStatus
from app.storage import CODE_SCHEMA_VERSION, DatabaseRole, connect, queries, schema_version
from app.storage import keyword_index as store

#: The section 158 estimate (see the module docstring).
ESTIMATE_TEXT_FACTOR = 3
ESTIMATE_PER_ROW = 64
ESTIMATE_BASE = 65_536

_SIDECARS = ("-wal", "-shm", "-journal")
_STAGE = "query.index"
_REBUILD = (
    "Rebuild the derived index explicitly from knowledge.db; a query never builds or "
    "repairs it.",
    "Concept, exact and page queries do not need the index.",
)


def _searchable(text: str) -> str | None:
    """The D-30 form of a stored text, or None when nothing searchable remains."""
    try:
        return normalize_alias(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------- build


def build_index(
    knowledge: sqlite3.Connection, index_path: str | Path, *, disk_usage=shutil.disk_usage
) -> IndexBuildReport:
    """Build the derived index from `knowledge.db` and put it in place (P9-28)."""
    started = time.perf_counter()
    target = Path(index_path)
    version = schema_version(knowledge)
    if version != CODE_SCHEMA_VERSION:
        raise StorageError.of(
            "The knowledge database's schema version is not the one this build reads; "
            "no index is built from it.",
            f"Its schema version is {version}; this build reads version {CODE_SCHEMA_VERSION}.",
            stage=_STAGE,
            data_changed=False,
            retry_safe=True,
            next_options=("Check that the project root names the intended project.",),
        )
    if not target.parent.is_dir():
        raise StorageError.of(
            "The index directory does not exist.",
            f"{target.parent} is missing; nothing was written.",
            stage=_STAGE,
            missing=(str(target.parent),),
            data_changed=False,
            retry_safe=True,
            next_options=("Run 'python -m app' to create the directory layout.",),
        )
    building = target.with_name(target.name + ".building")
    with store.read_snapshot(knowledge):
        marker = store.marker_text(store.state_marker(knowledge))
        rows, volume = store.text_volume(knowledge)
        estimate = ESTIMATE_TEXT_FACTOR * volume + ESTIMATE_PER_ROW * rows + ESTIMATE_BASE
        available = int(disk_usage(target.parent).free)
        if available < estimate:
            raise ResourceLimitError.of(
                "There is not enough free disk space to build the derived index.",
                f"The build needs about {estimate} bytes and {available} are free; nothing was written.",
                stage=_STAGE,
                detail=f"estimate={estimate} available={available} output={target}",
                data_changed=False,
                retry_safe=True,
                next_options=("Free disk space on the drive that holds data/indexes, then build again.",),
            )
        _remove(building)
        try:
            connection = connect(building, role=DatabaseRole.INDEX)
            try:
                skipped = _fill(connection, knowledge)
                connection.commit()
                store.write_metadata(connection, {"build_marker": marker})  # last (P9-27)
                connection.commit()
                status = _verify(connection, str(target), marker)
            finally:
                connection.close()
            if status.state is not IndexState.FRESH:
                raise StorageError.of(
                    "The derived index did not verify after it was built; it was not put in place.",
                    status.reason,
                    stage=_STAGE,
                    detail=status.state.value,
                    data_changed=False,
                    retry_safe=True,
                    next_options=_REBUILD[:1],
                )
            _replace(building, target)
        finally:
            _remove(building)
    return IndexBuildReport(
        path=str(target),
        status=status,
        skipped_empty=skipped,
        estimate_bytes=estimate,
        available_bytes=available,
        size_bytes=target.stat().st_size,
        seconds=time.perf_counter() - started,
    )


def _fill(connection: sqlite3.Connection, knowledge: sqlite3.Connection) -> int:
    """Write the tables and metadata (all but the marker). Returns the rows skipped as empty."""
    counts = {kind: 0 for kind in store.ENTRY_KINDS}
    tally = {"skipped": 0, "pages": 0}

    def entries():
        rowid = 0
        for kind, identifier, text in store.entry_texts(knowledge):
            body = _searchable(text)
            if body is None:
                tally["skipped"] += 1
                continue
            rowid += 1
            counts[kind] += 1
            yield rowid, kind, identifier, body

    def pages():
        for identifier, text in store.segment_texts(knowledge):
            body = _searchable(text)
            if body is None:
                tally["skipped"] += 1
                continue
            tally["pages"] += 1
            yield tally["pages"], identifier, body

    store.create_schema(connection)
    store.insert_entries(connection, entries())
    store.insert_pages(connection, pages())
    store.write_metadata(
        connection,
        {
            "format_version": str(store.FORMAT_VERSION),
            "tokenizer": store.TOKENIZER,
            "normalization_version": str(NORMALIZATION_VERSION),
            "entries": json.dumps(counts, sort_keys=True),
            "pages": str(tally["pages"]),
        },
    )
    return tally["skipped"]


def _remove(path: Path) -> None:
    for candidate in (path, *(path.with_name(path.name + s) for s in _SIDECARS)):
        candidate.unlink(missing_ok=True)


def _replace(building: Path, target: Path) -> None:
    """Swap the verified build in by one rename; an old reader's sidecars go first."""
    try:
        for suffix in _SIDECARS:
            target.with_name(target.name + suffix).unlink(missing_ok=True)
        os.replace(building, target)
    except OSError as exc:
        raise StorageError.of(
            "The new derived index could not replace the old one; the old one is unchanged.",
            f"{type(exc).__name__}: {exc}",
            stage=_STAGE,
            detail=str(target),
            data_changed=False,
            retry_safe=True,
            cause=repr(exc),
            next_options=("Close any program that has the index open, then build again.",),
        ) from exc


# --------------------------------------------------------------------- verify


def verify_index(knowledge: sqlite3.Connection, index_path: str | Path | None) -> IndexStatus:
    """The derived index as read back and compared with `knowledge.db` now. Reads only."""
    status, connection = _open(knowledge, index_path)
    if connection is not None:
        connection.close()
    return status


def open_fresh_index(
    knowledge: sqlite3.Connection, index_path: str | Path | None
) -> tuple[sqlite3.Connection, IndexStatus]:
    """A read-only connection to a verified FRESH index, or a refusal with the reason (P9-29).

    The caller closes the connection. Nothing is built or repaired here.
    """
    status, connection = _open(knowledge, index_path)
    if connection is None or status.state is not IndexState.FRESH:
        if connection is not None:
            connection.close()
        raise StorageError.of(
            f"Keyword search needs a fresh derived index; the index is {status.state.value}.",
            status.reason,
            stage="query.keyword.index",
            detail=f"{status.state.value}: {status.path}",
            data_changed=False,
            retry_safe=True,
            next_options=_REBUILD,
        )
    return connection, status


def _open(knowledge, index_path) -> tuple[IndexStatus, sqlite3.Connection | None]:
    path = "" if index_path is None else str(index_path)
    if index_path is None or not Path(index_path).is_file():
        return IndexStatus(
            path=path,
            state=IndexState.MISSING,
            reason=f"No derived index exists at {path or '(no index path given)'}.",
        ), None
    try:
        connection = connect(index_path, role=DatabaseRole.INDEX, read_only=True)
    except StorageError as exc:
        return IndexStatus(path=path, state=IndexState.UNREADABLE, reason=exc.report.reason), None
    try:
        with store.read_snapshot(knowledge):
            current = store.marker_text(store.state_marker(knowledge))
        status = _verify(connection, path, current)
    except sqlite3.DatabaseError as exc:
        connection.close()
        return IndexStatus(
            path=path, state=IndexState.UNREADABLE, reason=f"{type(exc).__name__}: {exc}"
        ), None
    if status.state is not IndexState.FRESH:
        connection.close()
        return status, None
    return status, connection


def _verify(connection: sqlite3.Connection, path: str, expected_marker: str) -> IndexStatus:
    """Read the index back and compare it with a state marker (P9-27)."""
    metadata = store.read_metadata(connection)
    if metadata is None:
        return IndexStatus(path=path, state=IndexState.UNREADABLE,
                           reason="The file holds no RUDRA derived index (no index metadata).")
    fields = {
        "path": path,
        "format_version": _number(metadata.get("format_version")),
        "tokenizer": metadata.get("tokenizer"),
        "normalization_version": _number(metadata.get("normalization_version")),
        "build_marker": metadata.get("build_marker"),
        "state_marker": expected_marker,
    }
    differs = [
        f"{name} {found!r} (this build: {wanted!r})"
        for name, found, wanted in (
            ("format", fields["format_version"], store.FORMAT_VERSION),
            ("tokenizer", fields["tokenizer"], store.TOKENIZER),
            ("D-30 normalisation version", fields["normalization_version"], NORMALIZATION_VERSION),
        )
        if found != wanted
    ]
    if not differs and store.fts_definitions(connection) != store.FTS_DEFINITIONS:
        differs.append("FTS table definitions differ from this build's")
    if differs:
        return IndexStatus(state=IndexState.INCOMPATIBLE,
                           reason="Built differently from this build: " + "; ".join(differs) + ".",
                           **fields)
    try:
        entries = {str(k): int(v) for k, v in json.loads(metadata["entries"]).items()}
        pages = int(metadata["pages"])
    except (KeyError, TypeError, ValueError):
        return IndexStatus(state=IndexState.INCOMPLETE,
                           reason="Its metadata records no entry or page counts.", **fields)
    fields.update(entries=tuple(sorted(entries.items())), pages=pages)
    if fields["build_marker"] is None:
        return IndexStatus(state=IndexState.INCOMPLETE,
                           reason="It has no build marker: its build did not finish.", **fields)
    stored = store.stored_counts(connection)
    wanted = {**{kind: entries.get(kind, 0) for kind in store.ENTRY_KINDS},
              "entry_text": sum(entries.values()), "page": pages, "page_text": pages}
    if stored != wanted:
        mismatched = ", ".join(f"{k} {stored[k]} (recorded {wanted[k]})" for k in sorted(wanted) if stored[k] != wanted[k])
        return IndexStatus(state=IndexState.INCOMPLETE,
                           reason=f"Its tables do not hold the counts it records: {mismatched}.", **fields)
    if fields["build_marker"] != expected_marker:
        return IndexStatus(state=IndexState.STALE, reason=_stale_reason(fields["build_marker"], expected_marker),
                           **fields)
    return IndexStatus(
        state=IndexState.FRESH,
        reason=(
            "Its build marker equals the state marker computed read-only from knowledge.db, "
            "and its tables read back the counts it records."
        ),
        **fields,
    )


def _stale_reason(built: str, current: str) -> str:
    """Which parts of `knowledge.db` changed since the build - named, in a fixed order."""
    try:
        old, new = json.loads(built), json.loads(current)
    except ValueError:
        return "Its build marker cannot be read."
    changed = []
    if old.get("instance_id") != new.get("instance_id"):
        changed.append("it was built from another knowledge database")
    if old.get("schema_version") != new.get("schema_version"):
        changed.append("the schema version changed")
    tables = sorted(set(old.get("tables", {})) | set(new.get("tables", {})))
    changed.extend(
        f"{table} changed"
        for table in tables
        if old.get("tables", {}).get(table) != new.get("tables", {}).get(table)
    )
    return "knowledge.db changed since the index was built: " + "; ".join(changed or ["its state differs"]) + "."


# ----------------------------------------------------------- fully ingested


#: Stored processing statuses that mean the document was not parsed (P8-5).
_UNPARSED = ("PENDING", "PROCESSING", "FAILED")
#: The first extractor version with stage 15 (ADR 0032 P8-6, P8-7).
_FIRST_STAGE_15_EXTRACTOR = 4


def document_ingestion(knowledge: sqlite3.Connection, status: IndexStatus) -> tuple[DocumentIngestion, ...]:
    """Every stored document's "fully ingested" state (P8-6, P9-10). Reads only; stores nothing."""
    indexed = status.state is IndexState.FRESH
    reports = []
    for document in queries.all_documents(knowledge):
        processing = str(document.processing_status)
        parsed = processing not in _UNPARSED
        runs = tuple(
            run.id
            for run in queries.runs_for_document(knowledge, document.id)
            if run.status is ExtractionRunStatus.COMPLETED and _number(run.extractor_version) is not None
            and _number(run.extractor_version) >= _FIRST_STAGE_15_EXTRACTOR
        )
        missing = [
            text
            for present, text in (
                (parsed, f"not parsed (processing status {processing})"),
                (bool(runs), "no COMPLETED extraction run at extractor version 4 or later"),
                (indexed, f"the derived index is {status.state.value}"),
            )
            if not present
        ]
        reports.append(DocumentIngestion(
            document_id=document.id,
            filename=document.filename,
            processing_status=processing,
            parsed=parsed,
            qualifying_runs=runs,
            indexed=indexed,
            fully_ingested=not missing,
            reason="fully ingested" if not missing else "not fully ingested: " + "; ".join(missing),
        ))
    return tuple(reports)


def _number(value: str | None) -> int | None:
    try:
        return None if value is None else int(value)
    except ValueError:
        return None
