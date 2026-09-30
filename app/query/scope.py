"""Source scope, evidence filters and what is withheld (ADR 0035 P9-5; ADR 0036 P9-23).

**Authorisation.** Evidence whose source is `NOT_AUTHORIZED`, or of category
`UNAUTHORIZED_SOURCE`, is never returned, in any scope (sections 49-50). "My books"
(section 51) is category `USER_PROVIDED_SOURCE` or `LOCAL_SOURCE` and `AUTHORIZED`.
Nothing here infers or changes a source's authorisation: the stored columns decide.

**Items.** An item - a concept, a knowledge object or an edge - is listed only with
evidence that survives: an item left with none is withheld and counted, never shown
(sections 67, 228). An inferred edge is scoped through the source of the occurrence
its recorded basis names (ADR 0027).

**Lifecycle** (P9-23, P7 section 24): `DELETED` and `ARCHIVED` objects are excluded
from normal retrieval; every other stored status is shown as stored, `SUPERSEDED`
labelled (P9-16) unless the request leaves superseded objects out. A lifecycle
filter replaces the default.

`QueryContext` also holds what one query has read, so each stored row is read once
and the same row always gets the same verdict. It never writes.
"""

from collections.abc import Iterable
from enum import StrEnum

from app.deduplication.review import KnowledgeReview, review_knowledge
from app.models.entities import (
    Concept,
    ExtractionRun,
    KnowledgeObject,
    Relationship,
    Source,
    SourceOccurrence,
)
from app.models.enums import Authorization, KnowledgeType, LifecycleStatus, SourceCategory
from app.models.identifiers import parse_id
from app.query.requests import QueryRequest, SourceScope
from app.query.results import AnswerStatus, EvidenceRow, Withheld
from app.storage import queries
from app.storage.repository import Repository


class Verdict(StrEnum):
    IN_SCOPE = "IN_SCOPE"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    FILTERED = "FILTERED"


#: Section 51's "USER_PROVIDED / AUTHORIZED LOCAL DOCUMENTS".
MY_BOOKS_CATEGORIES = frozenset({SourceCategory.USER_PROVIDED_SOURCE, SourceCategory.LOCAL_SOURCE})

#: Excluded from normal retrieval unless a lifecycle filter names them (P7 section 24).
EXCLUDED_BY_DEFAULT = frozenset({LifecycleStatus.DELETED, LifecycleStatus.ARCHIVED})


def counter(identifier: str) -> int:
    """The numeric identifier counter - the sort key ADR 0006 allows, never the string."""
    return parse_id(identifier)[1]


def id_key(identifier: str) -> tuple[str, int]:
    """Order identifiers of mixed kinds: by prefix, then numeric counter."""
    kind, number = parse_id(identifier)
    return kind.prefix, number


def evidence_key(row: EvidenceRow) -> tuple:
    """Document, page, character offset, then numeric counter (P9-22); unknown last."""
    return (
        counter(row.document_id),
        row.page_number is None, row.page_number or 0,
        row.char_start is None, row.char_start or 0,
        row.char_end is None, row.char_end or 0,
        row.subject_kind,
        id_key(row.id),
    )


def source_verdict(source: Source | None, scope: SourceScope) -> Verdict:
    """Authorisation first, in every scope; then the requested scope (P9-5)."""
    if (
        source is None
        or source.authorization is not Authorization.AUTHORIZED
        or source.source_category is SourceCategory.UNAUTHORIZED_SOURCE
    ):
        return Verdict.NOT_AUTHORIZED
    if scope is SourceScope.MY_BOOKS and source.source_category not in MY_BOOKS_CATEGORIES:
        return Verdict.OUT_OF_SCOPE
    return Verdict.IN_SCOPE


