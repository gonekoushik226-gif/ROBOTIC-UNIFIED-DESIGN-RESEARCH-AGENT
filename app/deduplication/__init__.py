"""Knowledge deduplication and equivalence (L2, Phase 8 - Part 5 sections 196-197).

ADRs 0032-0034. The decision about *what* to link, create, compare or supersede is
kept apart from the persistence service, so it can be tested on its own (Part 1
section 24; the ADR 0021 and ADR 0030 precedent):

    rules        the rules as pure functions: D-30 normalisation, rule C1, ordering
    index        the ACTIVE knowledge in memory; the P8-12 exact relation, groups
                 and canonical members
    records      writes assessment records, conflicts and concept records
    equivalence  which concept pairs are POSSIBLE_EQUIVALENT (P8-16)
    stage15      stage 15, called inline by the extraction writer (P8-11)
    merge        `merge`: supersede stored exact duplicates in place (P8-19)
    review       `review`: one object's conflicts, assessments and sources (P8-26)
    editions     `edition`: user-declared editions as document_version rows (P8-27)

**Deterministic and provider-free** (P8-13, P8-25): no model, no embedding, no
similarity score, no threshold, no configuration key, no new dependency. String
similarity is never treated as equivalence; only identity after D-30 normalisation,
shared stored concept names, and rule C1's numeric-only difference are used.

**Nothing is ever deleted or rewritten**: evidence, edges, relationship occurrences
and Phase 6 inference bases stay where they are; `merge` changes lifecycle values
only; `normalized_hash` stays NULL (P8-18); concepts are never merged (P8-16).

Boundaries (P8-23, enforced in `tests/unit/test_import_boundaries.py`): may import
`app.version`, `app.models`, `app.core`, `app.storage` and `app.knowledge`; may
**not** import `app.documents` or `app.extraction` - it reads stored data through
`app.storage`, never raw text. No SQL here; it lives in `app.storage`.
"""

from app.deduplication.editions import EditionDeclaration, declare_edition
from app.deduplication.index import CONCEPT_RELATION, Item, KnowledgeIndex, Location
from app.deduplication.merge import MergeReport, Merger
from app.deduplication.review import (
    KnowledgeReview,
    ReviewedAssessment,
    ReviewedConflict,
    review_knowledge,
)
from app.deduplication.rules import (
    RULE_C1,
    RULE_CONCEPT,
    RULE_EXACT,
    RULE_SAME_CONCEPT,
    RULE_VERSION,
    c1_contradicts,
    normalized_statement,
)
from app.deduplication.stage15 import Decision, IssueNote, Stage15

__all__ = [
    "CONCEPT_RELATION",
    "Decision",
    "EditionDeclaration",
    "IssueNote",
    "Item",
    "KnowledgeIndex",
    "KnowledgeReview",
    "Location",
    "MergeReport",
    "Merger",
    "RULE_C1",
    "RULE_CONCEPT",
    "RULE_EXACT",
    "RULE_SAME_CONCEPT",
    "RULE_VERSION",
    "ReviewedAssessment",
    "ReviewedConflict",
    "Stage15",
    "c1_contradicts",
    "declare_edition",
    "normalized_statement",
    "review_knowledge",
]
