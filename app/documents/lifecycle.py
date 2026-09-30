"""The source-file lifecycle (ADR 0055; Part 7 sections 8-20, 27).

A user may delete RUDRA's preserved copy of a document to save storage (Part 7 section 14)
without losing what was learnt from it (section 10: source deletion is not knowledge
deletion). What that means, exactly:

    deleted    RUDRA's own copy in `data/documents/` - never the user's original elsewhere
    kept       the document record, its SHA-256 (section 6), every source record, every
               knowledge object and every occurrence with its quote: provenance remains
    changed    each AVAILABLE source becomes DELETED_BY_USER - shown as
               SOURCE_FILE_DELETED - so RUDRA never claims the file is available
    audited    one `audit_event` row: what, when, on whose confirmation, verified (18)
    verified   the file gone; the hash, sources and knowledge still there (19)

Only with the user's explicit confirmation. Uploading the same bytes again restores the
copy and the source (section 20; `IngestionPipeline`). Knowledge deletion is not offered
(sections 22-24: more restricted, dependency-aware).
"""

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

from app.core.errors import InvalidInputError
from app.models.base import utc_now
from app.models.entities import AuditEvent, Document
from app.models.enums import RiskLevel, SourceAvailability, VerificationStatus
from app.models.identifiers import EntityKind, is_valid_id, parse_id
from app.storage import queries
from app.storage.repository import Repository

#: How a source's availability is shown to the user (Part 7 section 27, step 12).
STATUS_LABELS = {
    SourceAvailability.AVAILABLE: "SOURCE_FILE_AVAILABLE",
    SourceAvailability.DELETED_BY_USER: "SOURCE_FILE_DELETED",
}
EVENT = "SOURCE_FILE_DELETED"


@dataclass(frozen=True, slots=True)
class SourceRecord:
    id: str
    name: str
    category: str
    availability: str
    status: str


@dataclass(frozen=True, slots=True)
class SourceStatus:
    document_id: str
    original_filename: str
    stored_path: str
    file_hash: str
    preserved_file_present: bool
    sources: tuple[SourceRecord, ...]
    #: Knowledge objects with an occurrence in this document.
    knowledge: int
    #: SOURCE_FILE_AVAILABLE, SOURCE_FILE_DELETED, or the stored availability.
    status: str


@dataclass(frozen=True, slots=True)
class Deletion:
    """What a deletion request did (or, refused, would do)."""

    document_id: str
    #: DELETED (and VERIFIED), REFUSED (nothing changed), or FAILED.
    outcome: str
    message: str
    before: SourceStatus
    after: SourceStatus | None = None
    audit_id: str | None = None
    checks: tuple[tuple[str, bool], ...] = ()


def refuse(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="documents.lifecycle", data_changed=False, retry_safe=True,
        next_options=("python -m app source DOC-00000001", "python -m app source DOC-00000001 --delete-file --confirm"),
    )


class SourceLifecycle:
    def __init__(self, repository: Repository, documents_dir: Path) -> None:
        self.repository = repository
        self.documents_dir = Path(documents_dir)

    @staticmethod
    def check_identifier(identifier: object) -> None:
        if not isinstance(identifier, str) or not is_valid_id(identifier) or \
                parse_id(identifier)[0] is not EntityKind.DOCUMENT:
            raise refuse(f"{identifier!r} is not a document identifier.", "For example DOC-00000001.")

    def status(self, document_id: str) -> SourceStatus | None:
        self.check_identifier(document_id)
        document = self.repository.get(Document, document_id)
        if document is None:
            return None
        connection = self.repository.connection
        sources = tuple(
            SourceRecord(s.id, s.name, s.source_category.value, s.availability.value,
                         STATUS_LABELS.get(s.availability, s.availability.value))
            for s in queries.sources_for_document(connection, document_id))
        knowledge = len({o.knowledge_id for o in queries.occurrences_by_method(connection, document_id, "")})
        overall = sources[0].status if sources else "NO_SOURCE_RECORD"
        return SourceStatus(document.id, document.original_filename, document.file_path, document.file_hash,
                            Path(document.file_path).exists(), sources, knowledge, overall)

    def delete_file(self, document_id: str, *, confirmed: bool) -> Deletion:
        """Delete RUDRA's preserved copy of one document, keeping everything learnt from it."""
        before = self.status(document_id)
        if before is None:
            raise refuse(f"No document has the identifier {document_id}.", "Nothing was deleted.")
        stored = Path(before.stored_path)
        if not before.preserved_file_present:
            raise refuse(f"RUDRA's copy of {document_id} is already gone.", f"{stored} does not exist.")
        try:
            stored.resolve().relative_to(self.documents_dir.resolve())
        except ValueError:
            raise refuse(f"{stored} is not in RUDRA's document store.",
                         "Only RUDRA's own preserved copies are ever deleted - never a file elsewhere.") from None
        if hashlib.sha256(stored.read_bytes()).hexdigest().upper() != before.file_hash.upper():
            raise refuse(f"{stored} does not match the document's recorded SHA-256.",
                         "Nothing was deleted: the file is not the one the knowledge came from.")
        size = stored.stat().st_size
        would = (f"delete RUDRA's preserved copy {stored} ({size} bytes); keep the document record, its SHA-256, "
                 f"{len(before.sources)} source record(s), {before.knowledge} knowledge object(s) and their "
                 "provenance; show the source as SOURCE_FILE_DELETED")
        if not confirmed:
            return Deletion(document_id, "REFUSED", f"Nothing was deleted. Confirmed, this would {would}. "
                            "Run again with --confirm.", before)
        connection = self.repository.connection
        try:
            for source in queries.sources_for_document(connection, document_id):
                if source.availability is SourceAvailability.AVAILABLE:
                    self.repository.update(replace(source, availability=SourceAvailability.DELETED_BY_USER))
            stored.unlink()
            after = self.status(document_id)
            checks = (
                ("the preserved file is gone", not stored.exists()),
                ("the document's SHA-256 is kept", after.file_hash == before.file_hash),
                ("every source record is kept", len(after.sources) == len(before.sources)),
                ("every knowledge object is kept", after.knowledge == before.knowledge),
                ("the source shows SOURCE_FILE_DELETED", after.status == "SOURCE_FILE_DELETED"),
            )
            verified = all(ok for _, ok in checks)
            now = utc_now()
            audit = self.repository.add(AuditEvent(
                id=self.repository.new_id(AuditEvent), created_at=now, updated_at=now, event_type=EVENT,
                occurred_at=now, actor="user", action=f"delete RUDRA's preserved copy {stored} ({size} bytes)",
                risk_level=RiskLevel.MEDIUM, authorization="the user's explicit --confirm",
                execution_status="EXECUTED",
                verification_status=VerificationStatus.VERIFIED if verified else VerificationStatus.FAILED,
                result="; ".join(f"{text}: {'yes' if ok else 'NO'}" for text, ok in checks),
                document_id=document_id,
            ))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return Deletion(document_id, "DELETED" if verified else "FAILED",
                        ("RUDRA's preserved copy was deleted and the deletion verified; the knowledge and its "
                         "provenance remain, and the source shows SOURCE_FILE_DELETED. Uploading the same file "
                         "again restores it." if verified else "The deletion could not be fully verified."),
                        before, after, audit.id, checks)
