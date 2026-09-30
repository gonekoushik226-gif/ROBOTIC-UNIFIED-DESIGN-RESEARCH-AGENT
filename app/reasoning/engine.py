"""The Phase 10 reasoning engine (ADRs 0038-0040; Part 5 sections 200-201).

**What it reasons over.** The stored dependency graph (`app.reasoning.graph`): ACTIVE
`REQUIRES` and `DEPENDS_ON` relationships between two concepts, each concept's stored
requirement set treated as complete (ADR 0038 P10-3, P10-4). Every relationship and
concept is used only with evidence in the requested scope and only when its lifecycle
allows (P9-5, P9-23); a requirement that cannot be used is counted, never shown, and
blocks its node - a node is never derived from part of its set (ADR 0039 P10-20).

**What is available.** Only what the request makes available (OI-1 = Option B;
`app.reasoning.inputs`): a `USER_INPUT`, an explicitly admitted stored knowledge object,
or an assumption (U7(b)). Stored knowledge is never available merely because it exists,
and no knowledge object, text or equation becomes a node (D1).

**The rule** (ADR 0039 P10-12) - the requirement-set rule, formed here at reasoning
time from source-stated relationships: *a node whose non-empty stored requirement set
is wholly AVAILABLE or DERIVED is DERIVED.* An empty set derives nothing: a node with no
stored requirement is AVAILABLE only through the request, and otherwise MISSING.

**The states** (section 84; ADR 0039 P10-11), decided requirements first:

    CONFLICTING  stored conflict information is attached (ADR 0039 P10-19); never
                 hidden, even for a node the request supplies
    AVAILABLE    the request made it available
    BLOCKED      on a stored dependency cycle; or a requirement is MISSING, BLOCKED,
                 CONFLICTING, withheld or excluded
    MISSING      not made available, and no stored requirement to derive it from
    DERIVED      the rule applied

**Directions** (ADR 0039 P10-14). TARGET: backward from every concept answering to the
target - each is its own method, none selected or ranked (P10-17). FORWARD: from every
node the request made available, through the concepts that depend on them. Both use one
evaluation, so they agree.

**What it returns** (P10-21): each node's state and why; the derivation steps in
dependency order; every chain of stored requirements from an AVAILABLE node to the
target, inputs first (P10-16); the missing dependencies with what needs them (sections
89, 228); cycles; conflicts with both claims and their provenance; what was withheld;
and what each derived result is conditional on. A derived result is a reasoning result,
not a source fact. No hidden chain-of-thought exists to expose. Nothing is written.
"""

from dataclasses import dataclass

from app.core.errors import StorageError
from app.knowledge import ConceptService
from app.models.entities import Concept
from app.models.identifiers import EntityKind, is_valid_id
from app.reasoning.conflicts import conflict_information
from app.reasoning.context import ReasoningContext
from app.reasoning.graph import DependencyGraph, backward_graph, forward_graph, strongly_connected
from app.reasoning.inputs import initial_availability
from app.reasoning.requests import ReasoningMode, ReasoningRequest
from app.reasoning.results import (
    AnswerStatus,
    Block,
    BlockReason,
    DerivationStep,
    ForwardResult,
    InitialAvailability,
    Method,
    MissingDependency,
    NodeResult,
    NodeState,
    Origin,
    PathNode,
    ReasoningPath,
    ReasoningResult,
    RequiredBy,
    RequirementLink,
)
from app.reasoning.scope import counter, lifecycle_excluded
from app.storage import CODE_SCHEMA_VERSION, queries, schema_version
from app.storage.repository import Repository

#: The requirement-set rule's name and version, carried by every derivation step.
RULE = "REQUIREMENT_SET"
RULE_VERSION = "1"

#: Path listing stops here and says so; the states, steps and missing dependencies
#: are always complete (ADR 0040 P10-29: never a silent truncation).
MAX_PATHS = 1000
#: Partial paths explored before the listing stops, for graphs with very many chains.
MAX_PATH_STATES = 100_000

_ORIGIN_ORDER = {origin: position for position, origin in enumerate(Origin)}
_ESTABLISHED = (NodeState.AVAILABLE, NodeState.DERIVED)

STORED_SET_NOTE = (
    "Requirement sets are the stored REQUIRES and DEPENDS_ON relationships between "
    "concepts, each treated as complete (ADR 0038 P10-4): a requirement no source "
    "stated, or one extraction did not store, is not known to this reasoning."
)
NOT_A_FACT_NOTE = (
    "A DERIVED node is a reasoning result under the requirement-set rule, not a source "
    "fact; it and its trace are returned only, and nothing was written (U5(a))."
)