class QueryContext:
    """One query's reads, verdicts and withheld counts. Read-only."""

    def __init__(self, repository: Repository, request: QueryRequest) -> None:
        self.repository = repository
        self.connection = repository.connection
        self.request = request
        self.filters = request.filters
        self._rows: dict[type, dict[str, object]] = {}
        self._evidence: dict[str, tuple[EvidenceRow, ...]] = {}
        self._edges_of: dict[str, tuple[Relationship, ...]] = {}
        self._reviews: dict[str, KnowledgeReview | None] = {}
        self._linked_types: frozenset[KnowledgeType] | None = None
        #: Cache of labelled edges, filled by `app.query.traversal`.
        self.labelled: dict[str, object] = {}
        self._verdicts: dict[str, Verdict] = {}
        self._items: dict[str, str] = {}
        self._listed: set[str] = set()

    # ------------------------------------------------------------ stored rows

    def get(self, entity_type: type, identifier: str | None):
        """One stored row by identifier, read once per query; None when absent."""
        if identifier is None:
            return None
        cache = self._rows.setdefault(entity_type, {})
        if identifier not in cache:
            cache[identifier] = self.repository.get(entity_type, identifier)
        return cache[identifier]

    def concept(self, identifier: str | None) -> Concept | None:
        return self.get(Concept, identifier)

    def knowledge(self, identifier: str | None) -> KnowledgeObject | None:
        return self.get(KnowledgeObject, identifier)

    def relationship(self, identifier: str | None) -> Relationship | None:
        return self.get(Relationship, identifier)

    def source(self, identifier: str | None) -> Source | None:
        return self.get(Source, identifier)

    def run(self, identifier: str | None) -> ExtractionRun | None:
        return self.get(ExtractionRun, identifier)

    def evidence(self, subject_id: str) -> tuple[EvidenceRow, ...]:
        """Every stored evidence row of one subject, in evidence order (P9-22)."""
        if subject_id not in self._evidence:
            rows = (EvidenceRow.of(row) for row in queries.evidence_for(self.connection, subject_id))
            self._evidence[subject_id] = tuple(sorted(rows, key=evidence_key))
        return self._evidence[subject_id]

    def basis_row(self, occurrence_id: str | None) -> EvidenceRow | None:
        """The evidence row of the source occurrence a recorded basis names."""
        occurrence = self.get(SourceOccurrence, occurrence_id)
        if occurrence is None:
            return None
        for row in self.evidence(occurrence.knowledge_id):
            if row.id == occurrence.id:
                return row
        return None  # pragma: no cover - the view covers every source occurrence

    def edges_of(self, concept_id: str) -> tuple[Relationship, ...]:
        """Every ACTIVE stored edge touching a concept, across runs, by counter."""
        if concept_id not in self._edges_of:
            found = queries.relationships_for_concept(self.connection, concept_id)
            self._edges_of[concept_id] = tuple(sorted(found, key=lambda e: counter(e.id)))
        return self._edges_of[concept_id]

    def review(self, knowledge_id: str) -> KnowledgeReview | None:
        """`review`'s read model of one object (ADR 0034 P8-26), read once."""
        if knowledge_id not in self._reviews:
            self._reviews[knowledge_id] = review_knowledge(self.repository, knowledge_id)
        return self._reviews[knowledge_id]

    def linked_types(self) -> frozenset[KnowledgeType]:
        if self._linked_types is None:
            self._linked_types = queries.knowledge_types_linked_to_concepts(self.connection)
        return self._linked_types

    # -------------------------------------------------------------- verdicts

    def verdict(self, row: EvidenceRow) -> Verdict:
        """Authorisation, then scope, then the evidence filters. The same row, the same verdict."""
        if row.id not in self._verdicts:
            source = self.source(row.source_id)
            found = source_verdict(source, self.request.scope)
            if found is Verdict.IN_SCOPE and not self._passes_filters(row, source):
                found = Verdict.FILTERED
            self._verdicts[row.id] = found
        return self._verdicts[row.id]

    def _passes_filters(self, row: EvidenceRow, source: Source) -> bool:
        f = self.filters
        if f.document_ids and row.document_id not in f.document_ids:
            return False
        if f.source_categories and source.source_category not in f.source_categories:
            return False
        if f.pages and row.page_number not in f.pages:
            return False
        if f.run_ids and row.extraction_run_id not in f.run_ids:
            return False
        if f.run_statuses or f.extractor_versions:
            run = self.run(row.extraction_run_id)
            if run is None:
                return False
            if f.run_statuses and run.status not in f.run_statuses:
                return False
            if f.extractor_versions and run.extractor_version not in f.extractor_versions:
                return False
        return True

    def keep(self, rows: Iterable[EvidenceRow]) -> tuple[EvidenceRow, ...]:
        """The rows that may be returned, in evidence order."""
        kept = {row.id: row for row in rows if self.verdict(row) is Verdict.IN_SCOPE}
        return tuple(sorted(kept.values(), key=evidence_key))

    def judge_item(self, key: str, rows: Iterable[EvidenceRow]) -> tuple[EvidenceRow, ...]:
        """An item's evidence that may be returned; with none, the item is withheld."""
        rows = tuple(rows)
        kept = self.keep(rows)
        if not kept:
            if not rows:
                self.withhold(key, "no evidence")
            elif any(self.verdict(row) is Verdict.FILTERED for row in rows):
                self.withhold(key, "filtered")
            else:
                self.withhold(key, "not authorized")
        return kept

    def filter_out(self, key: str) -> None:
        """An item excluded by the request's filters or the lifecycle default."""
        self.withhold(key, "filtered")

    def withhold(self, key: str, reason: str) -> None:
        """Count an item as not shown: "not authorized", "no evidence" or "filtered"."""
        self._items.setdefault(key, reason)

    def listed(self, key: str) -> None:
        """An item placed in the result: never counted as withheld."""
        self._listed.add(key)

    def reason(self, key: str) -> str | None:
        """Why an item was withheld, or None when it was not."""
        return None if key in self._listed else self._items.get(key)

    def withheld_answer(self, keys: Iterable[str], what: str) -> tuple[AnswerStatus, str]:
        """The answer when every one of these items was withheld (sections 67, 228).

        If authorised evidence in scope existed and the request's own filters removed
        it, the answer is "not found within the filters", not insufficiency.
        """
        reasons = {self.reason(key) for key in keys}
        if "filtered" in reasons:
            return (
                AnswerStatus.NOT_FOUND,
                f"{what}: excluded by the request's filters or the lifecycle default.",
            )
        return (
            AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION,
            f"Insufficient authorized information: {what}, but nothing about it has "
            "evidence from a source that is authorised and in the requested scope "
            f"({self.request.scope.value}).",
        )

    def withheld(self) -> Withheld:
        verdicts = list(self._verdicts.values())
        reasons = [reason for key, reason in self._items.items() if key not in self._listed]
        return Withheld(
            unauthorized_evidence=verdicts.count(Verdict.NOT_AUTHORIZED),
            out_of_scope_evidence=verdicts.count(Verdict.OUT_OF_SCOPE),
            filtered_evidence=verdicts.count(Verdict.FILTERED),
            items_without_authorized_evidence=reasons.count("not authorized"),
            items_without_evidence=reasons.count("no evidence"),
            items_filtered_out=reasons.count("filtered"),
        )

    # ------------------------------------------------------------- lifecycle

    def lifecycle_allows(self, status: LifecycleStatus) -> bool:
        wanted = self.filters.lifecycle_statuses
        if wanted:
            return status in wanted
        if status in EXCLUDED_BY_DEFAULT:
            return False
        return status is not LifecycleStatus.SUPERSEDED or self.request.include_superseded

    def concept_allowed(self, concept: Concept) -> bool:
        """Concepts are never superseded or merged; only the default exclusion applies."""
        return concept.lifecycle_status not in EXCLUDED_BY_DEFAULT

    def knowledge_allowed(self, knowledge: KnowledgeObject) -> bool:
        f = self.filters
        return (
            self.lifecycle_allows(knowledge.lifecycle_status)
            and (not f.knowledge_types or knowledge.knowledge_type in f.knowledge_types)
            and (not f.certainties or knowledge.certainty in f.certainties)
        )

    def edge_allowed(self, edge: Relationship) -> bool:
        f = self.filters
        return (
            edge.lifecycle_status not in EXCLUDED_BY_DEFAULT
            and (not f.relation_types or edge.relation_type in f.relation_types)
            and (not f.origins or edge.origin in f.origins)
        )
