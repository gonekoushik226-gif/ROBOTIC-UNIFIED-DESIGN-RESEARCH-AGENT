"""Provenance of every cited evidence row (ADR 0036 P9-18, P9-20; ADR 0035 P9-6).

The context comes from stored rows through `queries.evidence_context`: each source
with its stored name, category, authorisation and availability; each document with
its declared editions (by file hash, as `edition` files them) and, separately,
whether its preserved file is present on disk now - the check `lookup` makes; each
run with its number, status and extractor version. Nothing is derived from them: no
accessibility label (P9-20), no preferred run (P9-6).

`cited_rows` walks a finished part of a result for every evidence row it cites, so
the provenance of a section can never miss a row the section shows.
"""

from collections.abc import Iterable
from dataclasses import fields, is_dataclass
from pathlib import Path

from app.models.enums import ExtractionRunStatus
from app.query.results import DocumentProvenance, EvidenceRow, Provenance
from app.query.scope import QueryContext, evidence_key, id_key
from app.storage import queries

#: Stage 15 (extractor version 4) is where evidence is first compared (ADR 0032 P8-7).
FIRST_COMPARED_EXTRACTOR_VERSION = 4


def cited_rows(value: object) -> tuple[EvidenceRow, ...]:
    """Every distinct evidence row inside a result part, in evidence order."""
    found: dict[str, EvidenceRow] = {}

    def walk(item: object) -> None:
        if isinstance(item, EvidenceRow):
            found.setdefault(item.id, item)
        elif isinstance(item, (tuple, list)):
            for element in item:
                walk(element)
        elif is_dataclass(item) and not isinstance(item, type):
            for f in fields(item):
                walk(getattr(item, f.name))

    walk(value)
    return tuple(sorted(found.values(), key=evidence_key))


def provenance_of(context: QueryContext, rows: Iterable[EvidenceRow]) -> Provenance:
    """The stored source, document, run and edition rows these evidence rows name."""
    rows = tuple(rows)
    if not rows:
        return Provenance()
    stored = queries.evidence_context(
        context.connection,
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


def run_notes(provenance: Provenance) -> tuple[str, ...]:
    """What the cited runs themselves disclose (P9-6, P9-16)."""
    notes = []
    partial = [run.id for run in provenance.runs if run.status is ExtractionRunStatus.PARTIAL]
    if partial:
        notes.append(
            "Evidence from PARTIAL extraction runs is cited (" + ", ".join(partial) + "): "
            "those runs did not fully process every page."
        )
    early = []
    for run in provenance.runs:
        try:
            if int(run.extractor_version) < FIRST_COMPARED_EXTRACTOR_VERSION:
                early.append(run.id)
        except ValueError:
            continue
    if early:
        notes.append(
            "Evidence from runs with extractor version below 4 is cited ("
            + ", ".join(early)
            + "): stage 15 never compared it, so identical knowledge may appear as "
            "separate items until `merge` is run."
        )
    return tuple(notes)


def sorted_ids(identifiers: Iterable[str]) -> tuple[str, ...]:
    """Distinct identifiers by prefix and numeric counter."""
    return tuple(sorted(set(identifiers), key=id_key))
