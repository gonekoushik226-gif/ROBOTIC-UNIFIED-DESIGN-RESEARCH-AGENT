"""Traversal over stored edges only (ADR 0036 P9-14).

* **One hop** from each resolved concept, in both directions, across every relation
  type (`QueryContext.edges_of`).
* **Hierarchy closures**: ancestors and descendants over stored ACTIVE `PARENT_OF`
  edges (D-23; section 42) - the edges `queries.ancestors_of_concept` and
  `descendants_of_concept` follow, walked here one hop at a time so that every
  edge on the way is scope-checked (ADR 0035 P9-5). Cycle-safe: a concept is
  visited once, and the starting concept is never its own ancestor.
* **Labels** (section 40): every edge says how it was established. An `INFERRED`
  edge is never presented as stated; a rule-R1 edge is *"related by definition
  mention (rule R1)"*, with its recorded basis (ADR 0028, ADR 0027).
* **Nothing new is inferred** and neighbours are named, never expanded.

`label` returns None for an edge with nothing in scope behind it - neither a
relationship occurrence nor a recorded basis occurrence - and that edge is withheld.
"""

from app.models.entities import Relationship
from app.models.enums import RelationshipOrigin, RelationType
from app.query.results import EdgeBasis, LinkedEdge
from app.query.scope import QueryContext, counter
from app.storage import queries

#: The rule name rule R1 records on its bases (`app/classification/rule_r1.py`, ADR 0028).
RULE_R1 = "R1"

EXPLICIT_LABEL = "EXPLICIT - stated by a source"
R1_LABEL = "INFERRED - related by definition mention (rule R1); not stated by a source"
INFERRED_LABEL = "INFERRED - inferred by RUDRA; not stated by a source"


def label(context: QueryContext, edge: Relationship) -> LinkedEdge | None:
    """The edge with what establishes it in scope, or None when nothing does."""
    if edge.id in context.labelled:
        return context.labelled[edge.id]  # type: ignore[return-value]
    stated = context.evidence(edge.id)
    inferences = queries.inferences_for_relationship(context.connection, edge.id)
    basis_rows = {
        inference.id: context.basis_row(inference.basis_occurrence_id)
        for inference in inferences
    }
    kept = context.judge_item(
        edge.id, stated + tuple(row for row in basis_rows.values() if row is not None)
    )
    linked: LinkedEdge | None = None
    if kept:
        kept_ids = {row.id for row in kept}
        bases = []
        for inference in inferences:
            row = basis_rows[inference.id]
            if row is not None and row.id not in kept_ids:
                continue  # its occurrence is withheld, so the basis is too
            bases.append(
                EdgeBasis(
                    inference=inference,
                    occurrence=row,
                    mentioning_concept_ids=(
                        queries.concepts_defined_by(context.connection, row.subject_id)
                        if row is not None
                        else ()
                    ),
                )
            )
        if edge.origin is RelationshipOrigin.EXPLICIT:
            text = EXPLICIT_LABEL
        elif any(basis.inference.rule == RULE_R1 for basis in bases):
            text = R1_LABEL
        else:
            text = INFERRED_LABEL
        linked = LinkedEdge(
            relationship=edge,
            label=text,
            evidence=tuple(row for row in stated if row.id in kept_ids),
            bases=tuple(sorted(bases, key=lambda basis: counter(basis.inference.id))),
        )
    context.labelled[edge.id] = linked
    return linked


def closure(context: QueryContext, concept_id: str, *, upward: bool) -> tuple[str, ...]:
    """Ancestors (`upward`) or descendants over stored `PARENT_OF` edges in scope."""
    seen: set[str] = set()
    frontier = [concept_id]
    while frontier:
        current = frontier.pop()
        for edge in context.edges_of(current):
            if edge.relation_type is not RelationType.PARENT_OF:
                continue
            if upward:
                step = edge.from_concept_id if edge.to_concept_id == current else None
            else:
                step = edge.to_concept_id if edge.from_concept_id == current else None
            if step is None or step == concept_id or step in seen:
                continue
            if not context.edge_allowed(edge) or label(context, edge) is None:
                continue
            seen.add(step)
            frontier.append(step)
    return tuple(sorted(seen, key=counter))
