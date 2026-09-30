"""The structured request (ADR 0035 P9-2; ADR 0036 P9-23, P9-24).

Phase 9 parses no natural language: a query is a typed request, and section 199's
*"Find everything about MOSFETs in my books."* is run as the concept request
`QueryRequest.concept("MOSFET")` in the "my books" scope (the operator's translation
stands in for Phase 13).

Four modes:

    CONCEPT  every concept answering to an exact name, and its knowledge
    EXACT    one stored item by identifier
    PAGE     one page of one document
    KEYWORD  stored text matching one term, through the derived index (ADR 0037);
             the term is one phrase, a prefix only when `prefix` asks for it

Filters (P9-23): different filters combine with AND, the values of one filter with
OR. An empty filter does not narrow.
"""

from dataclasses import dataclass, fields
from enum import StrEnum

from app.core.errors import InvalidInputError
from app.models.enums import (
    CertaintyState,
    ExtractionRunStatus,
    KnowledgeType,
    LifecycleStatus,
    RelationshipOrigin,
    RelationType,
    SourceCategory,
)
from app.models.identifiers import EntityKind, is_valid_id
from app.models.naming import normalize_alias


class QueryMode(StrEnum):
    CONCEPT = "CONCEPT"
    EXACT = "EXACT"
    PAGE = "PAGE"
    KEYWORD = "KEYWORD"


class SourceScope(StrEnum):
    """Which sources' evidence a query may use (ADR 0035 P9-5).

    Evidence whose source is `NOT_AUTHORIZED`, or of category `UNAUTHORIZED_SOURCE`,
    is never returned in any scope (sections 49-50).
    """

    #: Section 51's "USER_PROVIDED / AUTHORIZED LOCAL DOCUMENTS": category
    #: `USER_PROVIDED_SOURCE` or `LOCAL_SOURCE`, and `AUTHORIZED`.
    MY_BOOKS = "MY_BOOKS"
    #: Every `AUTHORIZED` source.
    AUTHORIZED = "AUTHORIZED"


#: The identifier kinds exact search accepts (P9-24; `PHASE_9.md` section 10).
EXACT_KINDS: tuple[EntityKind, ...] = (
    EntityKind.CONCEPT,
    EntityKind.KNOWLEDGE_OBJECT,
    EntityKind.RELATIONSHIP,
    EntityKind.DOCUMENT,
)

_STAGE = "query.request"


def _refuse(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary,
        reason,
        stage=_STAGE,
        data_changed=False,
        retry_safe=True,
        next_options=(
            'QueryRequest.concept("MOSFET")',
            'QueryRequest.exact("K-00000001")',
            'QueryRequest.page("DOC-00000001", 1)',
            'QueryRequest.keyword("gate-source voltage")',
        ),
    )


@dataclass(frozen=True, slots=True)
class QueryFilters:
    """Metadata and source filters (P9-23). Each is a tuple of accepted values."""

    knowledge_types: tuple[KnowledgeType, ...] = ()
    lifecycle_statuses: tuple[LifecycleStatus, ...] = ()
    certainties: tuple[CertaintyState, ...] = ()
    relation_types: tuple[RelationType, ...] = ()
    origins: tuple[RelationshipOrigin, ...] = ()
    document_ids: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
    run_statuses: tuple[ExtractionRunStatus, ...] = ()
    extractor_versions: tuple[str, ...] = ()
    source_categories: tuple[SourceCategory, ...] = ()
    pages: tuple[int, ...] = ()

    def check(self) -> None:
        """Refuse a filter value of the wrong type or form; nothing is coerced."""
        typed = (
            ("knowledge_types", KnowledgeType),
            ("lifecycle_statuses", LifecycleStatus),
            ("certainties", CertaintyState),
            ("relation_types", RelationType),
            ("origins", RelationshipOrigin),
            ("run_statuses", ExtractionRunStatus),
            ("source_categories", SourceCategory),
        )
        for name, kind in typed:
            values = getattr(self, name)
            if not isinstance(values, tuple) or not all(isinstance(v, kind) for v in values):
                raise _refuse(
                    f"The {name} filter is not a tuple of {kind.__name__} values.",
                    f"Got {values!r}.",
                )
        for name, kind in (("document_ids", EntityKind.DOCUMENT), ("run_ids", EntityKind.EXTRACTION_RUN)):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not all(is_valid_id(v, kind=kind) for v in values):
                raise _refuse(
                    f"The {name} filter holds a value that is not a {kind.name} identifier.",
                    f"Got {values!r}; a {kind.name} identifier looks like {kind.prefix}-00000001.",
                )
        if not isinstance(self.extractor_versions, tuple) or not all(
            isinstance(v, str) and v.strip() for v in self.extractor_versions
        ):
            raise _refuse("The extractor_versions filter holds an empty value.", f"Got {self.extractor_versions!r}.")
        if not isinstance(self.pages, tuple) or not all(
            isinstance(p, int) and not isinstance(p, bool) and p >= 0 for p in self.pages
        ):
            raise _refuse("The pages filter holds a value that is not a page number.", f"Got {self.pages!r}.")

    @property
    def active(self) -> tuple[str, ...]:
        """The names of the filters that narrow, in declaration order."""
        return tuple(f.name for f in fields(self) if getattr(self, f.name))


