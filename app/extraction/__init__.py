"""Knowledge extraction (L2, Phase 5 - Part 5 section 190, ADRs 0017-0025).

Pipeline stages 9-14 of `ARCHITECTURE.md` section 7.1, each in its own module
because Part 2 section 31 forbids one giant function:

    classify   stage 9   which text may yield claims at all
    detectors  10-11     the eleven section 190 categories
    validate   12        section 60's checks; problems recorded, never repaired
    normalize  13        whitespace and names only; never NFKC on content
    pipeline   14        provenance attachment, runs, issues

Boundaries this package keeps (decision D-42, ADR 0021):

* **Separate from `app.knowledge`.** That package stores knowledge correctly;
  this one decides what to store. It calls `ConceptService` rather than writing
  concept rows itself.
* **Never imports `app.documents`.** Segments come from `app.storage`, so a parser
  swap (ADR 0014) cannot ripple into extraction.
* **No SQL, no model, no provider, no new dependency** (decision D-39).
"""

from app.extraction.classify import PageView, classify_pages
from app.extraction.detectors import detect_all
from app.extraction.pipeline import (
    CATEGORIES,
    EXTRACTOR_VERSION,
    ExtractionPipeline,
    ExtractionReport,
)

__all__ = [
    "CATEGORIES",
    "EXTRACTOR_VERSION",
    "ExtractionPipeline",
    "ExtractionReport",
    "PageView",
    "classify_pages",
    "detect_all",
]
