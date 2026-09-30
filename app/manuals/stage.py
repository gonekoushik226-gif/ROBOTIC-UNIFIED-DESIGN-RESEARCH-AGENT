"""The manual stage (ADR 0049 P17-3): section 214's items from a declared manual.

It reads the declared document's **stored page segments** - never the file, never the
parser or the Phase 5 extractor - and writes, for each item the detectors find:

    MENU_PATH, SHORTCUT   a PROCEDURE knowledge object and a DOCUMENTED_PROCEDURE procedure
    CONSTRAINT            a CONSTRAINT knowledge object
    FILE_FORMAT           a PROPERTY knowledge object

each with a source occurrence carrying the page, the span and the verbatim (flattened)
text, and the method `deterministic/manual.KIND@v1`. The same item stated twice in one
manual is one object with two occurrences; the same text in two manuals is two objects,
because an application's procedure is its own (P17-3). The stage runs once per document.
The caller commits.
"""

from dataclasses import dataclass

from app.core.errors import InvalidInputError
from app.manuals.declarations import declaration_of
from app.manuals.detectors import CONSTRAINT, FILE_FORMAT, Found, detect
from app.models.base import utc_now
from app.models.entities import KnowledgeObject, Procedure, SourceOccurrence
from app.models.enums import (
    CertaintyState,
    KnowledgeType,
    LifecycleStatus,
    ProcedureDocumentationStatus,
)
from app.storage import queries
from app.storage.repository import Repository

STAGE_VERSION = "1"
METHOD_PREFIX = "deterministic/manual."
KINDS = ("MENU_PATH", "SHORTCUT", CONSTRAINT, FILE_FORMAT)
_TYPES = {"MENU_PATH": KnowledgeType.PROCEDURE, "SHORTCUT": KnowledgeType.PROCEDURE,
          CONSTRAINT: KnowledgeType.CONSTRAINT, FILE_FORMAT: KnowledgeType.PROPERTY}


def method(kind: str) -> str:
    return f"{METHOD_PREFIX}{kind}@v{STAGE_VERSION}"


@dataclass(frozen=True, slots=True)
class StageReport:
    document_id: str
    application: str
    #: False when the stage had already run for this document: nothing was written.
    ran: bool
    message: str
    #: Items found, per kind.
    found: tuple[tuple[str, int], ...]
    #: Knowledge objects created (an item repeated in the manual adds an occurrence).
    created: int
    procedures: int


class ManualStage:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def run(self, document_id: str) -> StageReport:
        connection = self.repository.connection
        declaration = declaration_of(self.repository, document_id)
        if declaration is None:
            raise InvalidInputError.of(
                f"{document_id} is not declared an application manual.",
                "The manual stage runs only over a document the user declared (ADR 0049 P17-2).",
                stage="manuals.stage", data_changed=False, retry_safe=True,
                next_options=(f'python -m app extract --re-extract {document_id} --manual "Application name"',),
            )
        if queries.occurrences_by_method(connection, document_id, METHOD_PREFIX):
            return StageReport(document_id, declaration.application, False,
                               "the manual stage has already run for this document; nothing was changed",
                               (), 0, 0)
        sources = queries.sources_for_document(connection, document_id)
        if not sources:
            raise InvalidInputError.of(f"{document_id} has no source record.", "Evidence needs a source.",
                                       stage="manuals.stage", data_changed=False, retry_safe=False)
        source_id = sources[0].id
        found = {kind: 0 for kind in KINDS}
        seen: dict[tuple[str, str], str] = {}
        created = procedures = 0
        for segment in queries.segments_for_document(connection, document_id):
            for item in detect(segment.page_number, segment.text):
                found[item.kind] += 1
                key = (item.kind, item.statement)
                knowledge_id = seen.get(key)
                if knowledge_id is None:
                    knowledge_id = self._knowledge(item)
                    seen[key] = knowledge_id
                    created += 1
                    if _TYPES[item.kind] is KnowledgeType.PROCEDURE:
                        self._procedure(item, knowledge_id)
                        procedures += 1
                self._occurrence(item, knowledge_id, source_id, document_id, segment.id)
        return StageReport(document_id, declaration.application, True,
                           f"{sum(found.values())} item(s) found; {created} knowledge object(s), "
                           f"{procedures} of them procedures", tuple(found.items()), created, procedures)

    def _knowledge(self, item: Found) -> str:
        now = utc_now()
        return self.repository.add(KnowledgeObject(
            id=self.repository.new_id(KnowledgeObject), created_at=now, updated_at=now,
            knowledge_type=_TYPES[item.kind], canonical_name=item.name, statement=item.statement,
            lifecycle_status=LifecycleStatus.ACTIVE, certainty=CertaintyState.REPORTED_BY_SOURCE,
            knowledge_version=1,
        )).id

    def _procedure(self, item: Found, knowledge_id: str) -> None:
        now = utc_now()
        self.repository.add(Procedure(
            id=self.repository.new_id(Procedure), created_at=now, updated_at=now, name=item.name,
            # From a document, so DOCUMENTED (Part 3 section 111) - never VERIFIED.
            documentation_status=ProcedureDocumentationStatus.DOCUMENTED_PROCEDURE,
            lifecycle_status=LifecycleStatus.ACTIVE, execution_count=0, successful_executions=0,
            failed_executions=0, knowledge_id=knowledge_id,
        ))

    def _occurrence(self, item: Found, knowledge_id: str, source_id: str, document_id: str, segment_id: str) -> None:
        now = utc_now()
        self.repository.add(SourceOccurrence(
            id=self.repository.new_id(SourceOccurrence), created_at=now, updated_at=now,
            knowledge_id=knowledge_id, source_id=source_id, document_id=document_id,
            original_text=item.text, extraction_method=method(item.kind), extraction_timestamp=now,
            segment_id=segment_id, page_number=item.page_number, char_start=item.start, char_end=item.end,
        ))
