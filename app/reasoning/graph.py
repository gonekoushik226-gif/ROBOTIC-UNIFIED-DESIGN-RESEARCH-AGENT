"""The stored dependency graph and its cycles (ADR 0038 P10-3, P10-4; ADR 0039 P10-14,
P10-20; ADR 0040 P10-29).

A dependency is an ACTIVE `REQUIRES` or `DEPENDS_ON` relationship between two concepts,
read in its stored direction: `X REQUIRES A` means X requires A. The graph is read
through the Phase 10 storage closures, which return each reached concept's complete
stored requirement set; no requirement is added and none is dropped here. Scope and
lifecycle are applied by the engine, not here.

**Backward** reasoning reads the closure below the targets. **Forward** reasoning reads
the concepts that depend on the seeds (`dependent_closure`) and then everything below
them, so that every node's state is decided by the same evaluation in both directions.

Cycles are found by an iterative Tarjan's algorithm - no recursion limit and no depth
cut-off - over nodes and successors in numeric identifier order, so the same graph
always gives the same components in the same order.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.models.entities import Relationship
from app.reasoning.scope import counter
from app.storage import queries


@dataclass(frozen=True, slots=True)
class DependencyGraph:
    """Each concept's stored dependency relationships, by numeric counter."""

    requirements: Mapping[str, tuple[Relationship, ...]]

    def of(self, concept_id: str) -> tuple[Relationship, ...]:
        return self.requirements.get(concept_id, ())

    def concept_ids(self) -> tuple[str, ...]:
        found = set(self.requirements)
        for relationships in self.requirements.values():
            found.update(str(r.to_concept_id) for r in relationships)
        return tuple(sorted(found, key=counter))


def _graph(relationships: Iterable[Relationship]) -> DependencyGraph:
    grouped: dict[str, list[Relationship]] = {}
    for relationship in relationships:
        grouped.setdefault(str(relationship.from_concept_id), []).append(relationship)
    return DependencyGraph(
        requirements={
            concept_id: tuple(sorted(found, key=lambda r: counter(r.id)))
            for concept_id, found in grouped.items()
        }
    )


def backward_graph(connection, target_ids: Iterable[str]) -> DependencyGraph:
    """The targets and everything they require, transitively (backward reasoning)."""
    return _graph(queries.dependency_closure(connection, target_ids))


def forward_graph(connection, seed_ids: Iterable[str]) -> tuple[DependencyGraph, tuple[str, ...]]:
    """The seeds, the concepts depending on them, and all of their requirements.

    Returns the graph and the concepts of the dependent closure (the seeds included),
    from which forward reasoning starts.
    """
    seeds = tuple(seed_ids)
    upward = queries.dependent_closure(connection, seeds)
    above = set(seeds)
    for relationship in upward:
        above.add(str(relationship.from_concept_id))
        above.add(str(relationship.to_concept_id))
    graph = _graph(queries.dependency_closure(connection, above))
    return graph, tuple(sorted(above, key=counter))


def strongly_connected(
    nodes: Iterable[str], successors: Mapping[str, tuple[str, ...]]
) -> list[tuple[str, ...]]:
    """Tarjan's strongly connected components, iterative, requirements first.

    A component is emitted only after every component it can reach, so evaluating in
    the returned order sees each requirement before the node that needs it. Nodes and
    successors are visited in numeric identifier order: the result is deterministic.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[tuple[str, ...]] = []
    counter_next = 0
    for root in sorted(set(nodes), key=counter):
        if root in index:
            continue
        work = [(root, iter(successors.get(root, ())))]
        index[root] = low[root] = counter_next
        counter_next += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter_next
                    counter_next += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(successors.get(child, ()))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(tuple(sorted(component, key=counter)))
    return components
