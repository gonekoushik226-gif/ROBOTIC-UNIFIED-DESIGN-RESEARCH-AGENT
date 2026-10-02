"""Verifying a recorded reasoning answer (ADR 0044 P12-11).

Two kinds of check, each reported with what it found:

1. **REPRODUCTION** — the recorded request is run again through the Phase 10 reasoning
   engine on current data; status, every method's target and state, every node's state,
   every derivation step and, in forward mode, the derived set must equal the recorded
   ones.
2. **STEP** — every recorded derivation step is re-checked against stored data: each
   relationship it cites must exist, be ACTIVE, be `REQUIRES` or `DEPENDS_ON` from the
   step's concept to one of its recorded requirements, and have evidence in the requested
   scope; and together they must be the concept's whole stored requirement set (ADR 0038
   P10-4), since a node is never derived from part of its set.

Reasoning always reads `knowledge.db`; without it both checks are `INCONCLUSIVE`. The
outcome is combined as in calculation. **Source provenance:** every relationship the
answer cites and every item it admitted, re-read with the quote and file checks; when
none can be shown, *"Provenance unavailable."* with the reason.
"""

import json

from app.core.errors import InvalidInputError, StorageError
from app.models.entities import Relationship
from app.models.enums import LifecycleStatus, RelationType
from app.provenance import (
    UNAVAILABLE_TEXT,
    Check,
    CheckStatus,
    ProvenanceScope,
    ProvenanceService,
    ProvenanceStatus,
    combined,
)
from app.provenance.scope import Verdict, counter, source_verdict
from app.reasoning import ReasoningEngine
from app.reasoning import to_json as reasoning_json
from app.storage import queries
from app.storage.repository import Repository
from app.verification.answers import RecordedAnswer
from app.verification.results import AnswerVerification, Exposure

_DEPENDENCIES = (RelationType.REQUIRES, RelationType.DEPENDS_ON)


def _steps(data: dict) -> list[dict]:
    steps = [s for m in data.get("methods", ()) for s in m.get("steps", ())]
    forward = data.get("forward")
    if isinstance(forward, dict):
        steps.extend(forward.get("steps", ()))
    return steps


def _fingerprint(data: dict) -> dict:
    def part(nodes, steps):
        return (sorted((n["concept"]["id"], n["state"]) for n in nodes),
                [(s["concept_id"], tuple(s["requirements"]), tuple(s["relationships"])) for s in steps])

    forward = data.get("forward")
    return {
        "status": data.get("status"),
        "methods": [(m["target"]["id"], m["state"], part(m["nodes"], m["steps"])) for m in data.get("methods", ())],
        "forward": None if not forward else (tuple(forward["derived"]), part(forward["nodes"], forward["steps"])),
    }


def _reproduce(answer: RecordedAnswer, repository: Repository | None) -> Check:
    if repository is None:
        return Check("REPRODUCTION", "answer", CheckStatus.INCONCLUSIVE,
                     "knowledge.db cannot be read, so the reasoning cannot be run again")
    try:
        again = ReasoningEngine(repository).reason(answer.request)
    except InvalidInputError as exc:
        return Check("REPRODUCTION", "answer", CheckStatus.FAILED,
                     f"the recorded request is no longer valid on current data: {exc.report.summary}")
    except StorageError as exc:
        return Check("REPRODUCTION", "answer", CheckStatus.INCONCLUSIVE,
                     f"knowledge.db cannot be read as this build reads it: {exc.report.summary}")
    fresh = json.loads(reasoning_json(again))
    try:
        recorded = _fingerprint(answer.recorded)
    except (KeyError, TypeError, AttributeError) as exc:
        return Check("REPRODUCTION", "answer", CheckStatus.FAILED,
                     f"the recorded answer is incomplete or malformed ({type(exc).__name__}: {exc})")
    produced = _fingerprint(fresh)
    differing = [key for key in produced if produced[key] != recorded.get(key)]
    if differing:
        return Check("REPRODUCTION", "answer", CheckStatus.FAILED,
                     "running the recorded request again gives a different answer in: " + ", ".join(differing))
    return Check("REPRODUCTION", "answer", CheckStatus.VERIFIED,
                 "running the recorded request again on current data gives the recorded answer")


def _in_scope(repository: Repository, subject_id: str, scope: ProvenanceScope) -> bool:
    from app.models.entities import Source

    for row in queries.evidence_for(repository.connection, subject_id):
        if source_verdict(repository.get(Source, row["source_id"]), scope) is Verdict.IN_SCOPE:
            return True
    return False


