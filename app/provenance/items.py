"""The provenance of a stored item: *"Where did you get this?"* (ADR 0044 P12-4 ... P12-8).

`ProvenanceService.of_item(identifier, scope)` answers for one stored item, named by its
identifier (P12-2), with section 49's fields read as stored and the P12-8 checks:

    K        a knowledge object: its evidence, and its transformation history
    CPT      a concept: its evidence, and its equivalence records
    REL      a relationship: its evidence, its origin, and an INFERRED edge's bases
    EQ VAR RUL PRC   a row carrying a knowledge object: that object's evidence
    CON      a conflict: each claim's provenance, a withheld claim by identifier only
    S CO RO  an occurrence: the evidence row itself
    DOC SRC RUN SEG DV   identity-level provenance: the document, its sources, the file
                         check, its runs, a segment's page, a declared edition

Any other kind is answered *"Provenance unavailable."*: RUDRA records no provenance for it.

**Nothing is invented** (section 205): only stored rows are shown, a NULL field stays
unknown, and a location is never searched for. **Scope** (P9-5): evidence from a source
not authorised in the requested scope is withheld, counted and never shown; an item with
no evidence in scope is identified, but its content is not shown. **Lifecycle** (P9-23):
an item stored `DELETED` or `ARCHIVED` is reported as excluded. Everything here reads;
nothing writes.
"""

from collections.abc import Iterable

from app.core.errors import InvalidInputError, StorageError
from app.models.entities import (
    ENTITIES,
    Concept,
    Conflict,
    Document,
    DocumentSegment,
    DocumentVersion,
    Equation,
    ExtractionRun,
    KnowledgeObject,
    Procedure,
    Relationship,
    Rule,
    Source,
    Variable,
)
from app.models.enums import KnowledgeEquivalenceOutcome, LifecycleStatus
from app.models.identifiers import EntityKind, is_valid_id, parse_id
from app.provenance.checks import FileHashes, file_check, quote_check
from app.provenance.results import (
    UNAVAILABLE_TEXT,
    Basis,
    Citation,
    DocumentRecord,
    EvidenceRow,
    History,
    ItemProvenance,
    ProvenanceStatus,
    Withheld,
    combined,
)
from app.provenance.scope import (
    ProvenanceScope,
    Verdict,
    counter,
    evidence_key,
    lifecycle_excluded,
    source_verdict,
)
from app.storage import CODE_SCHEMA_VERSION, queries, schema_version
from app.storage.repository import Repository

_ENTITY_BY_KIND = {cls.KIND: cls for cls in ENTITIES}
_CARRIERS = {
    EntityKind.EQUATION: Equation,
    EntityKind.VARIABLE: Variable,
    EntityKind.RULE: Rule,
    EntityKind.PROCEDURE: Procedure,
}
_OCCURRENCES = {EntityKind.SOURCE_OCCURRENCE, EntityKind.CONCEPT_OCCURRENCE, EntityKind.RELATIONSHIP_OCCURRENCE}


def refuse(summary: str, reason: str) -> InvalidInputError:
    """An invalid provenance request (exit code 2); nothing was read or changed."""
    return InvalidInputError.of(
        summary, reason, stage="provenance.request", data_changed=False, retry_safe=True,
        next_options=("python -m app provenance K-00000001",
                      "python -m app provenance --answer answer.json"),
    )


