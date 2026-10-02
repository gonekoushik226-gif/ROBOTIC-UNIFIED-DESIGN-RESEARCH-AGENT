"""The query engine facade (ADRs 0035-0037).

`QueryEngine(connection, database_path=..., index_path=...).run(request)` checks the
request, refuses a database whose schema version is not this build's (it never
migrates, as `lookup` never does, ADR 0031 I7-B), dispatches the mode, and returns a
`QueryResult` with its `AnswerTrace` (ADR 0036 P9-19).

**Read-only.** The engine never writes, commits or persists anything - not the trace,
not a query row. Whether the connection itself refuses writes is read from it and
stated in the trace; the `query` command (a later step) opens `knowledge.db` with
`read_only=True`.

**Only keyword mode uses the derived index** (ADR 0037 P9-29), and only after it is
verified fresh; a missing or stale index makes keyword mode refuse, and nothing here
builds or repairs one. Concept, exact and page modes read `knowledge.db` only and
never open the index.

**Deterministic** (P9-22): fixed group order, stored-attribute and numeric-counter
ordering, no score, no clock; `to_json` of equal results is byte-identical.
"""

import sqlite3
from dataclasses import fields, is_dataclass
from pathlib import Path

from app.core.errors import StorageError
from app.query.concepts import build_section, resolve
from app.query.exact import exact_result
from app.query.keyword import index_note, keyword_mode
from app.query.pages import page_result
from app.query.provenance import cited_rows, run_notes, sorted_ids
from app.query.requests import QueryMode, QueryRequest
from app.query.results import (
    AnswerStatus,
    AnswerTrace,
    ConceptSection,
    DatabaseState,
    EdgeItem,
    KnowledgeItem,
    QueryResult,
    TraceConcept,
    TraceLink,
)
from app.query.scope import QueryContext
from app.query.widening import possible_equivalents
from app.storage import CODE_SCHEMA_VERSION, schema_version
from app.storage import queries
from app.storage.repository import Repository

INDEX_NOT_USED = (
    "not used: this mode reads knowledge.db only and never opens the derived index"
)
EXACT_IDENTITY = (
    "Only concepts named exactly so were resolved: the name is matched by D-30 "
    "normalisation (NFKC, case folding, whitespace) against every ACTIVE alias, with no "
    "plural folding or stemming; a differently named concept is a different concept "
    "unless a source states it equivalent."
)
D1_SCOPE = (
    "Equations, variables and examples - and units, rules and procedures - are listed "
    "only where a stored edge links them to a concept; extraction records no such edge, "
    "so section 199 is PARTIALLY IMPLEMENTED for those categories."
)
NOT_WIDENED = "Widening over stored equivalence data was not requested."


class QueryEngine:
    """Structured retrieval over one open `knowledge.db` connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        database_path: str | Path,
        index_path: str | Path | None = None,
    ) -> None:
        self._connection = connection
        self._repository = Repository(connection)
        self._database_path = str(database_path)
        #: `data/indexes/index.db` for keyword mode; None means no index is configured.
        self._index_path = index_path

    def database_state(self) -> DatabaseState:
        """The database as read from the connection; refuses a schema this build cannot read."""
        version = schema_version(self._connection)
        if version != CODE_SCHEMA_VERSION:
            raise StorageError.of(
                "The knowledge database's schema version is not the one this build reads; "
                "a query never migrates.",
                f"Its schema version is {version}; this build reads version {CODE_SCHEMA_VERSION}.",
                stage="query.schema",
                detail=self._database_path,
                data_changed=False,
                retry_safe=True,
                next_options=(
                    "Check that the project root names the intended project.",
                    "Migrate explicitly with: python -m app db  (this writes to the database; "
                    "for the live database make a fresh D-15 backup first).",
                ),
            )
        return DatabaseState(
            path=self._database_path,
            schema_version=version,
            read_only=queries.connection_is_read_only(self._connection),
        )

    def run(self, request: QueryRequest) -> QueryResult:
        """Answer one request. Reads only."""
        request = request.check()
        database = self.database_state()
        context = QueryContext(self._repository, request)
        if request.mode is QueryMode.CONCEPT:
            parts = _concept_mode(context)
        elif request.mode is QueryMode.EXACT:
            parts = _exact_mode(context)
        elif request.mode is QueryMode.KEYWORD:
            parts = keyword_mode(context, self._index_path)
        else:
            status, message, page = page_result(context, request.document_id, request.page_number)
            notes = () if page is None else run_notes(page.provenance)
            parts = {"status": status, "message": message, "page": page, "notes": notes}
        withheld = context.withheld()
        return QueryResult(
            request=request,
            trace=_trace(request, database, parts, withheld),
            withheld=withheld,
            **parts,
        )


def _concept_mode(context: QueryContext) -> dict:
    name, normalized = context.request.name, context.request.normalized_name
    candidates = resolve(context, normalized)
    what = f"the exact name {name!r} (normalised {normalized!r})"
    plural_note: tuple[str, ...] = ()
    singular = _singular(normalized)
    if not candidates and singular:
        # "MOSFETs" asks about the concept "MOSFET": one fixed English rule, stated in the notes.
        candidates = resolve(context, singular)
        if candidates:
            plural_note = (f"No concept is named {normalized!r}; its singular {singular!r} was used.",)
    if not candidates:
        return {
            "status": AnswerStatus.NOT_FOUND,
            "message": f"No concept answers to {what}; absence is the answer and nothing was guessed.",
            "notes": (EXACT_IDENTITY,),
        }
    section = build_section(context, candidates)
    if section is None:
        status, message = context.withheld_answer(
            (concept.id for concept, _ in candidates),
            f"{len(candidates)} concept(s) answer to {what}",
        )
        return {"status": status, "message": message, "notes": (EXACT_IDENTITY,)}
    shown = len(section.concepts)
    message = f"{shown} concept(s) answer to {what}"
    if shown < len(candidates):
        message += (
            f"; {len(candidates) - shown} other(s) answering to it are withheld "
            "(no evidence from an authorised source in scope)"
        )
    if shown > 1:
        message += "; every one is shown and none was chosen"
    return _concept_answer(context, section, message + ".", (EXACT_IDENTITY, *plural_note))


def _singular(normalized: str) -> str | None:
    """The singular of a name's last word by the regular English rules, or None if it has none."""
    words = normalized.split(" ")
    last = words[-1]
    if len(last) < 4:
        return None
    if last.endswith("ies"):
        stem = last[:-3] + "y"
    elif last.endswith(("sses", "shes", "ches", "xes", "zes")):
        stem = last[:-2]
    elif last.endswith("s") and not last.endswith(("ss", "us", "is")):
        stem = last[:-1]
    else:
        return None
    return " ".join([*words[:-1], stem])