def _step_checks(answer: RecordedAnswer, repository: Repository | None) -> list[Check]:
    checks = []
    scope = ProvenanceScope(answer.request.scope.value)
    for number, step in enumerate(_steps(answer.recorded), start=1):
        concept = step["concept_id"]
        subject = f"step {step.get('number', number)} ({concept})"
        if repository is None:
            checks.append(Check("STEP", subject, CheckStatus.INCONCLUSIVE, "knowledge.db cannot be read"))
            continue
        problems = []
        required = set(step["requirements"])
        for relationship_id in step["relationships"]:
            relationship = repository.get(Relationship, relationship_id)
            if relationship is None:
                problems.append(f"{relationship_id} is not stored")
            elif relationship.lifecycle_status is not LifecycleStatus.ACTIVE:
                problems.append(f"{relationship_id} is stored {relationship.lifecycle_status.value}")
            elif relationship.relation_type not in _DEPENDENCIES:
                problems.append(f"{relationship_id} is {relationship.relation_type.value}, not a dependency")
            elif relationship.from_concept_id != concept or relationship.to_concept_id not in required:
                problems.append(f"{relationship_id} does not join {concept} to a recorded requirement")
            elif not _in_scope(repository, relationship_id, scope):
                problems.append(f"{relationship_id} has no evidence in the scope {scope.value}")
        stored = {
            r.id for r in queries.relationships_for_concept(repository.connection, concept)
            if r.from_concept_id == concept and r.relation_type in _DEPENDENCIES
        }
        if stored != set(step["relationships"]):
            problems.append("the concept's stored requirement set is now "
                            + (", ".join(sorted(stored, key=counter)) or "empty")
                            + ", not the relationships the step cites")
        if problems:
            checks.append(Check("STEP", subject, CheckStatus.FAILED, "; ".join(problems)))
        else:
            checks.append(Check("STEP", subject, CheckStatus.VERIFIED,
                                f"its {len(step['relationships'])} relationship(s) are stored, ACTIVE, in "
                                "scope, and are the concept's whole requirement set"))
    return checks


def _cited(data: dict) -> tuple[str, ...]:
    """Every relationship the recorded answer cites, by numeric counter."""
    found: set[str] = set()
    parts = [m for m in data.get("methods", ())]
    if isinstance(data.get("forward"), dict):
        parts.append(data["forward"])
    for part in parts:
        for node in part.get("nodes", ()):
            for link in node.get("requirements", ()):
                relationship = link.get("relationship") or {}
                if isinstance(relationship.get("id"), str):
                    found.add(relationship["id"])
        for step in part.get("steps", ()):
            found.update(r for r in step.get("relationships", ()) if isinstance(r, str))
    return tuple(sorted(found, key=counter))


def verify_reasoning(answer: RecordedAnswer, repository: Repository | None) -> AnswerVerification:
    """Verify one recorded reasoning answer (module docstring). Reads only."""
    checks = [_reproduce(answer, repository), *_step_checks(answer, repository)]
    status = combined(tuple(checks))
    data = answer.recorded
    admitted = tuple(a.knowledge_id for a in answer.request.admissions)
    relationships = _cited(data)
    sources = ()
    if repository is None:
        source_status = ProvenanceStatus.UNAVAILABLE
        message = f"{UNAVAILABLE_TEXT} knowledge.db cannot be read, so no evidence can be shown."
    else:
        service = ProvenanceService(repository)
        scope = ProvenanceScope(answer.request.scope.value)
        try:
            sources = tuple(service.of_item(i, scope) for i in (*relationships, *admitted))
        except StorageError:
            sources = ()
        shown = [s for s in sources if s.status is ProvenanceStatus.AVAILABLE]
        if shown:
            source_status = ProvenanceStatus.AVAILABLE
            message = f"Provenance available for {len(shown)} of {len(sources)} stored item(s) the answer used."
        else:
            source_status = ProvenanceStatus.UNAVAILABLE
            message = (f"{UNAVAILABLE_TEXT} The answer rests on no stored relationship or admitted item "
                       "with evidence in scope; no citation is made.")
    steps = _steps(data)
    exposure = Exposure(
        source_status=source_status,
        source_message=message,
        sources=sources,
        pages=tuple(dict.fromkeys(page for s in sources for page in s.pages)),
        relevant_knowledge=(*relationships, *admitted),
        derivation=tuple(
            f"Step {s.get('number', n)}: {s['concept_id']} from {', '.join(s['requirements'])} by "
            f"{s.get('rule', 'REQUIREMENT_SET')} v{s.get('rule_version', '1')} [{', '.join(s['relationships'])}]"
            for n, s in enumerate(steps, start=1)
        ),
        calculation=("No calculation: dependency reasoning performs none.",),
        assumptions=tuple(
            f"{a.node} ({a.statement or 'assumed available'}) (ASSUMPTION)" for a in answer.request.assumptions
        ),
        verification_status=status,
    )
    if status is CheckStatus.VERIFIED:
        text = (f"Verification VERIFIED: the answer was reproduced on current data, and its {len(steps)} "
                "derivation step(s) rest on stored, ACTIVE relationships in scope.")
    else:
        first = next(c for c in checks if c.status is status)
        text = f"Verification {status.value}: {first.kind} ({first.subject_id}) - {first.detail}."
    return AnswerVerification(
        kind=answer.kind.value,
        status=status,
        message=text,
        answer_status=str(data.get("status")),
        scope=answer.request.scope.value,
        checks=tuple(checks),
        exposure=exposure,
        database_opened=repository is not None,
        notes=("Nothing was written: the verification is returned only.",
               "The answer file was read as untrusted input: its request was run again and its "
               "results compared, never believed."),
    )
