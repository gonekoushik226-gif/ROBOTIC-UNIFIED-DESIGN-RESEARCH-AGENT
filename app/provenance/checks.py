"""The provenance checks: is a citation real? (ADR 0044 P12-8; sections 18, 137, 165.)

**Quote check.** A citation is checked against the text RUDRA stored when it ingested the
page, at the recorded span only. Spans are offsets into `document_segment.text` (ADR 0019).
A span is never recomputed by searching the text, because a sentence can occur twice and a
search would fabricate a location (Part 1 section 5): with no span recorded, the check is
`INCONCLUSIVE`. The segment must also belong to the cited document and page. What must
match follows the two rules extraction documents for what it stores (ADR 0044 P12-8, as
corrected at implementation):

- **A knowledge object's or relationship's quote** is the stored sentence. It must equal
  the text at the span exactly, or equal it after the extractor's own flattening of the
  line breaks a PDF puts inside a sentence (`app/extraction/detectors.py` `_flat`: every
  run of whitespace as one space, ends trimmed). The detail says which.
- **A concept's quote** is its surface form, and its span is the sentence it was found
  in. The name must occur within the text at the span under D-30 (NFKC, case-folded,
  whitespace collapsed; ADR 0010), never anywhere else on the page.

Any other difference is `FAILED`, never excused.

**File check.** The preserved copy of the document is read and hashed (SHA-256, streamed)
and compared with the stored hash. An absent file is `INCONCLUSIVE`, not a failure: Part 7
lets the user delete source files, and the knowledge and its provenance survive
(sections 4-5); the citation still stands on the stored page text.

Both read only. Neither changes or repairs anything.
"""

import hashlib
import re
from pathlib import Path

from app.models.entities import Document, DocumentSegment
from app.models.naming import normalize_alias
from app.provenance.results import Check, CheckKind, CheckStatus, EvidenceRow
from app.storage.repository import Repository

_CHUNK = 1 << 20


def quote_check(repository: Repository, row: EvidenceRow) -> Check:
    """Whether the quoted text is where the evidence says it is."""
    kind = CheckKind.QUOTE.value
    if row.segment_id is None or row.char_start is None or row.char_end is None:
        return Check(kind, row.id, CheckStatus.INCONCLUSIVE,
                     "no segment and span were recorded for this evidence, so there is no "
                     "location to check (a location is never searched for, ADR 0019)")
    segment = repository.get(DocumentSegment, row.segment_id)
    if segment is None:
        return Check(kind, row.id, CheckStatus.FAILED,
                     f"the recorded segment {row.segment_id} is not stored")
    if segment.document_id != row.document_id or segment.page_number != row.page_number:
        return Check(kind, row.id, CheckStatus.FAILED,
                     f"the recorded segment {segment.id} belongs to {segment.document_id} "
                     f"p.{segment.page_number}, not to the cited {row.document_id} p.{row.page_number}")
    stored = segment.text[row.char_start:row.char_end]
    where = f"{row.document_id} p.{row.page_number} [{row.char_start}-{row.char_end}] of {segment.id}"
    if row.subject_kind == "CONCEPT":
        if _named_within(row.evidence_text, stored):
            return Check(kind, row.id, CheckStatus.VERIFIED,
                         f"the concept's name occurs within the recorded sentence at {where} "
                         "(compared under D-30)")
    elif stored == row.evidence_text:
        return Check(kind, row.id, CheckStatus.VERIFIED, f"the quoted text is at {where}, exactly")
    elif _flat(stored) == row.evidence_text:
        return Check(kind, row.id, CheckStatus.VERIFIED,
                     f"the quoted text is at {where}, as extraction stores it: the line breaks "
                     "inside the sentence flattened to single spaces (Phase 5)")
    return Check(kind, row.id, CheckStatus.FAILED,
                 f"the stored page text at {where} reads {stored!r}, which does not match the "
                 f"quoted {row.evidence_text!r}")


def _flat(text: str) -> str:
    """The extractor's documented flattening of a sentence (Phase 5 `_flat`)."""
    return re.sub(r"\s+", " ", text).strip()


def _named_within(name: str, text: str) -> bool:
    try:
        return normalize_alias(name) in normalize_alias(text)
    except (TypeError, ValueError):
        return False


class FileHashes:
    """SHA-256 of preserved files, each computed once per request."""

    def __init__(self) -> None:
        self._hashes: dict[str, str | None] = {}

    def of(self, path: Path) -> str | None:
        key = str(path)
        if key not in self._hashes:
            self._hashes[key] = _sha256(path) if path.is_file() else None
        return self._hashes[key]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_check(document: Document, hashes: FileHashes) -> tuple[bool, Check]:
    """Whether the preserved copy is present, and whether it is the file that was hashed."""
    kind = CheckKind.FILE.value
    found = hashes.of(Path(document.file_path))
    if found is None:
        return False, Check(kind, document.id, CheckStatus.INCONCLUSIVE,
                            "the preserved file is not present; the knowledge and its provenance "
                            "are preserved, and the citation stands on the stored page text (Part 7)")
    if found.lower() == document.file_hash.lower():
        return True, Check(kind, document.id, CheckStatus.VERIFIED,
                           f"the preserved file's SHA-256 equals the stored hash {document.file_hash[:12]}…")
    return True, Check(kind, document.id, CheckStatus.FAILED,
                       f"the preserved file's SHA-256 {found[:12]}… differs from the stored hash "
                       f"{document.file_hash[:12]}…: the file is not the one ingested")
