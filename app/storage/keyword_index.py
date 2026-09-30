"""The derived keyword index, `data/indexes/index.db` (ADR 0037 P9-25 ... P9-30; ADR 0004 Amendment 1).

All SQL of the derived index lives here, with the read-only statements that feed its
build and compute its freshness marker from `knowledge.db` (P9-32: SQL only in
`app.storage`). Building, verifying and searching are orchestrated by `app.query`.

**Layout of `index.db`:**

    index_metadata(key, value)   format, tokenizer, normalisation version, entry
                                 counts and - written last - the build marker
    entry(id, kind, item_id)     one row per indexed stored text; `id` is the FTS rowid
    entry_text                   FTS5, contentless: the D-30 form of each entry's text
    page(id, segment_id)         one row per indexed page segment
    page_text                    FTS5, contentless: the D-30 form of each page's text

**Contentless** (`content=''`): the FTS tables hold tokens, never the text, so the index
cannot become a second copy of the knowledge; every hit is an identifier, and the
caller re-reads the authoritative row from `knowledge.db` (P9-25).

**What is indexed** (P9-25): knowledge-object statements, source-occurrence text and
concept aliases (`entry`), and page text in its own table (`page`).

**Tokenizer** (P9-30; the Step 0 probe, `docs/phases/PHASE_9.md` section 13.3):
`unicode61 remove_diacritics 0`, the text and every query term passed through D-30
first, each term one quoted FTS5 string, a prefix only as `"term"*`.

**Freshness** (P9-27): the build marker is `state_marker` of `knowledge.db` at build
time - its instance, schema version, and for every covered table its row count, its
identifier counter and its latest `updated_at`. ADR 0037's conditions make it change
whenever a covered row is inserted or updated: every insert allocates from
`id_sequence`, every update refreshes `updated_at`, nothing deletes (the row count is
there in case a later phase does).
"""

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from app.storage.connection import read_metadata as read_database_metadata
from app.storage.queries import _counter

#: The derived index's own format. Bumped with any change to its tables or rules.
FORMAT_VERSION = 1

#: The FTS5 tokenizer (P9-30), exactly as it appears in the tables' definitions.
TOKENIZER = "unicode61 remove_diacritics 0"

#: Entry kinds, in index order.
KNOWLEDGE_OBJECT = "KNOWLEDGE_OBJECT"
SOURCE_OCCURRENCE = "SOURCE_OCCURRENCE"
CONCEPT_ALIAS = "CONCEPT_ALIAS"
ENTRY_KINDS = (KNOWLEDGE_OBJECT, SOURCE_OCCURRENCE, CONCEPT_ALIAS)

#: The `knowledge.db` tables the index covers, and their identifier-counter kind.
COVERED_TABLES: tuple[tuple[str, str], ...] = (
    ("knowledge_object", "K"),
    ("source_occurrence", "S"),
    ("concept_alias", "CA"),
    ("document_segment", "SEG"),
)

#: The text each entry kind indexes: (kind, table, text column).
_ENTRY_SOURCES = (
    (KNOWLEDGE_OBJECT, "knowledge_object", "statement"),
    (SOURCE_OCCURRENCE, "source_occurrence", "original_text"),
    (CONCEPT_ALIAS, "concept_alias", "alias"),
)

_TABLES = (
    "CREATE TABLE index_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT",
    "CREATE TABLE entry (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, item_id TEXT NOT NULL) STRICT",
    f"CREATE VIRTUAL TABLE entry_text USING fts5(body, content='', tokenize='{TOKENIZER}')",
    "CREATE TABLE page (id INTEGER PRIMARY KEY, segment_id TEXT NOT NULL) STRICT",
    f"CREATE VIRTUAL TABLE page_text USING fts5(body, content='', tokenize='{TOKENIZER}')",
)

#: The definitions the verifier expects to read back from `sqlite_master`.
FTS_DEFINITIONS = {
    "entry_text": _TABLES[2],
    "page_text": _TABLES[4],
}

_BATCH = 500


