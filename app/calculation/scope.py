"""Source scope, lifecycle and ordering for the admitted equation (ADR 0035 P9-5; ADR 0036
P9-22, P9-23), re-applied unchanged.

ADR 0043 P11-26 lets `app.calculation` read through `app.storage` but not import
`app.query` or `app.reasoning`, so the Phase 9 rules are applied here a third time, from
the same definitions. The tests hold the three to the same verdicts on the same data.

**Authorisation.** Evidence whose source is `NOT_AUTHORIZED`, or of category
`UNAUTHORIZED_SOURCE`, is never used, in any scope (sections 49-50). "My books"
(section 51) is category `USER_PROVIDED_SOURCE` or `LOCAL_SOURCE` and `AUTHORIZED`.
The stored columns decide; nothing here infers or changes an authorisation.

**Lifecycle** (P9-23, P7 section 24): `DELETED` and `ARCHIVED` are excluded; every
other stored status is used as stored, `SUPERSEDED` labelled with its pointer.

**Order** (P9-22; ADR 0006): by stored attributes, then by the numeric identifier
counter - never by the identifier string, which breaks above the fixed-width ceiling.
"""

from enum import StrEnum

from app.calculation.requests import CalculationScope
from app.calculation.results import EvidenceRow
from app.models.entities import Source
from app.models.enums import Authorization, LifecycleStatus, SourceCategory
from app.models.identifiers import parse_id


class Verdict(StrEnum):
    IN_SCOPE = "IN_SCOPE"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


#: Section 51's "USER_PROVIDED / AUTHORIZED LOCAL DOCUMENTS".
MY_BOOKS_CATEGORIES = frozenset({SourceCategory.USER_PROVIDED_SOURCE, SourceCategory.LOCAL_SOURCE})

#: Excluded from normal use (P7 section 24; P9-23).
EXCLUDED_BY_DEFAULT = frozenset({LifecycleStatus.DELETED, LifecycleStatus.ARCHIVED})


def source_verdict(source: Source | None, scope: CalculationScope) -> Verdict:
    """Authorisation first, in every scope; then the requested scope (P9-5)."""
    if (
        source is None
        or source.authorization is not Authorization.AUTHORIZED
        or source.source_category is SourceCategory.UNAUTHORIZED_SOURCE
    ):
        return Verdict.NOT_AUTHORIZED
    if scope is CalculationScope.MY_BOOKS and source.source_category not in MY_BOOKS_CATEGORIES:
        return Verdict.OUT_OF_SCOPE
    return Verdict.IN_SCOPE


def lifecycle_excluded(status: LifecycleStatus) -> bool:
    return status in EXCLUDED_BY_DEFAULT


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
