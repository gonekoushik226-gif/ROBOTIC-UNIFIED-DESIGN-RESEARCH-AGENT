"""Phase 18 controlled Internet (ADR 0050; Part 5 sections 216-217; Part 4 sections 124-129).

    sites      the user's authorization of one website, and the HTML reader (pure)
    retrieval  one page, inside the authorization and the limits (standard library)
    research   local knowledge first; "Insufficient authorized information."; with the
               user's named site, retrieval, external provenance, labels and conflicts

Nothing here connects anywhere unless the user names a site on the command line; the
tests use a loopback server only (P18-13).
"""

from app.internet.research import (
    ConflictRecord,
    ExternalClaim,
    ExternalRecord,
    LocalAnswer,
    Research,
    ResearchAnswer,
    ResearchStatus,
)
from app.internet.retrieval import RetrievalFailed, Retrieved, retrieve
from app.internet.sites import Site, site, within

__all__ = [
    "ConflictRecord",
    "ExternalClaim",
    "ExternalRecord",
    "LocalAnswer",
    "Research",
    "ResearchAnswer",
    "ResearchStatus",
    "RetrievalFailed",
    "Retrieved",
    "Site",
    "retrieve",
    "site",
    "within",
]