# ------------------------------------------------------------ knowledge.db, read-only


@contextmanager
def read_snapshot(connection: sqlite3.Connection) -> Iterator[None]:
    """One read transaction, so every read inside it sees the same database state.

    Nothing is written: the transaction is rolled back. Inside a transaction the
    caller already holds, it adds nothing.
    """
    if connection.in_transaction:
        yield
        return
    connection.execute("BEGIN")
    try:
        yield
    finally:
        connection.rollback()


def state_marker(connection: sqlite3.Connection) -> dict:
    """The state of the covered `knowledge.db` rows, read-only (P9-27)."""
    counters = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT entity_kind, next_value FROM id_sequence WHERE entity_kind IN (?, ?, ?, ?)",
            tuple(kind for _, kind in COVERED_TABLES),
        )
    }
    tables = {}
    for table, kind in COVERED_TABLES:
        rows, last = connection.execute(
            f"SELECT count(*), max(updated_at) FROM {table}"
        ).fetchone()
        tables[table] = {"rows": int(rows), "next_id": counters.get(kind), "last_updated": last}
    return {
        "instance_id": read_database_metadata(connection).get("instance_id"),
        "schema_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        "tables": tables,
    }


def marker_text(marker: dict) -> str:
    """The one canonical text of a marker, so equal states compare equal as text."""
    return json.dumps(marker, sort_keys=True, separators=(",", ":"))


def text_volume(connection: sqlite3.Connection) -> tuple[int, int]:
    """(rows, UTF-8 bytes) of the text the index would hold - the section 158 estimate's input."""
    rows = volume = 0
    for _, table, column in _ENTRY_SOURCES + ((None, "document_segment", "text"),):
        count, size = connection.execute(
            f"SELECT count(*), coalesce(sum(length(CAST({column} AS BLOB))), 0) FROM {table}"
        ).fetchone()
        rows, volume = rows + int(count), volume + int(size)
    return rows, volume


def entry_texts(connection: sqlite3.Connection) -> Iterator[tuple[str, str, str]]:
    """(kind, id, stored text) of every indexed row: kind order, then numeric counter."""
    for kind, table, column in _ENTRY_SOURCES:
        cursor = connection.execute(
            f"SELECT id, {column} FROM {table} ORDER BY {_counter('id')}"
        )
        while batch := cursor.fetchmany(_BATCH):
            for identifier, text in batch:
                yield kind, str(identifier), str(text)


def segment_texts(connection: sqlite3.Connection) -> Iterator[tuple[str, str]]:
    """(segment id, stored page text) of every page segment, by numeric counter."""
    cursor = connection.execute(
        f"SELECT id, text FROM document_segment ORDER BY {_counter('id')}"
    )
    while batch := cursor.fetchmany(_BATCH):
        for identifier, text in batch:
            yield str(identifier), str(text)


# ------------------------------------------------------------ index.db, build


def create_schema(connection: sqlite3.Connection) -> None:
    for statement in _TABLES:
        connection.execute(statement)


def insert_entries(connection: sqlite3.Connection, rows: Iterable[tuple[int, str, str, str]]) -> None:
    """Store (rowid, kind, item id, normalised text) entries, streamed in batches."""
    _insert_pairs(
        connection,
        rows,
        "INSERT INTO entry (id, kind, item_id) VALUES (?, ?, ?)",
        "INSERT INTO entry_text (rowid, body) VALUES (?, ?)",
        lambda r: ((r[0], r[1], r[2]), (r[0], r[3])),
    )


def insert_pages(connection: sqlite3.Connection, rows: Iterable[tuple[int, str, str]]) -> None:
    """Store (rowid, segment id, normalised text) pages, streamed in batches."""
    _insert_pairs(
        connection,
        rows,
        "INSERT INTO page (id, segment_id) VALUES (?, ?)",
        "INSERT INTO page_text (rowid, body) VALUES (?, ?)",
        lambda r: ((r[0], r[1]), (r[0], r[2])),
    )


