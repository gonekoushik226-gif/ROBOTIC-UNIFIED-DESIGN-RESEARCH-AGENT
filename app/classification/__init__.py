"""Automatic organisation of extracted knowledge (L2, Phase 6 - Part 5 section 192).

ADRs 0026-0030. Four modules, each separately testable (Part 1 section 24):

    selection   which run: exactly one, explicitly named; no current-run policy
    rule_r1     the one inference rule, as pure functions
    classifier  R1 over one run; each edge written with its basis, one transaction
    views       V1 hierarchy navigation and V2 organisation - views, never stored

**Interpretation, not specification** (ADR 0026, P6-1): section 192's *"Support:"*
is read as *organise*. Only "related concepts" is inferred, by rule R1; parent/child,
grouping, prerequisites, dependencies and applications are organised from what
Phase 5 extracted as source-stated; cross-references are not represented. The
specification mandates neither this reading nor R1.

**Not to be confused with `app/extraction/classify.py`**, which is Phase 5's stage 9
*content* classification (it masks question banks before detection). This package
is section 192's knowledge *organisation*. Neither may import the other.

Boundaries (decision P6-11, ADR 0030; enforced in `tests/unit/test_import_boundaries.py`):

* may import `app.knowledge`, so edges are written through `ConceptService` and the
  edge rules (D-21, D-24) keep applying;
* may **not** import `app.documents` or `app.extraction`: classification reads
  stored Phase 5 output through `app.storage` - never raw text, never the extractor
  (ADR 0026, P6-17);
* no SQL (it lives in `app.storage`), no model, no network, no new dependency
  (P6-13).
"""

from app.classification.classifier import (
    AREA_STATUS,
    ClassificationReport,
    Classifier,
    PairOutcome,
)
from app.classification.rule_r1 import RULE, RULE_VERSION, canonical_pair
from app.classification.selection import REFUSED_STATUSES, select_run
from app.classification.views import (
    HierarchyNode,
    HierarchyView,
    LabelledEdge,
    OrganisationView,
    hierarchy_view,
    organisation_view,
)

__all__ = [
    "AREA_STATUS",
    "ClassificationReport",
    "Classifier",
    "HierarchyNode",
    "HierarchyView",
    "LabelledEdge",
    "OrganisationView",
    "PairOutcome",
    "REFUSED_STATUSES",
    "RULE",
    "RULE_VERSION",
    "canonical_pair",
    "hierarchy_view",
    "organisation_view",
    "select_run",
]
