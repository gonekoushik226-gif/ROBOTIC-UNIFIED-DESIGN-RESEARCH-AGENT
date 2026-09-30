"""Procedural memory: the procedures RUDRA stores, with their steps, parameters, source and
verification history (ADR 0048 P16-2 ... P16-4, P16-8 ... P16-10; Part 2 section 56).

The store is the `procedure` table, apart from `knowledge_object`, which holds only the
documented claim a procedure came from (section 212; ADR 0019 D-37). Reading never
writes. `record` is the one write: one live run's verification and counters, in one
transaction (P16-8).
"""

from dataclasses import dataclass, replace
from enum import StrEnum

from app.core.errors import InvalidInputError, StorageError
from app.models.base import utc_now
from app.models.entities import KnowledgeObject, Procedure, Verification
from app.models.enums import ProcedureDocumentationStatus, VerificationStatus
from app.models.identifiers import EntityKind, is_valid_id, parse_id
from app.procedures.steps import parameters, recovered_steps
from app.provenance import ItemProvenance, ProvenanceScope, ProvenanceService, ProvenanceStatus
from app.provenance.scope import lifecycle_excluded
from app.storage import CODE_SCHEMA_VERSION, queries, schema_version
from app.storage.repository import Repository

#: Section 113: repeated execution is not proof of correctness under every environment.
ENVIRONMENT_NOTE = "verification holds for the recorded environments only (section 113)"