def _insert_pairs(connection, rows, into_ids: str, into_text: str, split) -> None:
    ids: list = []
    texts: list = []
    for row in rows:
        first, second = split(row)
        ids.append(first)
        texts.append(second)
        if len(ids) >= _BATCH:
            connection.executemany(into_ids, ids)
            connection.executemany(into_text, texts)
            ids, texts = [], []
    if ids:
        connection.executemany(into_ids, ids)
        connection.executemany(into_text, texts)


def write_metadata(connection: sqlite3.Connection, values: dict[str, str]) -> None:
    connection.executemany(
        "INSERT OR REPLACE INTO index_metadata (key, value) VALUES (?, ?)",
        sorted(values.items()),
    )


# ------------------------------------------------------------ index.db, read back


def read_metadata(connection: sqlite3.Connection) -> dict[str, str] | None:
    """The index's metadata, or None when the file holds no RUDRA index."""
    try:
        rows = connection.execute("SELECT key, value FROM index_metadata").fetchall()
    except sqlite3.DatabaseError:
        return None
    return {str(row[0]): str(row[1]) for row in rows}


def fts_definitions(connection: sqlite3.Connection) -> dict[str, str]:
    """The stored definitions of the FTS tables, as SQLite keeps them."""
    return {
        str(row[0]): str(row[1])
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN ('entry_text', 'page_text')"
        )
    }


def stored_counts(connection: sqlite3.Connection) -> dict[str, int]:
    """Row counts read back from every index table, entries per kind."""
    counts = {kind: 0 for kind in ENTRY_KINDS}
    for kind, count in connection.execute("SELECT kind, count(*) FROM entry GROUP BY kind"):
        counts[str(kind)] = int(count)
    counts["entry_text"] = int(connection.execute("SELECT count(*) FROM entry_text").fetchone()[0])
    counts["page"] = int(connection.execute("SELECT count(*) FROM page").fetchone()[0])
    counts["page_text"] = int(connection.execute("SELECT count(*) FROM page_text").fetchone()[0])
    return counts


def entry_rows(connection: sqlite3.Connection) -> tuple[tuple[int, str, str], ...]:
    """Every (rowid, kind, item id) entry, by rowid - for comparing two builds."""
    return tuple(
        (int(r[0]), str(r[1]), str(r[2]))
        for r in connection.execute("SELECT id, kind, item_id FROM entry ORDER BY id")
    )


def page_rows(connection: sqlite3.Connection) -> tuple[tuple[int, str], ...]:
    return tuple(
        (int(r[0]), str(r[1]))
        for r in connection.execute("SELECT id, segment_id FROM page ORDER BY id")
    )


# ------------------------------------------------------------ index.db, search


def match_expression(normalized_term: str, *, prefix: bool) -> str:
    """The FTS5 expression for one D-30-normalised term (P9-30).

    The whole term is one quoted FTS5 string - an inner double quote is doubled - so
    no user text is ever read as FTS5 syntax (operators, column filters, NEAR); several
    words form a phrase. A prefix is the quoted string followed by `*`.
    """
    quoted = '"' + normalized_term.replace('"', '""') + '"'
    return quoted + "*" if prefix else quoted


def search_entries(connection: sqlite3.Connection, expression: str) -> tuple[tuple[str, str], ...]:
    """(kind, item id) of every entry the expression matches, by rowid. No score is read."""
    return tuple(
        (str(r[0]), str(r[1]))
        for r in connection.execute(
            "SELECT e.kind, e.item_id FROM entry_text JOIN entry e ON e.id = entry_text.rowid "
            "WHERE entry_text MATCH ? ORDER BY e.id",
            (expression,),
        )
    )


def search_pages(connection: sqlite3.Connection, expression: str) -> tuple[str, ...]:
    """The segment id of every page the expression matches, by rowid. No score is read."""
    return tuple(
        str(r[0])
        for r in connection.execute(
            "SELECT p.segment_id FROM page_text JOIN page p ON p.id = page_text.rowid "
            "WHERE page_text MATCH ? ORDER BY p.id",
            (expression,),
        )
    )
