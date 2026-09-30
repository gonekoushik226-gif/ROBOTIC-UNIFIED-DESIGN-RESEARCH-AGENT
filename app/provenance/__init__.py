"""Phase 12 provenance: *"Where did you get this?"* for stored items (ADR 0044).

    results  the read models: provenance status, checks, citations, documents, history
    scope    P9-5 authorisation and scope, P9-23 lifecycle, P9-22 order, re-applied
    checks   the quote check and the file check (P12-8)
    items    `ProvenanceService.of_item`: the provenance of one stored item (P12-5 ... P12-7)

Reads through `app.storage` only; writes nothing (P12-3). A citation is shown only when
stored evidence in scope exists; otherwise the answer is *"Provenance unavailable."*
(section 205), and nothing is invented.
"""

from app.provenance.items import ProvenanceService
from app.provenance.results import (
    UNAVAILABLE_TEXT,
    Check,
    CheckStatus,
    ItemProvenance,
    ProvenanceStatus,
    as_plain,
    combined,
    to_json,
)
from app.provenance.scope import ProvenanceScope

__all__ = [
    "UNAVAILABLE_TEXT",
    "Check",
    "CheckStatus",
    "ItemProvenance",
    "ProvenanceScope",
    "ProvenanceService",
    "ProvenanceStatus",
    "as_plain",
    "combined",
    "to_json",
]