# ---------------------------------------------------------------- the scoped graph


class _ScopedGraph:
    """The stored graph with P9-5 and P9-23 applied to each relationship and concept."""

    def __init__(self, context: ReasoningContext, graph: DependencyGraph) -> None:
        self.context = context
        self.graph = graph
        self._links: dict[str, tuple[tuple[RequirementLink, ...], int, int]] = {}

    def links(self, node: str) -> tuple[tuple[RequirementLink, ...], int, int]:
        """The node's usable requirements, and how many were withheld and excluded."""
        if node not in self._links:
            usable: list[RequirementLink] = []
            withheld = excluded = 0
            for relationship in self.graph.of(node):
                required = self.context.get(Concept, relationship.to_concept_id)
                if not self.context.usable_relationship(relationship):
                    withheld += 1
                elif required is None or lifecycle_excluded(required.lifecycle_status):
                    self.context.usable_concept(required)  # counted as excluded
                    excluded += 1
                elif not self.context.usable_concept(required):
                    withheld += 1
                else:
                    evidence = self.context.kept(relationship.id)
                    usable.append(
                        RequirementLink(
                            relationship=relationship,
                            required_id=required.id,
                            evidence=evidence,
                            provenance=self.context.provenance(evidence),
                        )
                    )
            self._links[node] = (tuple(usable), withheld, excluded)
        return self._links[node]

    def successors(self, node: str) -> tuple[str, ...]:
        return tuple(link.required_id for link in self.links(node)[0])

    def below(self, roots) -> set[str]:
        """The roots and every node their usable requirements reach."""
        reached: set[str] = set()
        frontier = list(roots)
        while frontier:
            node = frontier.pop()
            if node not in reached:
                reached.add(node)
                frontier.extend(self.successors(node))
        return reached


# ------------------------------------------------------------------ evaluation


@dataclass(frozen=True, slots=True)
class _Evaluation:
    scoped: _ScopedGraph
    nodes: dict[str, NodeResult]
    #: Derived nodes in step order: a node after all its derived requirements.
    order: tuple[str, ...]
    cycle_of: dict[str, tuple[str, ...]]


def _evaluate(
    context: ReasoningContext, scoped: _ScopedGraph, reached: set[str], initial: InitialAvailability
) -> _Evaluation:
    origins: dict[str, set[Origin]] = {}
    admitted: dict[str, set[str]] = {}
    for entry in initial.available:
        origins.setdefault(entry.concept_id, set()).add(entry.origin)
        if entry.knowledge_id is not None:
            admitted.setdefault(entry.concept_id, set()).add(entry.knowledge_id)

    successors = {node: scoped.successors(node) for node in reached}
    components = strongly_connected(reached, successors)
    cycle_of: dict[str, tuple[str, ...]] = {}
    for component in components:
        if len(component) > 1 or component[0] in successors[component[0]]:
            for node in component:
                cycle_of[node] = component

    states: dict[str, NodeState] = {}
    blocks: dict[str, tuple[Block, ...]] = {}
    conditional: dict[str, tuple[str, ...]] = {}
    attached: dict[str, tuple] = {}
    for component in components:  # every requirement is decided before what needs it
        for node in component:
            links, withheld, excluded = scoped.links(node)
            conflicts = conflict_information(
                context, node, tuple(sorted(admitted.get(node, ()), key=counter))
            )
            attached[node] = conflicts
            reasons: list[Block] = []
            if conflicts[0] or conflicts[1]:
                state = NodeState.CONFLICTING
            elif node in origins:
                state = NodeState.AVAILABLE
            elif node in cycle_of:
                state = NodeState.BLOCKED
                reasons.append(Block(BlockReason.ON_CYCLE))
            elif not scoped.graph.of(node):
                state = NodeState.MISSING
            else:
                for link in links:
                    reason = {
                        NodeState.MISSING: BlockReason.MISSING_REQUIREMENT,
                        NodeState.BLOCKED: BlockReason.BLOCKED_REQUIREMENT,
                        NodeState.CONFLICTING: BlockReason.CONFLICTING_REQUIREMENT,
                    }.get(states[link.required_id])
                    if reason is not None:
                        reasons.append(Block(reason, link.required_id, link.relationship.id))
                state = NodeState.BLOCKED if reasons or withheld or excluded else NodeState.DERIVED
            if state is NodeState.BLOCKED:
                if withheld:
                    reasons.append(Block(BlockReason.WITHHELD_REQUIREMENT))
                if excluded:
                    reasons.append(Block(BlockReason.EXCLUDED_REQUIREMENT))
            states[node] = state
            blocks[node] = tuple(reasons)
            if state is NodeState.AVAILABLE:
                given = origins[node] & {Origin.USER_INPUT, Origin.ADMITTED_STORED_ITEM}
                conditional[node] = () if given else (node,)
            elif state is NodeState.DERIVED:
                conditional[node] = tuple(sorted(
                    {c for link in links for c in conditional[link.required_id]}, key=counter
                ))
            else:
                conditional[node] = ()

    pending = sorted((n for n in reached if states[n] is NodeState.DERIVED), key=counter)
    done: set[str] = set()
    order: list[str] = []
    while pending:  # the derived nodes form no cycle: a cycle is BLOCKED
        ready = next(
            n for n in pending
            if all(m in done or states[m] is not NodeState.DERIVED for m in successors[n])
        )
        order.append(ready)
        done.add(ready)
        pending.remove(ready)

    nodes = {}
    for node in reached:
        links, withheld, excluded = scoped.links(node)
        state = states[node]
        nodes[node] = NodeResult(
            concept=context.get(Concept, node),
            state=state,
            origins=(
                tuple(sorted(origins[node], key=_ORIGIN_ORDER.__getitem__))
                if state is NodeState.AVAILABLE
                else (Origin.DERIVED,) if state is NodeState.DERIVED else ()
            ),
            admitted=tuple(sorted(admitted.get(node, ()), key=counter)),
            requirements=links,
            withheld_requirements=withheld,
            excluded_requirements=excluded,
            blocks=blocks[node],
            cycle=cycle_of.get(node, ()),
            conflicts=attached[node][0],
            contradictions=attached[node][1],
            conditional_on=conditional[node],
        )
    return _Evaluation(scoped=scoped, nodes=nodes, order=tuple(order), cycle_of=cycle_of)


