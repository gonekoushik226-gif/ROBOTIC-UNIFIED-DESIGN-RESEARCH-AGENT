"""`edition` - record the user's declaration that two documents are editions of one work.

Decision P8-27 (ADR 0034), section 81: *"Represent: Same work / different source
version"*. RUDRA cannot establish this itself - ADR 0016 forbids guessing from the
title, and ISBN is not stored (U-5) - so it is only ever **declared**, and every
`document_version` row records a user declaration; that is its provenance.

**What it writes:** one `document_version` row per edition, all filed under the
document the user names as the work: `version_label` is the user's label,
`file_hash` and `ingested_at` are copied from that edition's own `document` row, and
`notes` is empty. The work's own row is written the first time the work is named.
Nothing else: no `document` row changes (`document.edition`, metadata read from the
file, is not written), and Phase 4's segments and occurrences keep
`document_version_id = NULL`. No metadata is invented (section 29).

**Refused, with nothing written:** an unknown document; a document named as its own
edition; an edition already declared (under this work or another); an edition that
is itself a work with editions filed under it; a work that is itself declared as an
edition of another work; a first declaration without the work's own label; and a
work label different from the one already recorded (a declaration is never
rewritten).

Knowledge in declared editions is compared by stage 15 like any other (P8-21).
Nothing here commits; the caller owns the transaction.
"""

from dataclasses import dataclass

from app.core.errors import InvalidInputError
from app.models.base import utc_now
from app.models.entities import Document, DocumentVersion
from app.storage import queries
from app.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class EditionDeclaration:
    work: Document
    edition: Document
    #: Rows written by this declaration, in the order written.
    written: tuple[DocumentVersion, ...]
    #: Every row filed under the work after this declaration.
    rows: tuple[DocumentVersion, ...]


_STAGE = "deduplication.edition"


def _refuse(summary: str, reason: str, *options: str, missing: tuple[str, ...] = ()) -> InvalidInputError:
    return InvalidInputError.of(
        summary,
        reason,
        stage=_STAGE,
        missing=missing,
        data_changed=False,
        retry_safe=True,
        next_options=options or ("python -m app edition <DOC-id> --work <DOC-id> --label \"...\"",),
    )


def declare_edition(
    repository: Repository,
    *,
    edition_id: str,
    work_id: str,
    label: str,
    work_label: str | None = None,
) -> EditionDeclaration:
    """Validate the declaration completely, then write its rows."""
    connection = repository.connection
    if edition_id == work_id:
        raise _refuse(
            "A document cannot be declared an edition of itself; nothing was written.",
            f"{edition_id} was given both as the edition and as the work.",
        )
    documents = {}
    for role, identifier in (("edition", edition_id), ("work", work_id)):
        document = repository.get(Document, identifier)
        if document is None:
            raise _refuse(
                f"There is no document {identifier} to declare; nothing was written.",
                f"No document row has id {identifier!r} (given as the {role}). Editions are "
                "declared between documents that are already ingested.",
                "Ingest the PDF first: python -m app extract <path-to-pdf>",
                missing=(identifier,),
            )
        documents[role] = document
    edition, work = documents["edition"], documents["work"]
    label = label.strip() if isinstance(label, str) else ""
    if not label:
        raise _refuse(
            "An edition needs a label; nothing was written.",
            "--label is empty. The label is the user's name for this edition.",
        )

    if queries.document_versions_of(connection, edition.id):
        raise _refuse(
            f"{edition.id} is already the work of declared editions; nothing was written.",
            "Editions are filed under one work. Name it as the work instead: "
            f"--work {edition.id}.",
        )
    declared = queries.document_versions_by_hash(connection, edition.file_hash)
    if declared:
        under = sorted({row.document_id for row in declared})
        where = "this work" if under == [work.id] else f"work {', '.join(under)}"
        raise _refuse(
            f"{edition.id} is already declared as an edition of {where}; nothing was written.",
            f"A document_version row with this document's file hash already exists "
            f"({', '.join(row.id for row in declared)}). A declaration is never rewritten.",
        )

    work_rows = queries.document_versions_by_hash(connection, work.file_hash)
    elsewhere = sorted({row.document_id for row in work_rows if row.document_id != work.id})
    if elsewhere:
        raise _refuse(
            f"{work.id} is itself declared as an edition of {', '.join(elsewhere)}; "
            "nothing was written.",
            "Editions are filed under one work, never under an edition of it.",
            f"Name the work instead: --work {elsewhere[0]}",
        )
    own = [row for row in work_rows if row.document_id == work.id]
    if work_label is not None:
        work_label = work_label.strip()
        if not work_label:
            raise _refuse(
                "The work's label is empty; nothing was written.",
                "--work-label, when given, must name the work's own edition.",
            )
    if not own and work_label is None:
        raise _refuse(
            f"{work.id} has no edition label yet; nothing was written.",
            "The first edition declared for a work also records the work's own edition, "
            "so its label is needed: --work-label \"...\".",
        )
    if own and work_label is not None and work_label != own[0].version_label:
        raise _refuse(
            f"{work.id} is already recorded as \"{own[0].version_label}\"; nothing was written.",
            f"--work-label \"{work_label}\" differs, and a declaration is never rewritten.",
            "Omit --work-label, or give the recorded label.",
        )

    written = []
    if not own:
        written.append(_row(repository, work, work, work_label))
    written.append(_row(repository, work, edition, label))
    return EditionDeclaration(
        work=work,
        edition=edition,
        written=tuple(written),
        rows=queries.document_versions_of(connection, work.id),
    )


def _row(repository: Repository, work: Document, edition: Document, label: str) -> DocumentVersion:
    now = utc_now()
    return repository.add(
        DocumentVersion(
            id=repository.new_id(DocumentVersion),
            created_at=now,
            updated_at=now,
            document_id=work.id,
            version_label=label,
            file_hash=edition.file_hash,
            ingested_at=edition.ingested_at,
            notes=None,
        )
    )