class LookupStatus(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    #: DELETED or ARCHIVED: never listed, shown or run (P9-23).
    EXCLUDED = "EXCLUDED"


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One recorded live run (a `verification` row whose subject is the procedure)."""

    id: str
    recorded: str
    status: str
    expected: str | None
    observed: str | None


@dataclass(frozen=True, slots=True)
class StoredProcedure:
    """Section 56's fields of one procedure, as stored and derived."""

    id: str
    name: str
    documentation_status: str
    lifecycle_status: str
    #: `documentation_status`, and `VERIFIED_PROCEDURE` beside it once a live run was fully
    #: verified (section 111: "may additionally have"; P16-9).
    labels: tuple[str, ...]
    knowledge_id: str | None
    steps: tuple[str, ...]
    parameters: tuple[str, ...]
    execution_count: int
    successful_executions: int
    failed_executions: int
    last_verified: str | None
    known_application_version: str | None
    limitations: str | None
    history: tuple[HistoryEntry, ...]
    #: Phase 12's provenance of the procedure, through its knowledge object.
    source: ItemProvenance
    executable: bool
    #: Why it cannot be executed as documented; "" when it can.
    reason: str
    #: The history in one sentence, with section 113's caution.
    note: str


@dataclass(frozen=True, slots=True)
class Lookup:
    status: LookupStatus
    identifier: str
    message: str
    procedure: StoredProcedure | None = None


def refuse(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="procedures", data_changed=False, retry_safe=True,
        next_options=("python -m app procedure", "python -m app procedure PRC-00000001"),
    )


class ProcedureMemory:
    """Retrieval and recording over one knowledge database."""

    def __init__(self, repository: Repository, scope: ProvenanceScope = ProvenanceScope.MY_BOOKS) -> None:
        self.repository = repository
        self.scope = scope
        self._provenance = ProvenanceService(repository)

    @staticmethod
    def check_identifier(identifier: object) -> None:
        """Refuse text that is not a procedure identifier, before any database is read."""
        if not isinstance(identifier, str) or not is_valid_id(identifier):
            raise refuse(f"{identifier!r} is not a RUDRA identifier.", "Name a procedure, e.g. PRC-00000001.")
        if parse_id(identifier)[0] is not EntityKind.PROCEDURE:
            raise refuse(f"{identifier} is not a procedure.",
                         "Procedure identifiers begin with PRC-; `python -m app procedure` lists them.")

    def require_schema(self) -> None:
        """Refuse a database whose schema is not this build's; never migrate."""
        version = schema_version(self.repository.connection)
        if version != CODE_SCHEMA_VERSION:
            raise StorageError.of(
                "The knowledge database's schema version is not the one this build reads; "
                "procedural memory never migrates.",
                f"Its schema version is {version}; this build reads version {CODE_SCHEMA_VERSION}.",
                stage="procedures.schema", data_changed=False, retry_safe=True,
                next_options=(
                    "Check that the project root names the intended project.",
                    "Migrate explicitly with: python -m app db  (this writes to the database; "
                    "for the live database make a fresh D-15 backup first).",
                ),
            )

    # ------------------------------------------------------------ retrieval

    def all(self) -> tuple[StoredProcedure, ...]:
        """Every procedure not DELETED or ARCHIVED, by numeric counter."""
        self.require_schema()
        rows = queries.all_procedures(self.repository.connection)
        return tuple(self._stored(row) for row in rows if not lifecycle_excluded(row.lifecycle_status))

    def find(self, identifier: str) -> Lookup:
        self.check_identifier(identifier)
        self.require_schema()
        row = self.repository.get(Procedure, identifier)
        if row is None:
            return Lookup(LookupStatus.NOT_FOUND, identifier, f"No stored procedure has the identifier {identifier}.")
        if lifecycle_excluded(row.lifecycle_status):
            return Lookup(LookupStatus.EXCLUDED, identifier,
                          f"{identifier} is {row.lifecycle_status.value}: it is not shown or run (P9-23).")
        return Lookup(LookupStatus.FOUND, identifier, "", self._stored(row))

    def _stored(self, row: Procedure) -> StoredProcedure:
        source = self._provenance.of_item(row.id, self.scope)
        knowledge = None if row.knowledge_id is None else self.repository.get(KnowledgeObject, row.knowledge_id)
        quotes = tuple(c.evidence.evidence_text for c in source.citations)
        if knowledge is None:
            steps, reason = (), "it is stored without a knowledge object, so it has no documented steps"
        else:
            steps, reason = recovered_steps(knowledge.statement, quotes)
        if source.status is not ProvenanceStatus.AVAILABLE:
            reason = f"its source is not available in scope {self.scope.value}: {source.message}"
        elif row.documentation_status is ProcedureDocumentationStatus.INFERRED_PROCEDURE:
            reason = "it is an inferred procedure; only documented procedures are executed (P16-1)"
        labels = (row.documentation_status.value,)
        if row.successful_executions >= 1 and row.documentation_status is not ProcedureDocumentationStatus.VERIFIED_PROCEDURE:
            labels += (ProcedureDocumentationStatus.VERIFIED_PROCEDURE.value,)
        history = tuple(
            HistoryEntry(v.id, v.created_at, v.status.value, v.expected, v.observed)
            for v in queries.verifications_of(self.repository.connection, row.id)
        )
        return StoredProcedure(
            id=row.id, name=row.name, documentation_status=row.documentation_status.value,
            lifecycle_status=row.lifecycle_status.value, labels=labels, knowledge_id=row.knowledge_id,
            steps=steps, parameters=parameters(steps), execution_count=row.execution_count,
            successful_executions=row.successful_executions, failed_executions=row.failed_executions,
            last_verified=row.last_verified, known_application_version=row.known_application_version,
            limitations=row.limitations, history=history, source=source, executable=not reason,
            reason=reason, note=_note(row, history),
        )

    # ------------------------------------------------------------ recording

    def record(self, identifier: str, status: VerificationStatus, *, expected: str, observed: str) -> Verification:
        """Record one live run (P16-8), in one transaction: `execution_count` + 1; a
        VERIFIED run adds a success and sets `last_verified`; a FAILED run adds a failure;
        an INCONCLUSIVE run adds neither - never a success."""
        if status is VerificationStatus.PENDING:
            raise refuse("A run is recorded only with its outcome.", "PENDING is not an outcome.")
        connection = self.repository.connection
        try:
            row = self.repository.get(Procedure, identifier)
            if row is None:
                raise refuse(f"No stored procedure has the identifier {identifier}.", "Nothing was recorded.")
            now = utc_now()
            self.repository.update(replace(
                row,
                execution_count=row.execution_count + 1,
                successful_executions=row.successful_executions + (status is VerificationStatus.VERIFIED),
                failed_executions=row.failed_executions + (status is VerificationStatus.FAILED),
                last_verified=now if status is VerificationStatus.VERIFIED else row.last_verified,
            ))
            verification = self.repository.add(Verification(
                id=self.repository.new_id(Verification), created_at=now, updated_at=now,
                subject_id=identifier, status=status, expected=expected, observed=observed,
            ))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return verification


def _note(row: Procedure, history: tuple[HistoryEntry, ...]) -> str:
    if row.execution_count == 0:
        return "never executed live"
    last = history[-1] if history else None
    tail = "" if last is None else f"; the last run was {last.status} at {last.recorded}"
    if row.successful_executions == 0:
        return f"executed live {row.execution_count} time(s), never fully verified{tail}"
    return (f"verified on {row.successful_executions} of {row.execution_count} live run(s){tail}; "
            f"{ENVIRONMENT_NOTE}")