# ------------------------------------------------------------- result sections


def _steps(evaluation: _Evaluation, subset: set[str]) -> tuple[DerivationStep, ...]:
    steps = []
    for node in (n for n in evaluation.order if n in subset):
        result = evaluation.nodes[node]
        steps.append(
            DerivationStep(
                number=len(steps) + 1,
                rule=RULE,
                rule_version=RULE_VERSION,
                concept_id=node,
                requirements=tuple(link.required_id for link in result.requirements),
                relationships=tuple(link.relationship.id for link in result.requirements),
                conditional_on=result.conditional_on,
            )
        )
    return tuple(steps)


def _missing(evaluation: _Evaluation, subset: set[str]) -> tuple[MissingDependency, ...]:
    found = []
    for node in sorted(subset, key=counter):
        if evaluation.nodes[node].state is not NodeState.MISSING:
            continue
        required_by = tuple(
            RequiredBy(parent, link.relationship.id)
            for parent in sorted(subset, key=counter)
            for link in evaluation.nodes[parent].requirements
            if link.required_id == node
        )
        found.append(MissingDependency(concept=evaluation.nodes[node].concept, required_by=required_by))
    return tuple(found)


def _cycles(evaluation: _Evaluation, subset: set[str]) -> tuple[tuple[str, ...], ...]:
    cycles = {evaluation.cycle_of[n] for n in subset if n in evaluation.cycle_of}
    return tuple(sorted(cycles, key=lambda cycle: tuple(counter(n) for n in cycle)))


def _paths(evaluation: _Evaluation, target: str) -> tuple[tuple[ReasoningPath, ...], bool]:
    """Every chain of stored requirements from an AVAILABLE node to the target, inputs first.

    Simple chains only (no node twice), so a cycle cannot loop. The listing stops at
    `MAX_PATHS` paths or `MAX_PATH_STATES` explored chains and reports that it did.
    """
    nodes = evaluation.nodes
    found: list[tuple[str, ...]] = []
    complete = True
    explored = 0
    stack: list[tuple[str, tuple[str, ...]]] = [(target, (target,))]
    while stack:
        node, chain = stack.pop()
        explored += 1
        if node != target and nodes[node].state is NodeState.AVAILABLE:
            found.append(tuple(reversed(chain)))
            if len(found) > MAX_PATHS:
                break
        if explored > MAX_PATH_STATES:
            complete = False
            break
        for link in nodes[node].requirements:
            if link.required_id not in chain:
                stack.append((link.required_id, chain + (link.required_id,)))
    if len(found) > MAX_PATHS:
        found = found[:MAX_PATHS]
        complete = False
    ordered = sorted(set(found), key=lambda chain: tuple(counter(n) for n in chain))
    paths = tuple(
        ReasoningPath(
            nodes=tuple(
                PathNode(concept_id=n, name=nodes[n].concept.canonical_name, state=nodes[n].state)
                for n in chain
            )
        )
        for chain in ordered
    )
    return paths, complete