def _exact_mode(context: QueryContext) -> dict:
    status, message, exact, section = exact_result(context, context.request.identifier)
    if section is not None:
        return _concept_answer(context, section, message, ())
    notes = () if exact is None else run_notes(exact.provenance)
    return {"status": status, "message": message, "exact": exact, "notes": notes}


def _concept_answer(context: QueryContext, section: ConceptSection, message: str, first: tuple) -> dict:
    if context.request.widen:
        equivalents, note = possible_equivalents(context, section)
    else:
        equivalents, note = (), NOT_WIDENED
    notes = list(first) + [D1_SCOPE]
    for provenance in [section.provenance] + [e.section.provenance for e in equivalents if e.section]:
        notes.extend(n for n in run_notes(provenance) if n not in notes)
    return {
        "status": AnswerStatus.FOUND,
        "message": message,
        "concept": section,
        "possible_equivalents": equivalents,
        "equivalence_note": note,
        "notes": tuple(notes),
    }


def _items(value: object, found: list) -> None:
    """Every listed item in a result part, in the order the result holds them."""
    if isinstance(value, (KnowledgeItem, EdgeItem)):
        found.append(value)
    if isinstance(value, (tuple, list)):
        for element in value:
            _items(element, found)
    elif is_dataclass(value) and not isinstance(value, type):
        for f in fields(value):
            _items(getattr(value, f.name), found)


def _trace(request: QueryRequest, database: DatabaseState, parts: dict, withheld) -> AnswerTrace:
    content = tuple(
        parts.get(key) for key in ("concept", "possible_equivalents", "exact", "page", "keyword")
    )
    keyword = parts.get("keyword")
    section = parts.get("concept")
    resolved = () if section is None else tuple(
        TraceConcept(
            concept_id=rc.concept.id,
            alias_id=None if rc.matched_alias is None else rc.matched_alias.id,
            alias=None if rc.matched_alias is None else rc.matched_alias.alias,
            normalized_alias=None if rc.matched_alias is None else rc.matched_alias.normalized_alias,
        )
        for rc in section.concepts
    )
    items: list = []
    _items(content, items)
    links: dict[tuple, TraceLink] = {}
    for item in items:
        item_id = item.knowledge.id if isinstance(item, KnowledgeItem) else item.edge.relationship.id
        for link in item.links:
            edge = link.edge.relationship
            key = (item_id, link.concept_id, edge.id, link.concept_end)
            links.setdefault(key, TraceLink(
                item_id=item_id,
                concept_id=link.concept_id,
                relationship_id=edge.id,
                relation_type=edge.relation_type,
                origin=edge.origin,
                concept_end=link.concept_end,
            ))
    return AnswerTrace(
        request=request,
        database=database,
        index=INDEX_NOT_USED if keyword is None else index_note(keyword.index),
        resolved=resolved,
        links=tuple(links.values()),
        evidence_ids=sorted_ids(row.id for row in cited_rows(content)),
        widened=tuple(e.concept.id for e in parts.get("possible_equivalents", ())),
        withheld=withheld,
    )
