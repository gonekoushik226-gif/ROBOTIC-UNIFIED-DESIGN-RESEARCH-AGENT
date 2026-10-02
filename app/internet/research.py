"""Local knowledge first, then the user's authorized site (ADR 0050 P18-2 ... P18-10;
sections 124-129, 217).

    1. interpret   a "What is X?" question: its subject X (Phase 13)
    2. local       the concept query in the requested scope (Phase 9)
    3. insufficient  without a FOUND local answer and without --site: "Insufficient
                   authorized information." and the authorization it would need
    4. authorize   --site: the user's grant, recorded with the request (section 129)
    5. retrieve    the one page, within the limits (`retrieval`)
    6. search      its sentences naming X, at most five (P18-5)
    7. record      the page as a document with one segment, a source
                   AUTHORIZED_EXTERNAL_SOURCE with the URL, each sentence a CLAIM with
                   its span (P18-6); the same page again is reused (P18-10)
    8. conflicts   rule C1 against the local definitions (P18-8)
    9. answer      labelled SOURCE / URL / Retrieved / Status EXTERNAL_INFORMATION (P18-7)

The caller opens the database for writing only when a site is named, and commits.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.core.errors import InvalidInputError
from app.deduplication.rules import c1_contradicts
from app.internet.retrieval import Retrieved, retrieve
from app.internet.sites import Site, site
from app.models.base import utc_now
from app.models.entities import (
    Conflict,
    Document,
    DocumentSegment,
    KnowledgeObject,
    MemoryItem,
    Source,
    SourceOccurrence,
)
from app.models.enums import (
    Authorization,
    CertaintyState,
    ConflictCause,
    DocumentProcessingStatus,
    KnowledgeType,
    LifecycleStatus,
    MemoryCategory,
    SourceAvailability,
    SourceCategory,
    TextOrigin,
)
from app.models.naming import normalize_alias
from app.nlu import InterpretationStatus, interpret
from app.query import AnswerStatus, QueryEngine, QueryRequest, SourceScope
from app.storage import queries
from app.storage.repository import Repository

EXTERNAL_METHOD = "external/sentence@v1"
SEGMENT_METHOD = "html.parser (standard library)"
GRANT_PREFIX = "internet-authorization:"
MAX_SENTENCES = 5
INSUFFICIENT = "Insufficient authorized information."
NEEDED = ("Internet authorization is needed to answer it: RUDRA searches only a website you name - run "
          "the question again with --site URL. Official, academic, government, manufacturer, reputable "
          "or broad search needs a search provider RUDRA does not have.")


class ResearchStatus(StrEnum):
    #: Local knowledge in scope answers the question.
    LOCAL = "LOCAL"
    #: It does not, and no site was named: "Insufficient authorized information."
    INSUFFICIENT = "INSUFFICIENT_AUTHORIZED_INFORMATION"
    #: The authorized site answers it: external information, labelled.
    EXTERNAL = "EXTERNAL_INFORMATION"
    #: The authorized site was retrieved but names nothing about the subject.
    NOT_ON_SITE = "NOT_ANSWERED_BY_THE_SITE"


@dataclass(frozen=True, slots=True)
class ExternalClaim:
    knowledge_id: str
    occurrence_id: str
    statement: str
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class ExternalRecord:
    """Where external information came from (section 126)."""

    url: str
    final_url: str
    retrieved_at: str
    document_id: str
    source_id: str
    sha256: str
    title: str | None
    content_type: str
    authorization: str
    grant_id: str
    #: False when the same page (URL and content) was already stored (P18-10).
    new: bool


@dataclass(frozen=True, slots=True)
class LocalAnswer:
    status: str
    message: str
    #: The local definitions of the subject in scope: (knowledge id, statement).
    definitions: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ConflictRecord:
    conflict_id: str
    local_id: str
    local_statement: str
    external_id: str
    external_statement: str


@dataclass(frozen=True, slots=True)
class ResearchAnswer:
    question: str
    subject: str
    status: ResearchStatus
    message: str
    scope: str
    local: LocalAnswer
    site: str | None = None
    record: ExternalRecord | None = None
    external: tuple[ExternalClaim, ...] = ()
    conflicts: tuple[ConflictRecord, ...] = ()
    #: Stored external claims about the subject from earlier retrievals, shown in the
    #: `authorized` scope with their retrieval time (section 129).
    cached: tuple[tuple[str, str, str, str], ...] = ()
    labels: tuple[tuple[str, str], ...] = ()


def refuse(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="internet.research", data_changed=False, retry_safe=True,
        next_options=('python -m app research "What is a memristor?"',
                      'python -m app research "What is a memristor?" --site https://example.org/memristor.html'),
    )


def subject_of(question: str) -> str:
    """The subject of a "What is X?" question (Phase 13), or refused."""
    interpretation = interpret(question)
    intents = interpretation.intents
    if (interpretation.status is not InterpretationStatus.INTERPRETED or len(intents) != 1
            or intents[0].intent_type != "QUERY_CONCEPT" or not intents[0].target):
        raise refuse(f"{question!r} is not a question research can answer.",
                     "Ask what a thing is, e.g. \"What is a memristor?\".")
    return intents[0].target


def sentences_naming(text: str, subject: str) -> tuple[tuple[int, int, str], ...]:
    """The page's sentences that name the subject (D-30), with their spans, at most five."""
    wanted = normalize_alias(subject)
    found, start = [], 0
    for index, char in enumerate(text + "\n"):
        boundary = char == "\n" or (char in ".!?" and (index + 1 >= len(text) or text[index + 1].isspace()))
        if not boundary:
            continue
        end = index + (0 if char == "\n" else 1)
        piece = text[start:end]
        stripped = piece.strip()
        if stripped:
            first = start + piece.index(stripped)
            if wanted in normalize_alias(stripped):
                found.append((first, first + len(stripped), " ".join(stripped.split())))
        start = index + 1
        if len(found) == MAX_SENTENCES:
            break
    return tuple(found)


