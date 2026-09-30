"""Phase 10 step A1: the read-only `knowledge.db` queries dependency reasoning reads.

ADR 0038 P10-3, P10-4 and ADR 0039 P10-14, P10-19, P10-20: the forward dependency
closure (backward reasoning's graph), the reverse closure (forward reasoning's graph),
and the relationships of one type touching a knowledge object (the `CONTRADICTS`
relationships of an admitted item).

What every test holds the queries to: a dependency is an ACTIVE `REQUIRES` or
`DEPENDS_ON` relationship between two concepts, returned exactly as stored; each
concept a closure reaches brings its complete stored requirement set; a cycle
terminates; and the order's last key is the numeric identifier counter, never the
identifier string (ADR 0006; ADR 0039 P10-24). Every test works in its own freshly
migrated temporary database (`tests/conftest.py`); the live database is never opened.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.models import KnowledgeType, LifecycleStatus, RelationType
from app.models.identifiers import EntityKind, format_id, parse_id
from app.storage import connect, queries
from tests.unit.query_rows import QueryRows as _Rows

REQUIRES, DEPENDS_ON = RelationType.REQUIRES, RelationType.DEPENDS_ON
CONTRADICTS = RelationType.CONTRADICTS


def _by_counter(rows) -> tuple:
    return tuple(sorted(rows, key=lambda row: parse_id(row.id)[1]))


def _section_201(rows: _Rows) -> SimpleNamespace:
    """Section 201's problem as stored relationships: X requires A and B, A requires C
    and D, C requires E. A's requirement on D is a `DEPENDS_ON`, so both approved
    types are present (ADR 0038 P10-3)."""
    g = SimpleNamespace()
    g.x, g.a, g.b, g.c, g.d, g.e = (rows.concept(name) for name in "XABCDE")
    g.x_a = rows.edge(REQUIRES, from_concept_id=g.x.id, to_concept_id=g.a.id)
    g.x_b = rows.edge(REQUIRES, from_concept_id=g.x.id, to_concept_id=g.b.id)
    g.a_c = rows.edge(REQUIRES, from_concept_id=g.a.id, to_concept_id=g.c.id)
    g.a_d = rows.edge(DEPENDS_ON, from_concept_id=g.a.id, to_concept_id=g.d.id)
    g.c_e = rows.edge(REQUIRES, from_concept_id=g.c.id, to_concept_id=g.e.id)
    return g


# ------------------------------------------------------------- the dependency types


def test_the_dependency_types_are_requires_and_depends_on_only():
    """ADR 0038 P10-3: `PREREQUISITE_OF` is study order, not a derivation dependency."""
    assert queries.DEPENDENCY_TYPES == (REQUIRES, DEPENDS_ON)
    assert RelationType.PREREQUISITE_OF not in queries.DEPENDENCY_TYPES


# ------------------------------------------------------------ the forward closure


def test_the_forward_closure_of_x_is_the_five_section_201_relationships(repo):
    g = _section_201(_Rows(repo))

    closure = queries.dependency_closure(repo.connection, [g.x.id])

    assert closure == (g.x_a, g.x_b, g.a_c, g.a_d, g.c_e)
    assert closure == _by_counter(closure)
    # As stored: the DEPENDS_ON relationship keeps its own type and direction.
    assert (g.a_d.relation_type, g.a_d.from_concept_id, g.a_d.to_concept_id) == (
        DEPENDS_ON, g.a.id, g.d.id
    )


def test_the_forward_closure_starts_where_it_is_asked(repo):
    g = _section_201(_Rows(repo))

    assert queries.dependency_closure(repo.connection, [g.a.id]) == (g.a_c, g.a_d, g.c_e)
    assert queries.dependency_closure(repo.connection, [g.c.id]) == (g.c_e,)
    # A concept with no stored requirement has none: E is a leaf.
    assert queries.dependency_closure(repo.connection, [g.e.id]) == ()


def test_seeds_are_a_set_so_their_order_and_repetition_do_not_matter(repo):
    g = _section_201(_Rows(repo))

    one = queries.dependency_closure(repo.connection, [g.c.id, g.b.id])
    assert one == queries.dependency_closure(repo.connection, [g.b.id, g.c.id, g.c.id])
    assert one == (g.c_e,)
    assert queries.dependency_closure(repo.connection, []) == ()


def test_a_dependency_cycle_terminates_and_returns_each_relationship_once(repo):
    rows = _Rows(repo)
    a, b, c = (rows.concept(name) for name in "ABC")
    a_b = rows.edge(REQUIRES, from_concept_id=a.id, to_concept_id=b.id)
    b_c = rows.edge(DEPENDS_ON, from_concept_id=b.id, to_concept_id=c.id)
    c_a = rows.edge(REQUIRES, from_concept_id=c.id, to_concept_id=a.id)

    for start in (a, b, c):
        assert queries.dependency_closure(repo.connection, [start.id]) == (a_b, b_c, c_a)
        assert queries.dependent_closure(repo.connection, [start.id]) == (a_b, b_c, c_a)


def test_only_active_relationships_are_dependencies(repo):
    """An inactive relationship is not a requirement, and is not followed."""
    rows = _Rows(repo)
    x, a, c = (rows.concept(name) for name in "XAC")
    x_a = rows.edge(REQUIRES, from_concept_id=x.id, to_concept_id=a.id)
    retired = rows.edge(
        REQUIRES, from_concept_id=a.id, to_concept_id=c.id, status=LifecycleStatus.SUPERSEDED
    )

    assert queries.dependency_closure(repo.connection, [x.id]) == (x_a,)
    assert queries.dependency_closure(repo.connection, [a.id]) == ()
    assert queries.dependent_closure(repo.connection, [c.id]) == ()
    assert retired.lifecycle_status is LifecycleStatus.SUPERSEDED


def test_other_relation_types_are_neither_returned_nor_followed(repo):
    """`PREREQUISITE_OF`, `RELATED_TO` and `USES` form no dependency (ADR 0038 P10-3)."""
    rows = _Rows(repo)
    x, a, b, p, d, q = (rows.concept(name) for name in ("X", "A", "B", "P", "D", "Q"))
    rows.edge(RelationType.USES, from_concept_id=x.id, to_concept_id=a.id)
    rows.edge(RelationType.RELATED_TO, from_concept_id=x.id, to_concept_id=b.id)
    rows.edge(RelationType.PREREQUISITE_OF, from_concept_id=p.id, to_concept_id=x.id)
    x_q = rows.edge(REQUIRES, from_concept_id=x.id, to_concept_id=q.id)
    rows.edge(REQUIRES, from_concept_id=a.id, to_concept_id=d.id)  # beyond a USES edge
    rows.edge(REQUIRES, from_concept_id=p.id, to_concept_id=d.id)  # a prerequisite's own

    assert queries.dependency_closure(repo.connection, [x.id]) == (x_q,)
    assert queries.dependent_closure(repo.connection, [x.id]) == (x_q,)


def test_only_concept_to_concept_relationships_are_dependencies(repo):
    rows = _Rows(repo)
    x, a = rows.concept("X"), rows.concept("A")
    claim = rows.knowledge(KnowledgeType.CLAIM, "X needs a stated claim.")
    x_a = rows.edge(REQUIRES, from_concept_id=x.id, to_concept_id=a.id)
    rows.edge(REQUIRES, from_concept_id=x.id, to_knowledge_id=claim.id)
    rows.edge(DEPENDS_ON, from_knowledge_id=claim.id, to_concept_id=a.id)

    assert queries.dependency_closure(repo.connection, [x.id]) == (x_a,)
    assert queries.dependent_closure(repo.connection, [a.id]) == (x_a,)


def test_the_closures_are_in_numeric_counter_order(repo):
    """`REL-100000000` sorts before `REL-99999999` as a string; the counter decides."""
    rows = _Rows(repo)
    x, a, b, c = (rows.concept(name) for name in "XABC")
    first = rows.edge(REQUIRES, from_concept_id=x.id, to_concept_id=a.id)
    high = rows.edge(
        REQUIRES, format_id(EntityKind.RELATIONSHIP, 100_000_000),
        from_concept_id=x.id, to_concept_id=b.id,
    )
    low = rows.edge(
        DEPENDS_ON, format_id(EntityKind.RELATIONSHIP, 99_999_999),
        from_concept_id=x.id, to_concept_id=c.id,
    )
    assert high.id < low.id  # what string order would do

    assert queries.dependency_closure(repo.connection, [x.id]) == (first, low, high)
    assert queries.dependent_closure(repo.connection, [a.id]) == (first, low, high)


# ------------------------------------------------------------ the reverse closure


def test_the_reverse_closure_brings_each_dependents_complete_requirement_set(repo):
    """From E: C, A and X depend on it. Each brings its whole stored set - A's
    requirement on D and X's on B lie outside the closure and are still returned,
    so forward reasoning never derives A or X from part of its set (P10-20)."""
    g = _section_201(_Rows(repo))

    assert queries.dependent_closure(repo.connection, [g.e.id]) == (
        g.x_a, g.x_b, g.a_c, g.a_d, g.c_e
    )


def test_the_reverse_closure_reaches_only_the_concepts_that_depend_on_the_seeds(repo):
    g = _section_201(_Rows(repo))

    # A and X depend on D; C does not, so C's requirement on E is absent.
    assert queries.dependent_closure(repo.connection, [g.d.id]) == (g.x_a, g.x_b, g.a_c, g.a_d)
    # Only X depends on B.
    assert queries.dependent_closure(repo.connection, [g.b.id]) == (g.x_a, g.x_b)
    # Nothing depends on X; X's own set is still present (the seeds belong to the closure).
    assert queries.dependent_closure(repo.connection, [g.x.id]) == (g.x_a, g.x_b)
    assert queries.dependent_closure(repo.connection, []) == ()


# ------------------------------------------ relationships touching a knowledge object


def test_contradicts_relationships_touching_a_knowledge_object_on_either_end(repo):
    rows = _Rows(repo)
    x = rows.concept("X")
    k1, k2, k3 = (
        rows.knowledge(KnowledgeType.CLAIM, f"Claim {n} about X.") for n in (1, 2, 3)
    )
    k1_k2 = rows.edge(CONTRADICTS, from_knowledge_id=k1.id, to_knowledge_id=k2.id)
    x_k1 = rows.edge(CONTRADICTS, from_concept_id=x.id, to_knowledge_id=k1.id)
    rows.edge(CONTRADICTS, from_knowledge_id=k2.id, to_knowledge_id=k3.id)  # not k1's
    rows.edge(RelationType.DEFINED_BY, from_concept_id=x.id, to_knowledge_id=k3.id)
    rows.edge(RelationType.DEFINED_BY, from_concept_id=rows.concept("Y").id, to_knowledge_id=k1.id)
    old = rows.edge(
        CONTRADICTS, from_knowledge_id=k3.id, to_knowledge_id=k1.id,
        status=LifecycleStatus.SUPERSEDED,
    )

    touching = queries.relationships_touching_knowledge(repo.connection, k1.id, CONTRADICTS)

    assert touching == (k1_k2, x_k1)  # both ends count; the other type is left out
    assert touching == _by_counter(touching)
    assert queries.relationships_touching_knowledge(
        repo.connection, k1.id, CONTRADICTS, active_only=False
    ) == (k1_k2, x_k1, old)
    assert queries.relationships_touching_knowledge(
        repo.connection, rows.knowledge(KnowledgeType.CLAIM, "Alone.").id, CONTRADICTS
    ) == ()


# ------------------------------------------------------------ read-only behaviour


def test_the_queries_write_nothing_and_run_on_a_read_only_connection(repo, db_path):
    """Phase 10 reads `knowledge.db` read-only and writes nothing (ADR 0038 P10-9)."""
    rows = _Rows(repo)
    g = _section_201(rows)
    k1, k2 = (rows.knowledge(KnowledgeType.CLAIM, f"Claim {n}.") for n in (1, 2))
    rows.edge(CONTRADICTS, from_knowledge_id=k1.id, to_knowledge_id=k2.id)
    rows.commit()

    def ask(connection) -> tuple:
        return (
            queries.dependency_closure(connection, [g.x.id]),
            queries.dependent_closure(connection, [g.e.id]),
            queries.relationships_touching_knowledge(connection, k1.id, CONTRADICTS),
        )

    writable = repo.connection
    before = writable.total_changes
    answer = ask(writable)
    assert all(answer)  # non-empty, so equality means something
    assert ask(writable) == answer  # the same answer every time
    assert writable.total_changes == before

    reader = connect(db_path, read_only=True)
    try:
        assert queries.connection_is_read_only(reader) is True
        assert ask(reader) == answer
    finally:
        reader.close()
