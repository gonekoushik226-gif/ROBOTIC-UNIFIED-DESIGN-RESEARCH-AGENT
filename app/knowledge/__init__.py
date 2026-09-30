"""Knowledge representation services (L2, Phase 3).

The layer above storage: it assembles concepts and their attachments, and holds the
rules the schema cannot. It may import `app.models`, `app.core` and `app.storage`
and nothing else - a boundary declared in `tests/unit/test_import_boundaries.py`
since Phase 1 and printed on every test run as "awaiting its package" until now.

**No SQL appears here.** `tests/unit/test_code_rules.py` fails the build if it does,
which is what keeps the database replaceable rather than nominally replaceable.
Every row this package needs comes from `app.storage.queries`.
"""

from app.knowledge.concepts import ConceptService, Evidence

__all__ = ["ConceptService", "Evidence"]