def _method(evaluation: _Evaluation, target: str) -> Method:
    subset = evaluation.scoped.below([target])
    paths, complete = _paths(evaluation, target)
    return Method(
        target=evaluation.nodes[target].concept,
        state=evaluation.nodes[target].state,
        nodes=tuple(evaluation.nodes[n] for n in sorted(subset, key=counter)),
        steps=_steps(evaluation, subset),
        paths=paths,
        paths_complete=complete,
        missing=_missing(evaluation, subset),
        cycles=_cycles(evaluation, subset),
    )


def _label(concept: Concept) -> str:
    return f"{concept.canonical_name} ({concept.id})"


def _method_line(method: Method) -> str:
    target = next(n for n in method.nodes if n.concept.id == method.target.id)
    label = _label(method.target)
    if method.state is NodeState.DERIVED:
        line = f"{label}: DERIVED in {len(method.steps)} step(s)"
    elif method.state is NodeState.AVAILABLE:
        line = f"{label}: AVAILABLE as given ({', '.join(o.value for o in target.origins)})"
    elif method.state is NodeState.CONFLICTING:
        line = f"{label}: CONFLICTING stored information; resolution not automatically selected"
    else:
        line = f"{label}: {method.state.value}"
    if method.missing:
        needs = "; ".join(
            f"{_label(m.concept)} "
            + (f"required by {', '.join(by.concept_id for by in m.required_by)}" if m.required_by
               else "- the target itself")
            for m in method.missing
        )
        line += f"; missing: {needs}"
    if target.conditional_on:
        line += f"; conditional on the assumption(s) {', '.join(target.conditional_on)}"
    return line + "."


# ------------------------------------------------------------------ the engine