class Research:
    def __init__(self, repository: Repository | None, *, database_path: str | Path, documents_dir: Path,
                 fetch: Callable[[Site], Retrieved] = retrieve) -> None:
        self.repository = repository
        self.database_path = database_path
        self.documents_dir = documents_dir
        self.fetch = fetch

    def ask(self, question: str, *, site_url: str | None = None,
            scope: SourceScope = SourceScope.MY_BOOKS) -> ResearchAnswer:
        subject = subject_of(question)
        authorized = None if site_url is None else site(site_url)
        local = self._local(subject, scope)
        cached = self._cached(subject) if scope is SourceScope.AUTHORIZED else ()
        if authorized is None:
            if local.status == AnswerStatus.FOUND.value:
                return ResearchAnswer(question, subject, ResearchStatus.LOCAL, local.message, scope.value, local,
                                      cached=cached)
            return ResearchAnswer(question, subject, ResearchStatus.INSUFFICIENT, f"{INSUFFICIENT}\n{NEEDED}",
                                  scope.value, local, cached=cached)
        if self.repository is None:
            raise refuse("There is no knowledge database to record into.", "Extract a document first.")
        retrieved = self.fetch(authorized)  # RetrievalFailed propagates: nothing is stored
        found = sentences_naming(retrieved.text, subject)
        if not found:
            return ResearchAnswer(question, subject, ResearchStatus.NOT_ON_SITE,
                                  f"The authorized site does not answer the question: no sentence of "
                                  f"{retrieved.final_url} names {subject!r}. Nothing was stored.",
                                  scope.value, local, site=authorized.scope, cached=cached)
        # The authorization context is recorded with what it let in (section 129).
        grant = self._grant(authorized, question)
        record, claims = self._record(authorized, retrieved, subject, found, grant)
        conflicts = self._conflicts(local, claims)
        labels = (("SOURCE", SourceCategory.AUTHORIZED_EXTERNAL_SOURCE.value), ("URL", record.final_url),
                  ("Retrieved", record.retrieved_at), ("Status", "EXTERNAL_INFORMATION"))
        message = (f"{len(claims)} sentence(s) from the authorized site name {subject!r}; they are external "
                   f"information, not your documents'.")
        if local.status != AnswerStatus.FOUND.value:
            message = f"{INSUFFICIENT} (local, scope {scope.value}) - {message}"
        return ResearchAnswer(question, subject, ResearchStatus.EXTERNAL, message, scope.value, local,
                              site=authorized.scope, record=record, external=claims, conflicts=conflicts,
                              cached=cached, labels=labels)

    # ------------------------------------------------------------ the parts

    def _local(self, subject: str, scope: SourceScope) -> LocalAnswer:
        if self.repository is None:
            return LocalAnswer(AnswerStatus.NOT_FOUND.value, "There is no knowledge database.", ())
        engine = QueryEngine(self.repository.connection, database_path=self.database_path)
        result = engine.run(QueryRequest.concept(subject, scope=scope))
        definitions = []
        if result.concept is not None:
            for group in result.concept.groups:
                for item in group.items:
                    knowledge = getattr(item, "knowledge", None)
                    if knowledge is not None and knowledge.knowledge_type is KnowledgeType.DEFINITION:
                        definitions.append((knowledge.id, knowledge.statement))
        return LocalAnswer(result.status.value, result.message, tuple(dict.fromkeys(definitions)))

    def _cached(self, subject: str) -> tuple[tuple[str, str, str, str], ...]:
        if self.repository is None:
            return ()
        rows = queries.external_claims(self.repository.connection, _canonical(subject), EXTERNAL_METHOD)
        return tuple((k.id, k.statement, o.extraction_timestamp, o.document_id) for k, o in rows)

    def _grant(self, authorized: Site, question: str) -> str:
        now = utc_now()
        row = self.repository.add(MemoryItem(
            id=self.repository.new_id(MemoryItem), created_at=now, updated_at=now,
            category=MemoryCategory.SOURCE_MEMORY, key=GRANT_PREFIX + authorized.url,
            value=json.dumps({"scope": "SPECIFIC_WEBSITE", "site": authorized.scope, "request": question},
                             sort_keys=True, ensure_ascii=False),
            lifecycle_status=LifecycleStatus.ACTIVE, observed_at=now,
        ))
        return row.id

    def _record(self, authorized: Site, retrieved: Retrieved, subject: str, found, grant: str):
        repo = self.repository
        existing = queries.document_by_hash(repo.connection, retrieved.sha256)
        source = None
        if existing is not None:
            source = next((s for s in queries.sources_for_document(repo.connection, existing.id)
                           if s.url == retrieved.final_url), None)
            if source is not None:
                claims = tuple(
                    ExternalClaim(o.knowledge_id, o.id, repo.get(KnowledgeObject, o.knowledge_id).statement,
                                  o.char_start, o.char_end)
                    for o in queries.occurrences_by_method(repo.connection, existing.id, EXTERNAL_METHOD)
                    if normalize_alias(subject) in normalize_alias(o.original_text))
                if claims:
                    return self._external_record(retrieved, existing.id, source.id, grant, new=False), claims
        now = utc_now()
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".html" if retrieved.content_type == "text/html" else ".txt"
        stored = self.documents_dir / f"{retrieved.sha256}{suffix}"
        if not stored.exists():
            with open(stored, "xb") as handle:
                handle.write(retrieved.data)
        if existing is not None:
            document_id = existing.id
            segment = queries.segments_for_document(repo.connection, document_id)[0]
        else:
            document_id = repo.add(Document(
                id=repo.new_id(Document), created_at=now, updated_at=now, filename=stored.name,
                original_filename=retrieved.final_url.rsplit("/", 1)[-1] or authorized.host,
                source_type="WEB", file_path=str(stored), file_hash=retrieved.sha256,
                file_size=len(retrieved.data), mime_type=retrieved.content_type,
                ingested_at=retrieved.retrieved_at,
                processing_status=DocumentProcessingStatus.PARTIALLY_PROCESSED, processing_version=1,
                document_title=retrieved.title, page_count=1,
            )).id
            segment = repo.add(DocumentSegment(
                id=repo.new_id(DocumentSegment), created_at=now, updated_at=now, document_id=document_id,
                page_number=1, ordinal=0, text=retrieved.text, extraction_method=SEGMENT_METHOD,
                text_origin=TextOrigin.NATIVE_TEXT,
            ))
        source_id = source.id if source is not None else repo.add(Source(
            id=repo.new_id(Source), created_at=now, updated_at=now,
            name=retrieved.title or authorized.host,
            source_category=SourceCategory.AUTHORIZED_EXTERNAL_SOURCE, authorization=Authorization.AUTHORIZED,
            availability=SourceAvailability.EXTERNAL_ONLY, document_id=document_id, url=retrieved.final_url,
            file_hash=retrieved.sha256,
        )).id
        claims = []
        for start, end, sentence in found:
            knowledge_id = repo.add(KnowledgeObject(
                id=repo.new_id(KnowledgeObject), created_at=now, updated_at=now,
                knowledge_type=KnowledgeType.CLAIM, canonical_name=_canonical(subject), statement=sentence,
                lifecycle_status=LifecycleStatus.ACTIVE, certainty=CertaintyState.REPORTED_BY_SOURCE,
                knowledge_version=1,
            )).id
            occurrence = repo.add(SourceOccurrence(
                id=repo.new_id(SourceOccurrence), created_at=now, updated_at=now, knowledge_id=knowledge_id,
                source_id=source_id, document_id=document_id, original_text=sentence,
                extraction_method=EXTERNAL_METHOD, extraction_timestamp=retrieved.retrieved_at,
                segment_id=segment.id, page_number=1, char_start=start, char_end=end,
            ))
            claims.append(ExternalClaim(knowledge_id, occurrence.id, sentence, start, end))
        return self._external_record(retrieved, document_id, source_id, grant, new=True), tuple(claims)

    @staticmethod
    def _external_record(retrieved: Retrieved, document_id: str, source_id: str, grant: str, *, new: bool):
        return ExternalRecord(retrieved.url, retrieved.final_url, retrieved.retrieved_at, document_id, source_id,
                              retrieved.sha256, retrieved.title, retrieved.content_type,
                              Authorization.AUTHORIZED.value, grant, new)

    def _conflicts(self, local: LocalAnswer, claims: tuple[ExternalClaim, ...]) -> tuple[ConflictRecord, ...]:
        records = []
        for local_id, local_statement in local.definitions:
            for claim in claims:
                if not c1_contradicts(local_statement, claim.statement):
                    continue
                now = utc_now()
                conflict = self.repository.add(Conflict(
                    id=self.repository.new_id(Conflict), created_at=now, updated_at=now,
                    claim_a_id=local_id, claim_b_id=claim.knowledge_id, cause=ConflictCause.UNDETERMINED,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                    context="local definition vs authorized external source (ADR 0050 P18-8, rule C1)",
                ))
                records.append(ConflictRecord(conflict.id, local_id, local_statement, claim.knowledge_id,
                                              claim.statement))
        return tuple(records)


def _canonical(subject: str) -> str:
    return f"External: {normalize_alias(subject)}"
