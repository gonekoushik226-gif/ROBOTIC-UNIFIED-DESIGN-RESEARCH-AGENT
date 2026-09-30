"""Document ingestion (L2, Phase 4).

Part 5 section 188's seven items - PDF detection, file validation, hashing,
metadata extraction, text extraction, page segmentation and document structure
representation - implemented as separate stages, because Part 2 section 31 says
"Do not make the entire ingestion process one giant function."

Boundaries this package keeps:

* **No SQL.** `tests/unit/test_code_rules.py` fails the build if it appears here.
  Rows come from `app.storage`.
* **`pypdf` is imported by `pypdf_adapter` and nowhere else** (ADR 0014), so
  replacing the parser stays a one-file change.
* **Phase 4 stops at stage 8.** No knowledge object, concept, relationship or
  occurrence is created by ingestion; that is Phase 5 (ADR 0016).
"""

from app.documents.intake import PreservedFile, ValidatedFile, hash_file, preserve, validate
from app.documents.pipeline import IngestionPipeline, IngestionReport, PageFailure
from app.documents.ports import ParsedDocument, ParsedPage, PdfMetadata, PdfParser
from app.documents.pypdf_adapter import PypdfParser
from app.documents.text_quality import TextQuality, assess

__all__ = [
    "IngestionPipeline",
    "IngestionReport",
    "PageFailure",
    "ParsedDocument",
    "ParsedPage",
    "PdfMetadata",
    "PdfParser",
    "PreservedFile",
    "PypdfParser",
    "TextQuality",
    "ValidatedFile",
    "assess",
    "hash_file",
    "preserve",
    "validate",
]
