"""One reasoning request's reads, scope verdicts and withheld counts. Read-only.

Every stored item the reasoning uses - a concept, a dependency relationship, a
knowledge object, a conflict claim - is used only with evidence in the requested scope
(ADR 0035 P9-5) and only when its lifecycle allows (P9-23): `DELETED` and `ARCHIVED`
are never used. An item that fails is withheld and counted, never shown. The same row
always gets the same verdict, and each stored row is read once per request.

Nothing here writes.
"""

from app.models.entities import Concept, KnowledgeObject, Relationship, Source
from app.models.enums import LifecycleStatus
from app.reasoning.provenance import evidence_of, provenance_of
from app.reasoning.requests import ReasoningScope
from app.reasoning.results import EvidenceRow, Provenance, Withheld
from app.reasoning.scope import Verdict, lifecycle_excluded, source_verdict
from app.storage.repository import Repository


class ReasoningContext:
    """Cached reads, verdicts and withheld counts for one request."""

    def __init__(self, repository: Repository, scope: ReasoningScope) -> None:
        self.repository = repository
        self.connection = repository.connection
        self.scope = scope
        self._rows: dict[type, dict[str, object]] = {}
        self._evidence: dict[str, tuple[EvidenceRow, ...]] = {}
        self._verdicts: dict[str, Verdict] = {}
        self._kept: dict[str, tuple[EvidenceRow, ...]] = {}
        self._provenance: dict[tuple[str, ...], Provenance] = {}
        self._withheld: dict[str, set[str]] = {
            "concepts": set(), "relationships": set(), "knowledge": set(), "excluded": set(),
        }

    # ------------------------------------------------------------ stored rows

    def get(self, entity_type: type, identifier: str | None):
        """One stored row by identifier, read once per request; None when absent."""
        if identifier is None:
            return None
        cache = self._rows.setdefault(entity_type, {})
        if identifier not in cache:
            cache[identifier] = self.repository.get(entity_type, identifier)
        return cache[identifier]

    def evidence(self, subject_id: str) -> tuple[EvidenceRow, ...]:
        if subject_id not in self._evidence:
            self._evidence[subject_id] = evidence_of(self.connection, subject_id)
        return self._evidence[subject_id]

    # --------------------------------------------------------------- verdicts

    def verdict(self, row: EvidenceRow) -> Verdict:
        """Authorisation, then scope (P9-5). The same row, the same verdict."""
        if row.id not in self._verdicts:
            self._verdicts[row.id] = source_verdict(self.get(Source, row.source_id), self.scope)
        return self._verdicts[row.id]

    def kept(self, subject_id: str) -> tuple[EvidenceRow, ...]:
        """A subject's evidence in the requested scope, in evidence order."""
        if subject_id not in self._kept:
            self._kept[subject_id] = tuple(
                row for row in self.evidence(subject_id) if self.verdict(row) is Verdict.IN_SCOPE
            )
        return self._kept[subject_id]

    def provenance(self, rows: tuple[EvidenceRow, ...]) -> Provenance:
        key = tuple(row.id for row in rows)
        if key not in self._provenance:
            self._provenance[key] = provenance_of(self.connection, rows)
        return self._provenance[key]

    def _in_scope(self, subject_id: str, kind: str) -> bool:
        if self.kept(subject_id):
            return True
        self._withheld[kind].add(subject_id)
        return False

    def usable_concept(self, concept: Concept | None) -> bool:
        """A concept is used only when not DELETED or ARCHIVED and with evidence in scope."""
        if concept is None:
            return False
        if lifecycle_excluded(concept.lifecycle_status):
            self._withheld["excluded"].add(concept.id)
            return False
        return self._in_scope(concept.id, "concepts")

    def usable_relationship(self, relationship: Relationship) -> bool:
        """A relationship is used only when ACTIVE and with its own evidence in scope."""
        if relationship.lifecycle_status is not LifecycleStatus.ACTIVE:
            return False
        return self._in_scope(relationship.id, "relationships")

    def usable_knowledge(self, knowledge: KnowledgeObject | None) -> bool:
        """A knowledge object is used only when not DELETED or ARCHIVED and in scope."""
        if knowledge is None:
            return False
        if lifecycle_excluded(knowledge.lifecycle_status):
            self._withheld["excluded"].add(knowledge.id)
            return False
        return self._in_scope(knowledge.id, "knowledge")

    def withheld(self) -> Withheld:
        verdicts = list(self._verdicts.values())
        return Withheld(
            unauthorized_evidence=verdicts.count(Verdict.NOT_AUTHORIZED),
            out_of_scope_evidence=verdicts.count(Verdict.OUT_OF_SCOPE),
            concepts=len(self._withheld["concepts"]),
            relationships=len(self._withheld["relationships"]),
            knowledge=len(self._withheld["knowledge"]),
            excluded_by_lifecycle=len(self._withheld["excluded"]),
        )