@dataclass(frozen=True, slots=True)
class QueryRequest:
    """One structured query. Build it with `concept`, `exact` or `page`."""

    mode: QueryMode
    name: str | None = None
    identifier: str | None = None
    document_id: str | None = None
    page_number: int | None = None
    #: The keyword term: one phrase, matched through the derived index.
    term: str | None = None
    #: Keyword mode only: match the term's last word as a prefix (`"term"*`).
    prefix: bool = False
    scope: SourceScope = SourceScope.MY_BOOKS
    filters: QueryFilters = QueryFilters()
    #: Superseded objects reached through a stored edge are listed, labelled
    #: (ADR 0036 P9-16, P9-23); False leaves them out.
    include_superseded: bool = True
    #: Deterministic widening over stored equivalence data (D2, ADR 0035 P9-4).
    widen: bool = True

    @classmethod
    def concept(cls, name: str, **options: object) -> "QueryRequest":
        return cls(mode=QueryMode.CONCEPT, name=name, **options)  # type: ignore[arg-type]

    @classmethod
    def exact(cls, identifier: str, **options: object) -> "QueryRequest":
        return cls(mode=QueryMode.EXACT, identifier=identifier, **options)  # type: ignore[arg-type]

    @classmethod
    def page(cls, document_id: str, page_number: int, **options: object) -> "QueryRequest":
        return cls(  # type: ignore[arg-type]
            mode=QueryMode.PAGE, document_id=document_id, page_number=page_number, **options
        )

    @classmethod
    def keyword(cls, term: str, *, prefix: bool = False, **options: object) -> "QueryRequest":
        return cls(mode=QueryMode.KEYWORD, term=term, prefix=prefix, **options)  # type: ignore[arg-type]

    @property
    def normalized_name(self) -> str | None:
        """The D-30 form of the concept name (ADR 0036 P9-11), or None."""
        return None if self.name is None else normalize_alias(self.name)

    @property
    def normalized_term(self) -> str | None:
        """The D-30 form of the keyword term (P9-30), or None."""
        return None if self.term is None else normalize_alias(self.term)

    def check(self) -> "QueryRequest":
        """Refuse a request that cannot be answered as asked. Returns it unchanged."""
        if not isinstance(self.mode, QueryMode):
            raise _refuse(
                "The query mode is not one of CONCEPT, EXACT, PAGE or KEYWORD.", f"Got {self.mode!r}."
            )
        if not isinstance(self.prefix, bool) or (self.prefix and self.mode is not QueryMode.KEYWORD):
            raise _refuse("A prefix is asked for only in KEYWORD mode, as True or False.", f"Got {self.prefix!r}.")
        if not isinstance(self.scope, SourceScope):
            raise _refuse("The source scope is not one of MY_BOOKS or AUTHORIZED.", f"Got {self.scope!r}.")
        if not isinstance(self.filters, QueryFilters):
            raise _refuse("The filters are not a QueryFilters value.", f"Got {self.filters!r}.")
        self.filters.check()
        used = {
            "name": self.name is not None,
            "identifier": self.identifier is not None,
            "document_id": self.document_id is not None,
            "page_number": self.page_number is not None,
            "term": self.term is not None,
        }
        wanted = {
            QueryMode.CONCEPT: {"name"},
            QueryMode.EXACT: {"identifier"},
            QueryMode.PAGE: {"document_id", "page_number"},
            QueryMode.KEYWORD: {"term"},
        }[self.mode]
        given = {name for name, present in used.items() if present}
        if given != wanted:
            raise _refuse(
                f"A {self.mode.value} query takes {', '.join(sorted(wanted))} and nothing else.",
                f"It was given: {', '.join(sorted(given)) or 'nothing'}.",
            )
        if self.mode is QueryMode.CONCEPT:
            try:
                normalize_alias(self.name)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise _refuse(
                    "The concept name is empty.",
                    "An exact-name query needs at least one character that is not whitespace.",
                ) from exc
        elif self.mode is QueryMode.KEYWORD:
            try:
                normalize_alias(self.term)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise _refuse(
                    "The keyword term is empty.",
                    "A keyword query needs at least one character that is not whitespace.",
                ) from exc
        elif self.mode is QueryMode.EXACT:
            if not any(is_valid_id(self.identifier, kind=kind) for kind in EXACT_KINDS):
                kinds = ", ".join(f"{k.name} ({k.prefix}-...)" for k in EXACT_KINDS)
                raise _refuse(
                    f"{self.identifier!r} is not an identifier exact search accepts.",
                    f"Exact search takes an identifier of a {kinds}.",
                )
        else:
            if not is_valid_id(self.document_id, kind=EntityKind.DOCUMENT):
                raise _refuse(
                    f"{self.document_id!r} is not a document identifier.",
                    "A page is named by a document identifier (DOC-00000001) and a page number.",
                )
            page = self.page_number
            if not isinstance(page, int) or isinstance(page, bool) or page < 0:
                raise _refuse("The page number is not a whole number of 0 or more.", f"Got {page!r}.")
        return self