class _Reading:
    """One request's reads, scope verdicts and withheld counts."""

    def __init__(self, repository: Repository, scope: ProvenanceScope, hashes: FileHashes) -> None:
        self.repository = repository
        self.connection = repository.connection
        self.scope = scope
        self.hashes = hashes
        self._sources: dict[str, Source | None] = {}
        self._verdicts: dict[str, Verdict] = {}
        self.bases_withheld = 0
        self.claims_withheld = 0

    def source(self, source_id: str) -> Source | None:
        if source_id not in self._sources:
            self._sources[source_id] = self.repository.get(Source, source_id)
        return self._sources[source_id]

    def verdict(self, row: EvidenceRow) -> Verdict:
        if row.id not in self._verdicts:
            self._verdicts[row.id] = source_verdict(self.source(row.source_id), self.scope)
        return self._verdicts[row.id]

    def rows(self, subject_id: str) -> tuple[EvidenceRow, ...]:
        found = (EvidenceRow.of(row) for row in queries.evidence_for(self.connection, subject_id))
        return tuple(sorted(found, key=evidence_key))

    def cite(self, row: EvidenceRow) -> Citation:
        run = None if row.extraction_run_id is None else self.repository.get(ExtractionRun, row.extraction_run_id)
        version = (None if row.document_version_id is None
                   else self.repository.get(DocumentVersion, row.document_version_id))
        return Citation(row, self.source(row.source_id), run, version, quote_check(self.repository, row))  # type: ignore[arg-type]

    def citations(self, subject_id: str) -> tuple[tuple[Citation, ...], int]:
        """The subject's evidence in scope, cited, and how many rows it has in all."""
        rows = self.rows(subject_id)
        return tuple(self.cite(row) for row in rows if self.verdict(row) is Verdict.IN_SCOPE), len(rows)

    def document_in_scope(self, document_id: str | None) -> tuple[Source, ...]:
        """A document's sources in scope; empty when none is (P9-5)."""
        if document_id is None:
            return ()
        sources = queries.sources_for_document(self.connection, document_id)
        return tuple(sorted((s for s in sources if source_verdict(s, self.scope) is Verdict.IN_SCOPE),
                            key=lambda s: counter(s.id)))

    def documents(self, document_ids: Iterable[str]) -> tuple[DocumentRecord, ...]:
        records = []
        for document_id in sorted(set(document_ids), key=counter):
            document = self.repository.get(Document, document_id)
            if document is None:
                continue
            present, check = file_check(document, self.hashes)
            editions = tuple(v for v in queries.document_versions_by_hash(self.connection, document.file_hash))
            records.append(DocumentRecord(document, present, editions, check))
        return tuple(records)

    def withheld(self) -> Withheld:
        verdicts = list(self._verdicts.values())
        return Withheld(
            unauthorized_evidence=verdicts.count(Verdict.NOT_AUTHORIZED),
            out_of_scope_evidence=verdicts.count(Verdict.OUT_OF_SCOPE),
            bases=self.bases_withheld,
            claims=self.claims_withheld,
        )


