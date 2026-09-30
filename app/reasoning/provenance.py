"""Evidence and provenance of a stored item used in reasoning (ADR 0039 P10-23; P9-18).

Every row comes from existing read-only storage queries: the `evidence` view through
`queries.evidence_for`, and the source, document, run and declared-edition rows
through `queries.evidence_context`. Each document also records whether its preserved
file is present on disk now - the same check `lookup` and `query` make, kept as a
separate fact and never folded into an accessibility label (P9-20). Nothing is derived
from these rows and nothing is written.
"""

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from app.models.enums import KnowledgeEquivalenceOutcome
from app.reasoning.results import DocumentProvenance, EvidenceRow, Provenance
from app.reasoning.scope import counter, evidence_key
from app.storage import queries


def evidence_of(connection: sqlite3.Connection, subject_id: str) -> tuple[EvidenceRow, ...]:
    """Every stored evidence row of one subject, in evidence order (P9-22)."""
    rows = (EvidenceRow.of(row) for row in queries.evidence_for(connection, subject_id))
    return tuple(sorted(rows, key=evidence_key))


def provenance_of(connection: sqlite3.Connection, rows: Iterable[EvidenceRow]) -> Provenance:
    """The stored source, document, run and edition rows these evidence rows name."""
    rows = tuple(rows)
    if not rows:
        return Provenance()
    stored = queries.evidence_context(
        connection,
        (
            {
                "source_id": row.source_id,
                "document_id": row.document_id,
                "extraction_run_id": row.extraction_run_id,
            }
            for row in rows
        ),
    )
    return Provenance(
        sources=stored.sources,
        documents=tuple(
            DocumentProvenance(
                document=document,
                preserved_file_present=Path(document.file_path).is_file(),
                editions=tuple(v for v in stored.editions if v.file_hash == document.file_hash),
            )
            for document in stored.documents
        ),
        runs=stored.runs,
    )


def superseded_pointer(connection: sqlite3.Connection, knowledge_id: str) -> str | None:
    """The canonical object a superseded item's stored `merge` pointer names, or None.

    `merge` supersedes a duplicate in place and records the pointer as an
    `EXACT_DUPLICATE` assessment naming the duplicate as the other object, with no
    extraction run (ADR 0033 P8-19; the "merge pointer" of `review`). Read as stored;
    never inferred.
    """
    records = queries.equivalences_of_knowledge(connection, knowledge_id)
    for record in sorted(records, key=lambda found: counter(found.id)):
        if (
            record.outcome is KnowledgeEquivalenceOutcome.EXACT_DUPLICATE
            and record.other_knowledge_id == knowledge_id
            and record.extraction_run_id is None
        ):
            return record.canonical_knowledge_id
    return None