class ReasoningEngine:
    """Answers a `ReasoningRequest` from `knowledge.db`, read-only (ADR 0038 P10-9)."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def reason(self, request: ReasoningRequest) -> ReasoningResult:
        """Raises `InvalidInputError` for an invalid request, `StorageError` for a
        database this build cannot read; otherwise always answers."""
        request.check()  # before the database is read
        self._require_schema()
        initial = initial_availability(self.repository, request)
        context = ReasoningContext(self.repository, request.scope)
        if request.mode is ReasoningMode.TARGET:
            status, message, methods, forward = self._target(context, request, initial)
        else:
            status, message, methods, forward = self._forward(context, initial)
        withheld = context.withheld()
        notes = [STORED_SET_NOTE, NOT_A_FACT_NOTE]
        results = [n for m in methods for n in m.nodes] + ([] if forward is None else list(forward.nodes))
        conditional = sorted({c for n in results for c in n.conditional_on}, key=counter)
        if conditional:
            notes.append(
                "Results marked conditional rest on the request's assumption(s) "
                + ", ".join(conditional) + " (U7(b)); they are not established without them."
            )
        if any(not m.paths_complete for m in methods):
            notes.append(
                f"The path listing stopped at its limit ({MAX_PATHS} paths or "
                f"{MAX_PATH_STATES} explored chains); states, steps and missing "
                "dependencies are complete."
            )
        if withheld != type(withheld)():
            notes.append(
                "Stored items without evidence from a source that is authorised and in "
                f"the requested scope ({request.scope.value}), or stored DELETED or "
                "ARCHIVED, were not used; they are counted under withheld (P9-5, P9-23)."
            )
        return ReasoningResult(
            request=request,
            status=status,
            message=message,
            initial=initial,
            methods=methods,
            forward=forward,
            withheld=withheld,
            notes=tuple(notes),
            rule=RULE,
            rule_version=RULE_VERSION,
            read_only_connection=queries.connection_is_read_only(self.repository.connection),
        )

    def _require_schema(self) -> None:
        """Refuse a database whose schema is not this build's; never migrate (ADR 0040 P10-28)."""
        version = schema_version(self.repository.connection)
        if version != CODE_SCHEMA_VERSION:
            raise StorageError.of(
                "The knowledge database's schema version is not the one this build reads; "
                "reasoning never migrates.",
                f"Its schema version is {version}; this build reads version {CODE_SCHEMA_VERSION}.",
                stage="reasoning.schema",
                data_changed=False,
                retry_safe=True,
                next_options=(
                    "Check that the project root names the intended project.",
                    "Migrate explicitly with: python -m app db  (this writes to the database; "
                    "for the live database make a fresh D-15 backup first).",
                ),
            )

    # ----------------------------------------------------------------- target

    def _resolve_target(self, context: ReasoningContext, reference: str) -> tuple[Concept, ...]:
        if is_valid_id(reference, kind=EntityKind.CONCEPT):
            concept = context.get(Concept, reference)
            found: tuple[Concept, ...] = () if concept is None else (concept,)
        else:
            found = ConceptService(self.repository).resolve(reference)
        kept = []
        for concept in sorted(found, key=lambda c: counter(c.id)):
            if lifecycle_excluded(concept.lifecycle_status):
                context.usable_concept(concept)  # counted as excluded; never used
            else:
                kept.append(concept)
        return tuple(kept)

    def _target(self, context: ReasoningContext, request: ReasoningRequest, initial):
        found = self._resolve_target(context, str(request.target))
        if not found:
            return (
                AnswerStatus.NOT_FOUND,
                f"No concept answers to {request.target!r}: there is nothing to reason about.",
                (),
                None,
            )
        targets = tuple(c.id for c in found if context.usable_concept(c))
        if not targets:
            return (
                AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION,
                f"Insufficient authorized information: {len(found)} concept(s) answer to "
                f"{request.target!r}, but none has evidence from a source that is "
                f"authorised and in the requested scope ({request.scope.value}).",
                (),
                None,
            )
        scoped = _ScopedGraph(context, backward_graph(context.connection, targets))
        evaluation = _evaluate(context, scoped, scoped.below(targets), initial)
        methods = tuple(_method(evaluation, target) for target in targets)
        determined = [m for m in methods if m.state in _ESTABLISHED]
        lines = " ".join(_method_line(m) for m in methods)
        if determined:
            status = AnswerStatus.DETERMINED
            message = (
                f"{request.target} is determined by {len(determined)} of {len(methods)} "
                f"method(s); none is selected. {lines}"
            )
        else:
            status = AnswerStatus.CANNOT_DETERMINE
            message = f"Cannot determine {request.target} from the authorized information. {lines}"
        return status, message, methods, None

    # ---------------------------------------------------------------- forward

    def _forward(self, context: ReasoningContext, initial: InitialAvailability):
        seeds = []
        for concept_id in sorted({e.concept_id for e in initial.available}, key=counter):
            if context.usable_concept(context.get(Concept, concept_id)):
                seeds.append(concept_id)
        graph, above = forward_graph(context.connection, seeds)
        scoped = _ScopedGraph(context, graph)
        needed_by: dict[str, set[str]] = {}
        for node in above:
            if context.usable_concept(context.get(Concept, node)):
                for required in scoped.successors(node):
                    needed_by.setdefault(required, set()).add(node)
        roots: set[str] = set()
        frontier = list(seeds)
        while frontier:  # upward, through usable requirements only
            node = frontier.pop()
            if node not in roots:
                roots.add(node)
                frontier.extend(needed_by.get(node, ()))
        reached = scoped.below(roots)
        evaluation = _evaluate(context, scoped, reached, initial)
        derived = evaluation.order
        forward = ForwardResult(
            seeds=tuple(seeds),
            nodes=tuple(evaluation.nodes[n] for n in sorted(reached, key=counter)),
            steps=_steps(evaluation, reached),
            derived=derived,
            missing=_missing(evaluation, reached),
            cycles=_cycles(evaluation, reached),
        )
        if derived:
            names = ", ".join(_label(evaluation.nodes[n].concept) for n in derived)
            return (
                AnswerStatus.DETERMINED,
                f"{len(derived)} node(s) follow from what the request made available: {names}.",
                (),
                forward,
            )
        return (
            AnswerStatus.CANNOT_DETERMINE,
            "Nothing further follows from what the request made available.",
            (),
            forward,
        )


def reason(repository: Repository, request: ReasoningRequest) -> ReasoningResult:
    """Answer one structured request. Read-only; raises `InvalidInputError` when invalid."""
    return ReasoningEngine(repository).reason(request)
