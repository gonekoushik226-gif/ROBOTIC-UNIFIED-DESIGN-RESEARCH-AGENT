"""The request's initial availability (ADR 0038 P10-5, OI-1 = Option B; ADR 0040 P10-27).

A node is AVAILABLE initially only when the structured request explicitly supplies it
as `USER_INPUT`, or explicitly admits one specific stored knowledge object for it; an
assumption (U7(b)) is stated separately and labelled. **Stored authorised knowledge is
never available merely because it exists in `knowledge.db`:** nothing here reads a
stored item the request does not name. Derived nodes come only from the reasoning
engine (ADR 0039 P10-12), never from here.

**Nodes** resolve as targets do: a concept identifier, or an exact concept name under
D-30 (ADR 0036 P9-11). A name resolves to every concept answering to it, and each is
listed - none is chosen. `DELETED` and `ARCHIVED` concepts are not used (P9-23). A
node that resolves to nothing makes the request invalid.

**Admissions** are checked in ADR 0040 P10-27's order:

1. the identifier is well formed, a knowledge object's, and exists, and the node
   resolves - otherwise the request is invalid (`InvalidInputError`, exit code 2);
2. the item has evidence in the requested scope (P9-5) - otherwise it is withheld;
3. the item is not `DELETED` or `ARCHIVED` (P9-23) - otherwise it is excluded.

A refused admission is reported with its reason and never used. An admitted item keeps
its evidence in scope and its provenance (P9-18), and its origin stays distinct from
`USER_INPUT`. No stored link between the item and the node is required, checked or
inferred, and an admission adds or removes no requirement.

Everything here reads; nothing writes.
"""

from app.knowledge import ConceptService
from app.models.entities import Concept, KnowledgeObject, Source
from app.models.enums import LifecycleStatus
from app.models.identifiers import EntityKind, is_valid_id
from app.reasoning.provenance import evidence_of, provenance_of, superseded_pointer
from app.reasoning.requests import Admission, ReasoningRequest, refuse
from app.reasoning.results import (
    AdmittedItem,
    Availability,
    InitialAvailability,
    Origin,
    RefusalReason,
    RefusedAdmission,
    StatedAssumption,
    SuppliedInput,
)
from app.reasoning.scope import Verdict, counter, lifecycle_excluded, source_verdict
from app.storage.repository import Repository

#: The order origins are listed in, for one node.
_ORIGIN_ORDER = {origin: position for position, origin in enumerate(Origin)}


def resolve_node(repository: Repository, reference: str, what: str) -> tuple[Concept, ...]:
    """Every concept a node reference names, by numeric counter. Refused when none does."""
    if is_valid_id(reference, kind=EntityKind.CONCEPT):
        concept = repository.get(Concept, reference)
        found: tuple[Concept, ...] = () if concept is None else (concept,)
    else:
        found = ConceptService(repository).resolve(reference)
    usable = sorted(
        (concept for concept in found if not lifecycle_excluded(concept.lifecycle_status)),
        key=lambda concept: counter(concept.id),
    )
    if not usable:
        raise refuse(
            f"The {what} node {reference!r} does not resolve to a concept.",
            "No stored concept has that identifier or an ACTIVE alias with that exact name "
            "(D-30); DELETED and ARCHIVED concepts are not used (P9-23).",
        )
    return tuple(usable)


def _admission_refusal(
    repository: Repository, knowledge: KnowledgeObject, request: ReasoningRequest, rows: tuple
) -> RefusalReason | None:
    """Why an existing item is not admitted, in P10-27's order; None when it is."""
    if not rows:
        return RefusalReason.NO_EVIDENCE
    verdicts = [source_verdict(repository.get(Source, row.source_id), request.scope) for row in rows]
    if Verdict.IN_SCOPE not in verdicts:
        if Verdict.OUT_OF_SCOPE in verdicts:
            return RefusalReason.OUT_OF_SCOPE
        return RefusalReason.NOT_AUTHORIZED
    if lifecycle_excluded(knowledge.lifecycle_status):
        return RefusalReason.EXCLUDED_BY_LIFECYCLE
    return None


def _admit(
    repository: Repository, admission: Admission, request: ReasoningRequest
) -> tuple[list[AdmittedItem], list[RefusedAdmission]]:
    knowledge = repository.get(KnowledgeObject, admission.knowledge_id)
    if knowledge is None:
        raise refuse(
            f"The admitted item {admission.knowledge_id!r} does not exist.",
            "No knowledge object with that identifier is stored.",
        )
    concepts = resolve_node(repository, admission.node, "admission")
    connection = repository.connection
    rows = evidence_of(connection, knowledge.id)
    reason = _admission_refusal(repository, knowledge, request, rows)
    if reason is not None:
        return [], [RefusedAdmission(admission.node, concept, knowledge, reason) for concept in concepts]
    kept = tuple(
        row for row in rows
        if source_verdict(repository.get(Source, row.source_id), request.scope) is Verdict.IN_SCOPE
    )
    provenance = provenance_of(connection, kept)
    pointer = (
        superseded_pointer(connection, knowledge.id)
        if knowledge.lifecycle_status is LifecycleStatus.SUPERSEDED
        else None
    )
    admitted = [
        AdmittedItem(admission.node, concept, knowledge, kept, provenance, pointer)
        for concept in concepts
    ]
    return admitted, []


def initial_availability(repository: Repository, request: ReasoningRequest) -> InitialAvailability:
    """What the request itself makes available, before any reasoning (OI-1 = Option B).

    Checks the request first; raises `InvalidInputError` for an invalid request. The
    target, if any, is not resolved here: that is the reasoning engine's.
    """
    request.check()
    inputs = [
        SuppliedInput(given.node, concept, given.value)
        for given in request.inputs
        for concept in resolve_node(repository, given.node, "input")
    ]
    admitted: list[AdmittedItem] = []
    refused: list[RefusedAdmission] = []
    for admission in request.admissions:
        took, turned_away = _admit(repository, admission, request)
        admitted.extend(took)
        refused.extend(turned_away)
    assumptions = [
        StatedAssumption(stated.node, concept, stated.statement)
        for stated in request.assumptions
        for concept in resolve_node(repository, stated.node, "assumption")
    ]
    available = {
        *(Availability(item.concept.id, Origin.USER_INPUT) for item in inputs),
        *(Availability(item.concept.id, Origin.ADMITTED_STORED_ITEM, item.knowledge.id) for item in admitted),
        *(Availability(item.concept.id, Origin.ASSUMPTION) for item in assumptions),
    }
    return InitialAvailability(
        inputs=tuple(sorted(inputs, key=lambda i: (
            counter(i.concept.id), i.reference, i.value is None, i.value or ""
        ))),
        admitted=tuple(sorted(admitted, key=lambda a: (
            counter(a.concept.id), counter(a.knowledge.id), a.reference
        ))),
        refused=tuple(sorted(refused, key=lambda r: (
            counter(r.concept.id), counter(r.knowledge.id), r.reference
        ))),
        assumptions=tuple(sorted(assumptions, key=lambda s: (
            counter(s.concept.id), s.reference, s.statement is None, s.statement or ""
        ))),
        available=tuple(sorted(available, key=lambda entry: (
            counter(entry.concept_id),
            _ORIGIN_ORDER[entry.origin],
            0 if entry.knowledge_id is None else counter(entry.knowledge_id),
        ))),
    )
