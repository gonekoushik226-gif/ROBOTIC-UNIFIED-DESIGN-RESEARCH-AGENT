"""Source scope, lifecycle and ordering for provenance (ADR 0035 P9-5; ADR 0036 P9-22,
P9-23), re-applied unchanged (ADR 0044 P12-4, P12-14).

`app.provenance` reads through `app.storage` only, so the Phase 9 rules are applied here
from the same definitions, as Phases 10 and 11 apply them; the tests hold all four to the
same verdicts on the same data.

**Authorisation.** Evidence whose source is `NOT_AUTHORIZED`, or of category
`UNAUTHORIZED_SOURCE`, is never shown, in any scope (sections 49-50). "My books" (section
51) is category `USER_PROVIDED_SOURCE` or `LOCAL_SOURCE` and `AUTHORIZED`. The stored
columns decide; nothing here infers or changes an authorisation.

**Lifecycle** (P9-23): `DELETED` and `ARCHIVED` are excluded; any other stored status is
shown as stored.

**Order** (P9-22; ADR 0006): by stored attributes, then the numeric identifier counter.
"""

from enum import StrEnum

from app.models.entities import Source
from app.models.enums import Authorization, LifecycleStatus, SourceCategory
from app.models.identifiers import parse_id
from app.provenance.results import EvidenceRow


class ProvenanceScope(StrEnum):
    """Which sources' evidence may be shown: ADR 0035 P9-5, unchanged."""

    MY_BOOKS = "MY_BOOKS"
    AUTHORIZED = "AUTHORIZED"


class Verdict(StrEnum):
    IN_SCOPE = "IN_SCOPE"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


MY_BOOKS_CATEGORIES = frozenset({SourceCategory.USER_PROVIDED_SOURCE, SourceCategory.LOCAL_SOURCE})
EXCLUDED_BY_DEFAULT = frozenset({LifecycleStatus.DELETED, LifecycleStatus.ARCHIVED})


def source_verdict(source: Source | None, scope: ProvenanceScope) -> Verdict:
    """Authorisation first, in every scope; then the requested scope (P9-5)."""
    if (
        source is None
        or source.authorization is not Authorization.AUTHORIZED
        or source.source_category is SourceCategory.UNAUTHORIZED_SOURCE
    ):
        return Verdict.NOT_AUTHORIZED
    if scope is ProvenanceScope.MY_BOOKS and source.source_category not in MY_BOOKS_CATEGORIES:
        return Verdict.OUT_OF_SCOPE
    return Verdict.IN_SCOPE


def lifecycle_excluded(status: object) -> bool:
    return status in EXCLUDED_BY_DEFAULT


def counter(identifier: str) -> int:
    """The numeric identifier counter - the sort key ADR 0006 allows, never the string."""
    return parse_id(identifier)[1]


def id_key(identifier: str) -> tuple[str, int]:
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