class ProvenanceService:
    """Answers *"Where did you get this?"* for stored items, read-only (ADR 0044)."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self.hashes = FileHashes()

    @staticmethod
    def check_identifier(identifier: object) -> None:
        """Refuse text that is not a RUDRA identifier, before any database is read."""
        if not isinstance(identifier, str) or not is_valid_id(identifier):
            raise refuse(f"{identifier!r} is not a RUDRA identifier.",
                         "Name one stored item by its identifier, e.g. K-00000001 or CPT-00000001.")

    def of_item(self, identifier: str, scope: ProvenanceScope = ProvenanceScope.MY_BOOKS) -> ItemProvenance:
        """Raises `InvalidInputError` for text that is not an identifier, `StorageError` for a
        database this build cannot read; otherwise answers."""
        self.check_identifier(identifier)
        if not isinstance(scope, ProvenanceScope):
            raise refuse("The source scope is not one of MY_BOOKS or AUTHORIZED.", f"Got {scope!r}.")
        self._require_schema()
        kind = parse_id(identifier)[0]
        reading = _Reading(self.repository, scope, self.hashes)
        if kind is EntityKind.KNOWLEDGE_OBJECT:
            return self._knowledge(reading, identifier)
        if kind is EntityKind.CONCEPT:
            return self._concept(reading, identifier)
        if kind is EntityKind.RELATIONSHIP:
            return self._relationship(reading, identifier)
        if kind in _CARRIERS:
            return self._carrier(reading, identifier, kind)
        if kind is EntityKind.CONFLICT:
            return self._conflict(reading, identifier)
        if kind in _OCCURRENCES:
            return self._occurrence(reading, identifier, kind)
        if kind in (EntityKind.DOCUMENT, EntityKind.SOURCE, EntityKind.EXTRACTION_RUN,
                    EntityKind.DOCUMENT_SEGMENT, EntityKind.DOCUMENT_VERSION):
            return self._identity(reading, identifier, kind)
        return self._other(reading, identifier, kind)

    def _require_schema(self) -> None:
        """Refuse a database whose schema is not this build's; never migrate (P12-15)."""
        version = schema_version(self.repository.connection)
        if version != CODE_SCHEMA_VERSION:
            raise StorageError.of(
                "The knowledge database's schema version is not the one this build reads; "
                "provenance never migrates.",
                f"Its schema version is {version}; this build reads version {CODE_SCHEMA_VERSION}.",
                stage="provenance.schema", data_changed=False, retry_safe=True,
                next_options=(
                    "Check that the project root names the intended project.",
                    "Migrate explicitly with: python -m app db  (this writes to the database; "
                    "for the live database make a fresh D-15 backup first).",
                ),
            )

    # ------------------------------------------------------------ the answers

    @staticmethod
    def _answer(reading: _Reading, identifier: str, kind: EntityKind, status: ProvenanceStatus,
                message: str, **fields) -> ItemProvenance:
        checks = fields.pop("checks", ())
        return ItemProvenance(
            identifier=identifier, kind=kind.name, status=status, message=message,
            scope=reading.scope.value, checks=checks, verification=combined(checks),
            withheld=reading.withheld(), **fields,
        )

    def _not_found(self, reading: _Reading, identifier: str, kind: EntityKind) -> ItemProvenance:
        return self._answer(reading, identifier, kind, ProvenanceStatus.NOT_FOUND,
                            f"No stored item has the identifier {identifier}; nothing is cited.")

    def _excluded(self, reading: _Reading, identifier: str, kind: EntityKind, status) -> ItemProvenance:
        return self._answer(
            reading, identifier, kind, ProvenanceStatus.EXCLUDED,
            f"{identifier} is stored {status.value}: it is excluded from use, and its provenance is "
            "not shown (P9-23).",
        )

    def _unavailable(self, reading: _Reading, identifier: str, kind: EntityKind, reason: str,
                     **fields) -> ItemProvenance:
        return self._answer(reading, identifier, kind, ProvenanceStatus.UNAVAILABLE,
                            f"{UNAVAILABLE_TEXT} {reason}", **fields)

    def _no_evidence_reason(self, reading: _Reading, total: int) -> str:
        if total == 0:
            return "No evidence is stored for it; no citation exists, and none is made."
        return (f"Its {total} stored evidence row(s) come only from sources not authorized in the "
                f"requested scope ({reading.scope.value}); they are withheld and counted, never shown (P9-5).")

    def _cited(self, reading: _Reading, identifier: str, kind: EntityKind, item: object,
               citations: tuple[Citation, ...], *, subject_id: str, history: History | None = None,
               origin: str | None = None, bases: tuple[Basis, ...] = ()) -> ItemProvenance:
        cited = list(citations) + [b.citation for b in bases if b.citation is not None]
        documents = reading.documents(c.evidence.document_id for c in cited)
        checks = tuple(c.quote for c in cited) + tuple(d.file for d in documents)
        return self._answer(
            reading, identifier, kind, ProvenanceStatus.AVAILABLE,
            f"Provenance available: {len(citations)} piece(s) of evidence"
            + (f" and {len([b for b in bases if b.citation])} inference basis(es)" if bases else "")
            + f" from {len(documents)} document(s), in scope {reading.scope.value}.",
            item=item, subject_id=subject_id, citations=citations, documents=documents,
            origin=origin, bases=bases, history=history, checks=checks,
        )

    def _knowledge(self, reading: _Reading, identifier: str) -> ItemProvenance:
        kind = EntityKind.KNOWLEDGE_OBJECT
        knowledge = self.repository.get(KnowledgeObject, identifier)
        if knowledge is None:
            return self._not_found(reading, identifier, kind)
        if lifecycle_excluded(knowledge.lifecycle_status):
            return self._excluded(reading, identifier, kind, knowledge.lifecycle_status)
        citations, total = reading.citations(identifier)
        if not citations:
            return self._unavailable(reading, identifier, kind, self._no_evidence_reason(reading, total))
        records = queries.equivalences_of_knowledge(reading.connection, identifier)
        history = History(
            lifecycle_status=knowledge.lifecycle_status.value,
            knowledge_version=knowledge.knowledge_version,
            certainty=knowledge.certainty.value,
            assessments=tuple(
                (r.id, r.outcome.value, r.other_knowledge_id or r.canonical_knowledge_id)
                for r in sorted(records, key=lambda r: counter(r.id))
            ),
            superseded_by=self._pointer(records, identifier)
            if knowledge.lifecycle_status is LifecycleStatus.SUPERSEDED else None,
        )
        return self._cited(reading, identifier, kind, knowledge, citations, subject_id=identifier, history=history)

    @staticmethod
    def _pointer(records, identifier: str) -> str | None:
        """The stored merge pointer (ADR 0033 P8-19), read as stored, never inferred."""
        for record in sorted(records, key=lambda r: counter(r.id)):
            if (record.outcome is KnowledgeEquivalenceOutcome.EXACT_DUPLICATE
                    and record.other_knowledge_id == identifier and record.extraction_run_id is None):
                return record.canonical_knowledge_id
        return None

    def _concept(self, reading: _Reading, identifier: str) -> ItemProvenance:
        kind = EntityKind.CONCEPT
        concept = self.repository.get(Concept, identifier)
        if concept is None:
            return self._not_found(reading, identifier, kind)
        if lifecycle_excluded(concept.lifecycle_status):
            return self._excluded(reading, identifier, kind, concept.lifecycle_status)
        citations, total = reading.citations(identifier)
        if not citations:
            return self._unavailable(reading, identifier, kind, self._no_evidence_reason(reading, total))
        records = queries.equivalences_of_concept(reading.connection, identifier)
        history = History(
            lifecycle_status=concept.lifecycle_status.value,
            assessments=tuple(
                (r.id, r.status.value, r.concept_b_id if r.concept_a_id == identifier else r.concept_a_id)
                for r in records
            ),
        )
        return self._cited(reading, identifier, kind, concept, citations, subject_id=identifier, history=history)

    def _relationship(self, reading: _Reading, identifier: str) -> ItemProvenance:
        kind = EntityKind.RELATIONSHIP
        relationship = self.repository.get(Relationship, identifier)
        if relationship is None:
            return self._not_found(reading, identifier, kind)
        if lifecycle_excluded(relationship.lifecycle_status):
            return self._excluded(reading, identifier, kind, relationship.lifecycle_status)
        citations, total = reading.citations(identifier)
        bases = []
        for inference in queries.inferences_for_relationship(reading.connection, identifier):
            row = queries.evidence_row(reading.connection, inference.basis_occurrence_id)
            evidence = None if row is None else EvidenceRow.of(row)
            if evidence is not None and reading.verdict(evidence) is Verdict.IN_SCOPE:
                bases.append(Basis(inference, reading.cite(evidence)))
            else:
                reading.bases_withheld += 1
                bases.append(Basis(inference, None))
        if not citations and not any(b.citation for b in bases):
            reason = self._no_evidence_reason(reading, total)
            if bases:
                reason = (f"Its {len(bases)} recorded inference basis(es) rest on evidence not "
                          f"authorized in the requested scope ({reading.scope.value}); withheld (P9-5).")
            return self._unavailable(reading, identifier, kind, reason)
        history = History(lifecycle_status=relationship.lifecycle_status.value)
        return self._cited(reading, identifier, kind, relationship, citations, subject_id=identifier,
                           history=history, origin=relationship.origin.value,
                           bases=tuple(b for b in bases if b.citation is not None))

    def _carrier(self, reading: _Reading, identifier: str, kind: EntityKind) -> ItemProvenance:
        row = self.repository.get(_CARRIERS[kind], identifier)
        if row is None:
            return self._not_found(reading, identifier, kind)
        if lifecycle_excluded(row.lifecycle_status):
            return self._excluded(reading, identifier, kind, row.lifecycle_status)
        if row.knowledge_id is None:
            return self._unavailable(reading, identifier, kind,
                                     "It is stored without a knowledge object, and no evidence is "
                                     "recorded for it; no citation exists, and none is made.")
        owner = self._knowledge(reading, row.knowledge_id)
        if owner.status is not ProvenanceStatus.AVAILABLE:
            reason = owner.message.removeprefix(UNAVAILABLE_TEXT).strip()
            return self._unavailable(reading, identifier, kind,
                                     f"Its knowledge object {row.knowledge_id}: {reason}")
        return ItemProvenance(
            identifier=identifier, kind=kind.name, status=ProvenanceStatus.AVAILABLE,
            message=f"{owner.message} (through its knowledge object {row.knowledge_id})",
            scope=reading.scope.value, item=row, subject_id=row.knowledge_id,
            citations=owner.citations, documents=owner.documents, history=owner.history,
            checks=owner.checks, verification=owner.verification, withheld=owner.withheld,
        )

    def _conflict(self, reading: _Reading, identifier: str) -> ItemProvenance:
        kind = EntityKind.CONFLICT
        conflict = self.repository.get(Conflict, identifier)
        if conflict is None:
            return self._not_found(reading, identifier, kind)
        if lifecycle_excluded(conflict.lifecycle_status):
            return self._excluded(reading, identifier, kind, conflict.lifecycle_status)
        claims = tuple(self._knowledge(reading, claim) for claim in (conflict.claim_a_id, conflict.claim_b_id))
        reading.claims_withheld = sum(1 for c in claims if c.status is not ProvenanceStatus.AVAILABLE)
        shown = [c for c in claims if c.status is ProvenanceStatus.AVAILABLE]
        if not shown:
            return self._unavailable(reading, identifier, kind,
                                     "Neither claim has evidence in the requested scope; both are "
                                     "withheld (P9-5).", claims=claims)
        checks = tuple(check for c in shown for check in c.checks)
        return self._answer(
            reading, identifier, kind, ProvenanceStatus.AVAILABLE,
            f"Provenance available for {len(shown)} of the conflict's 2 claims; the conflict is "
            "shown with both claims, neither chosen (section 229).",
            item=conflict, claims=claims, checks=checks,
        )

    def _occurrence(self, reading: _Reading, identifier: str, kind: EntityKind) -> ItemProvenance:
        row = queries.evidence_row(reading.connection, identifier)
        if row is None:
            return self._not_found(reading, identifier, kind)
        evidence = EvidenceRow.of(row)
        if reading.verdict(evidence) is not Verdict.IN_SCOPE:
            return self._unavailable(reading, identifier, kind, self._no_evidence_reason(reading, 1))
        return self._cited(reading, identifier, kind, evidence, (reading.cite(evidence),),
                           subject_id=evidence.subject_id)

    def _identity(self, reading: _Reading, identifier: str, kind: EntityKind) -> ItemProvenance:
        entity = {
            EntityKind.DOCUMENT: Document, EntityKind.SOURCE: Source, EntityKind.EXTRACTION_RUN: ExtractionRun,
            EntityKind.DOCUMENT_SEGMENT: DocumentSegment, EntityKind.DOCUMENT_VERSION: DocumentVersion,
        }[kind]
        row = self.repository.get(entity, identifier)
        if row is None:
            return self._not_found(reading, identifier, kind)
        if kind is EntityKind.SOURCE:
            in_scope = (row,) if source_verdict(row, reading.scope) is Verdict.IN_SCOPE else ()
            document_id = row.document_id
        elif kind is EntityKind.DOCUMENT_VERSION:
            document = queries.document_by_hash(reading.connection, row.file_hash)
            document_id = None if document is None else document.id
            in_scope = reading.document_in_scope(document_id)
        else:
            document_id = row.id if kind is EntityKind.DOCUMENT else row.document_id
            in_scope = reading.document_in_scope(document_id)
        if not in_scope:
            return self._unavailable(reading, identifier, kind,
                                     "No source of it is authorized in the requested scope "
                                     f"({reading.scope.value}); it is withheld (P9-5).")
        documents = reading.documents([document_id] if document_id else [])
        runs = queries.runs_for_document(reading.connection, document_id) if document_id else ()
        checks = tuple(d.file for d in documents)
        return self._answer(
            reading, identifier, kind, ProvenanceStatus.AVAILABLE,
            f"Provenance available: {kind.name.lower().replace('_', ' ')} identity, from "
            f"{len(in_scope)} source(s) in scope {reading.scope.value}.",
            item=row, sources=in_scope, documents=documents, runs=tuple(runs), checks=checks,
        )

    def _other(self, reading: _Reading, identifier: str, kind: EntityKind) -> ItemProvenance:
        entity = _ENTITY_BY_KIND.get(kind)
        if entity is None or self.repository.get(entity, identifier) is None:
            return self._not_found(reading, identifier, kind)
        return self._unavailable(reading, identifier, kind,
                                 f"RUDRA records no provenance for this kind of item ({kind.name}).")
