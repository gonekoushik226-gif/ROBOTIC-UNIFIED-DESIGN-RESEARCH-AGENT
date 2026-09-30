"""Structured knowledge retrieval (L2, Phase 9 - Part 5 sections 198-199).

ADRs 0035-0037. The query assembly and its typed read models; every row it reads
comes through `app.storage`, which holds all the SQL (P9-32):

    requests    the structured request: modes, source scope, filters (P9-2, P9-23)
    results     the typed result and answer-trace read models; canonical JSON
    scope       authorisation, "my books", evidence filters, withheld counts (P9-5)
    provenance  source, document, run and declared-edition context (P9-18, P9-20)
    concepts    concept mode: exact identity, direction-aware groups, D1 (P9-11 ... P9-13)
    traversal   one-hop stored edges, PARENT_OF closures, rule-R1 labels (P9-14)
    conflicts   conflicts, assessment records, superseded objects (P9-15 ... P9-17)
    widening    semantic retrieval as deterministic widening (D2, P9-4)
    pages       page retrieval from stored text (P9-21)
    exact       exact search by identifier (P9-24)
    index       the derived FTS5 index: explicit build and read-back verification
                (P9-26 ... P9-29)
    keyword     keyword search over a verified fresh index (P9-25, P9-30)
    engine      the facade, dispatch and the answer trace (P9-19)

**Read-only and provider-free**: queries never write, commit or persist; no model,
embedding, vector store, score or network; no dependency. The one writer is the
explicit `build_index`, and it writes only the derived index file.

Boundaries (P9-32, enforced in `tests/unit/test_import_boundaries.py`): may import
`app.version`, `app.models`, `app.core`, `app.storage`, `app.knowledge` and
`app.deduplication` (whose `review` counts sources); never the parser or the
extractor.
"""

from app.query.engine import QueryEngine
from app.query.index import build_index, verify_index
from app.query.requests import EXACT_KINDS, QueryFilters, QueryMode, QueryRequest, SourceScope
from app.query.results import (
    AnswerStatus,
    AnswerTrace,
    IndexState,
    QueryResult,
    Withheld,
    as_plain,
    to_json,
)

__all__ = [
    "AnswerStatus",
    "AnswerTrace",
    "EXACT_KINDS",
    "IndexState",
    "QueryEngine",
    "QueryFilters",
    "QueryMode",
    "QueryRequest",
    "QueryResult",
    "SourceScope",
    "Withheld",
    "as_plain",
    "build_index",
    "to_json",
    "verify_index",
]
