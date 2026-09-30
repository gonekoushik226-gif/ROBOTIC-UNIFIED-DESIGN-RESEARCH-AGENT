"""Phase 10 Batch B: the dependency-based reasoning engine (ADRs 0038-0040).

Section 201's two variants (U1(c)), the section 166 reasoning cases, and the rules the
engine is held to: the stored requirement set, complete and never invented (P10-3,
P10-4); the requirement-set rule, with an empty set deriving nothing (P10-12); node
states and why (P10-11); cycles reported, never looped (P10-20); conflicts blocking and
shown, never resolved (P10-19); availability only through the request (OI-1 = B);
authorisation, scope and lifecycle (P9-5, P9-23); no D1 attachment and no D2 widening
(P10-24); deterministic output; nothing written (U5(a), U10a(i)).

Every expected state, step, path and missing dependency below is written by hand from
the stored relationships, never computed by the code under test. Every test works in
its own freshly migrated temporary database (`tests/conftest.py`); the live database is
never opened.
"""

from __future__ import annotations

import pytest

from app.core.errors import InvalidInputError
from app.models import (
    Authorization,
    ConceptEquivalenceStatus,
    KnowledgeType,
    LifecycleStatus,
    RelationType,
    SourceCategory,
)
from app.models.identifiers import EntityKind, format_id
from app.reasoning import (
    RULE,
    RULE_VERSION,
    Admission,
    AnswerStatus,
    Assumption,
    BlockReason,
    NodeState,
    Origin,
    ReasoningRequest,
    ReasoningScope,
    UserInput,
    reason,
    to_json,
)
from app.reasoning import engine as engine_module
from app.reasoning.results import Provenance
from app.storage import Repository, connect
from tests.unit.query_rows import STATED
from tests.unit.query_rows import QueryRows as _Rows

REQUIRES, DEPENDS_ON = RelationType.REQUIRES, RelationType.DEPENDS_ON
AVAILABLE, DERIVED, MISSING = NodeState.AVAILABLE, NodeState.DERIVED, NodeState.MISSING
BLOCKED, CONFLICTING = NodeState.BLOCKED, NodeState.CONFLICTING


class _World:
    """Stored concepts and relationships with evidence, as extraction stores them."""

    def __init__(self, repo) -> None:
        self.repo = repo
        self.rows = _Rows(repo)
        self.book = self.rows.document("book")
        self.src = self.rows.source(self.book)
        self.run = self.rows.run(self.book)
        self.seg = self.rows.segment(self.book, 1)
        self._at = 0

    def _where(self, source):
        if source is None or source is self.src:
            self._at += 10
            return self.src, {"page": 1, "segment": self.seg, "span": (self._at, self._at + 5), "run": self.run}
        return source, {"page": 1}

    def concept(self, name: str, source=None):
        concept = self.rows.concept(name)
        where_source, where = self._where(source)
        self.rows.concept_occurrence(concept, where_source, **where)
        return concept

    def concepts(self, names: str):
        return tuple(self.concept(name) for name in names)

    def edge(self, kind, source=None, *, status=LifecycleStatus.ACTIVE, **ends):
        edge = self.rows.edge(kind, status=status, **ends)
        where_source, where = self._where(source)
        self.rows.relationship_occurrence(edge, where_source, **where)
        return edge

    def requires(self, x, a, kind=REQUIRES, source=None, **options):
        return self.edge(kind, source, from_concept_id=x.id, to_concept_id=a.id, **options)

    def knowledge(self, statement, source=None, *, kind=KnowledgeType.DEFINITION, status=LifecycleStatus.ACTIVE):
        item = self.rows.knowledge(kind, statement, status=status)
        where_source, where = self._where(source)
        self.rows.knowledge_occurrence(item, where_source, **where)
        return item

    def link(self, concept, knowledge, kind=RelationType.DEFINED_BY):
        return self.edge(kind, from_concept_id=concept.id, to_knowledge_id=knowledge.id)

    def other_source(self, name, **options):
        return self.rows.source(self.rows.document(name), **options)

    def section_201(self):
        """X requires A and B; A requires C and D (a DEPENDS_ON); C requires E."""
        self.x, self.a, self.b, self.c, self.d, self.e = self.concepts("XABCDE")
        self.x_a = self.requires(self.x, self.a)
        self.x_b = self.requires(self.x, self.b)
        self.a_c = self.requires(self.a, self.c)
        self.a_d = self.requires(self.a, self.d, DEPENDS_ON)
        self.c_e = self.requires(self.c, self.e)
        return self


def _run(repo, target="X", **options):
    return reason(repo, ReasoningRequest.of_target(target, **options))


def _states(method) -> dict[str, NodeState]:
    return {node.concept.canonical_name: node.state for node in method.nodes}


def _node(method, concept):
    return next(node for node in method.nodes if node.concept.id == concept.id)


def _path(path) -> list[tuple[str, NodeState]]:
    return [(node.name, node.state) for node in path.nodes]


# ------------------------------------------------------------- section 201 (U1(c))


def test_section_201_literal_variant(repo):
    """E known: C derives from E; A is blocked by missing D; X by missing B and blocked A.
    The path E -> C -> A -> X is reported although X cannot be determined."""
    w = _World(repo).section_201()

    result = _run(repo, inputs=(UserInput("E"),))

    assert result.status is AnswerStatus.CANNOT_DETERMINE
    (method,) = result.methods
    assert method.target == w.x and method.state is BLOCKED
    assert _states(method) == {"X": BLOCKED, "A": BLOCKED, "B": MISSING, "C": DERIVED, "D": MISSING, "E": AVAILABLE}
    assert _node(method, w.e).origins == (Origin.USER_INPUT,)
    assert _node(method, w.c).origins == (Origin.DERIVED,)
    assert [(b.reason, b.concept_id, b.relationship_id) for b in _node(method, w.a).blocks] == [
        (BlockReason.MISSING_REQUIREMENT, w.d.id, w.a_d.id),
    ]
    assert [(b.reason, b.concept_id, b.relationship_id) for b in _node(method, w.x).blocks] == [
        (BlockReason.BLOCKED_REQUIREMENT, w.a.id, w.x_a.id),
        (BlockReason.MISSING_REQUIREMENT, w.b.id, w.x_b.id),
    ]
    # One application of the rule: C from E.
    assert [(s.number, s.concept_id, s.requirements, s.relationships, s.rule, s.rule_version)
            for s in method.steps] == [(1, w.c.id, (w.e.id,), (w.c_e.id,), RULE, RULE_VERSION)]
    # The structural path, inputs first, each node with its state.
    assert [_path(p) for p in method.paths] == [
        [("E", AVAILABLE), ("C", DERIVED), ("A", BLOCKED), ("X", BLOCKED)]
    ]
    assert method.paths_complete is True
    # The missing dependencies, each with the stored requirement that needs it.
    assert [(m.concept.id, [(r.concept_id, r.relationship_id) for r in m.required_by])
            for m in method.missing] == [
        (w.b.id, [(w.x.id, w.x_b.id)]),
        (w.d.id, [(w.a.id, w.a_d.id)]),
    ]
    assert "Cannot determine X" in result.message and "missing" in result.message


def test_section_201_completed_variant(repo):
    """E, D and B available: X is derived through E -> C -> A -> X in three steps."""
    w = _World(repo).section_201()

    result = _run(repo, inputs=(UserInput("E"), UserInput("D"), UserInput("B")))

    assert result.status is AnswerStatus.DETERMINED
    (method,) = result.methods
    assert method.state is DERIVED
    assert _states(method) == {"X": DERIVED, "A": DERIVED, "B": AVAILABLE, "C": DERIVED, "D": AVAILABLE, "E": AVAILABLE}
    assert [(s.number, s.concept_id, s.requirements, s.relationships) for s in method.steps] == [
        (1, w.c.id, (w.e.id,), (w.c_e.id,)),
        (2, w.a.id, (w.c.id, w.d.id), (w.a_c.id, w.a_d.id)),
        (3, w.x.id, (w.a.id, w.b.id), (w.x_a.id, w.x_b.id)),
    ]
    assert [[name for name, _ in _path(p)] for p in method.paths] == [
        ["B", "X"], ["D", "A", "X"], ["E", "C", "A", "X"],
    ]
    assert method.missing == () and method.cycles == ()
    assert "determined by 1 of 1 method(s); none is selected" in result.message


def test_x_is_never_retrieved_from_text_as_a_substitute_for_reasoning(repo):
    """A stored statement that X follows from E, linked to X, changes nothing: X has no
    stored requirement, so it is MISSING (sections 8, 201)."""
    w = _World(repo)
    x, e = w.concepts("XE")
    w.link(x, w.knowledge("X is derived from E, so X is known whenever E is known."))

    result = _run(repo, inputs=(UserInput("E"),))

    (method,) = result.methods
    assert result.status is AnswerStatus.CANNOT_DETERMINE
    assert method.state is MISSING and method.steps == () and method.paths == ()
    assert [n.concept.id for n in method.nodes] == [x.id]


# ------------------------------------------------------ the section 166 reasoning cases


def test_direct_answer_by_user_input_or_admission(repo):
    w = _World(repo).section_201()
    definition = w.knowledge("X is a stated quantity.")

    given = _run(repo, inputs=(UserInput("X"),)).methods[0]
    admitted = _run(repo, admissions=(Admission("X", definition.id),)).methods[0]

    assert given.state is AVAILABLE and given.steps == ()
    assert _node(given, w.x).origins == (Origin.USER_INPUT,)
    assert admitted.state is AVAILABLE
    assert _node(admitted, w.x).origins == (Origin.ADMITTED_STORED_ITEM,)
    assert _node(admitted, w.x).admitted == (definition.id,)


def test_one_step_and_multi_step_derivation(repo):
    w = _World(repo)
    t, e = w.concepts("TE")
    t_e = w.requires(t, e)
    p, q, r, s = w.concepts("PQRS")
    w.requires(p, q)
    w.requires(q, r, DEPENDS_ON)
    w.requires(r, s)

    one = _run(repo, "T", inputs=(UserInput("E"),)).methods[0]
    many = _run(repo, "P", inputs=(UserInput("S"),)).methods[0]

    assert one.state is DERIVED
    assert [(s_.concept_id, s_.relationships) for s_ in one.steps] == [(t.id, (t_e.id,))]
    assert many.state is DERIVED
    assert [step.concept_id for step in many.steps] == [r.id, q.id, p.id]


def test_a_node_with_no_stored_requirement_is_missing_never_derived(repo):
    """An empty requirement set derives nothing (ADR 0039 P10-12)."""
    w = _World(repo)
    w.concepts("XE")

    (method,) = _run(repo, inputs=(UserInput("E"),)).methods

    assert method.state is MISSING
    assert [(m.concept.canonical_name, m.required_by) for m in method.missing] == [("X", ())]


def test_part_of_a_requirement_set_never_derives_its_node(repo):
    """U2b(i): the complete stored set must be available; A alone is not enough."""
    w = _World(repo)
    x, a, b = w.concepts("XAB")
    w.requires(x, a)
    x_b = w.requires(x, b)

    (method,) = _run(repo, inputs=(UserInput("A"),)).methods

    assert method.state is BLOCKED
    assert [(bl.reason, bl.concept_id, bl.relationship_id) for bl in _node(method, x).blocks] == [
        (BlockReason.MISSING_REQUIREMENT, b.id, x_b.id)
    ]
    assert method.steps == ()


def test_several_paths_are_all_retained(repo):
    """X requires A and B, both of which require E: two paths from E, neither chosen."""
    w = _World(repo)
    x, a, b, e = w.concepts("XABE")
    w.requires(x, a)
    w.requires(x, b)
    w.requires(a, e)
    w.requires(b, e, DEPENDS_ON)

    (method,) = _run(repo, inputs=(UserInput("E"),)).methods

    assert method.state is DERIVED
    assert [[name for name, _ in _path(p)] for p in method.paths] == [["E", "A", "X"], ["E", "B", "X"]]


def test_several_methods_for_one_target_are_evaluated_separately(repo):
    """Two concepts answer to "T", each with its own stored set: two methods, one derived
    and one blocked, both reported and none selected (U9(b), ADR 0039 P10-17)."""
    w = _World(repo)
    t1, e = w.concepts("TE")
    t2, f = w.concepts("TF")
    w.requires(t1, e)
    w.requires(t2, f)

    result = _run(repo, "T", inputs=(UserInput("E"),))

    assert [(m.target.id, m.state) for m in result.methods] == [(t1.id, DERIVED), (t2.id, BLOCKED)]
    assert result.status is AnswerStatus.DETERMINED
    assert "determined by 1 of 2 method(s); none is selected" in result.message
    assert [m.concept.id for m in result.methods[1].missing] == [f.id]


# ------------------------------------------------------------------------ cycles


def test_a_dependency_cycle_is_reported_and_blocks_never_loops(repo):
    w = _World(repo)
    x, a, b, e = w.concepts("XABE")
    w.requires(x, a)
    w.requires(a, b)
    w.requires(b, a, DEPENDS_ON)
    w.requires(b, e)

    (method,) = _run(repo, inputs=(UserInput("E"),)).methods

    assert method.cycles == ((a.id, b.id),)
    for node in (a, b):
        assert _node(method, node).state is BLOCKED
        assert [bl.reason for bl in _node(method, node).blocks] == [BlockReason.ON_CYCLE]
        assert _node(method, node).cycle == (a.id, b.id)
    assert _node(method, x).blocks[0].reason is BlockReason.BLOCKED_REQUIREMENT
    assert method.state is BLOCKED and method.steps == ()


def test_a_node_the_request_supplies_on_a_cycle_stays_available(repo):
    """The request's own statement stands; the rest of the cycle stays BLOCKED (P10-20)."""
    w = _World(repo)
    a, b = w.concepts("AB")
    w.requires(a, b)
    w.requires(b, a)

    (method,) = _run(repo, "A", inputs=(UserInput("B"),)).methods

    assert _node(method, b).state is AVAILABLE
    assert _node(method, a).state is BLOCKED
    assert _node(method, a).blocks[0].reason is BlockReason.ON_CYCLE


# --------------------------------------------------------------------- conflicts


def _conflicting_d(w):
    """D's stored definition conflicts with another authorised book's definition."""
    w.section_201()
    w.def_d = w.knowledge("D is five units.")
    w.link(w.d, w.def_d)
    w.other = w.knowledge("D is seven units.", w.other_source("book-2"))
    w.conflict = w.rows.conflict(w.def_d, w.other)
    return w


def test_a_conflicting_input_blocks_the_derivations_that_depend_on_it(repo):
    w = _conflicting_d(_World(repo))

    result = _run(repo, inputs=(UserInput("E"), UserInput("D"), UserInput("B")))

    (method,) = result.methods
    d = _node(method, w.d)
    assert d.state is CONFLICTING  # supplied, but never hidden
    assert [bl.reason for bl in _node(method, w.a).blocks] == [BlockReason.CONFLICTING_REQUIREMENT]
    assert method.state is BLOCKED and result.status is AnswerStatus.CANNOT_DETERMINE
    (item,) = d.conflicts
    assert item.conflict == w.conflict
    assert (item.claim_a.knowledge, item.claim_b.knowledge) == (w.def_d, w.other)
    assert item.resolution == "not automatically selected"
    assert item.claim_a.provenance.sources == (w.src,)
    (other_source,) = item.claim_b.provenance.sources
    assert other_source != w.src and other_source.document_id != w.book.id
    assert [step.concept_id for step in method.steps] == [w.c.id]  # C is still derived


def test_a_contradicts_relationship_touching_a_node_makes_it_conflicting(repo):
    w = _World(repo).section_201()
    claim = w.knowledge("C cannot follow from E.", kind=KnowledgeType.CLAIM)
    contradiction = w.edge(RelationType.CONTRADICTS, from_concept_id=w.c.id, to_knowledge_id=claim.id)

    (method,) = _run(repo, inputs=(UserInput("E"), UserInput("D"), UserInput("B"))).methods

    c = _node(method, w.c)
    assert c.state is CONFLICTING
    assert [item.relationship for item in c.contradictions] == [contradiction]
    assert method.state is BLOCKED


def test_a_conflict_whose_other_claim_is_out_of_scope_attaches_with_that_claim_withheld(repo):
    """P10-19 attaches a conflict through the claim linked to the node, as Phase 9 does
    (P9-15), whatever the other claim's scope. The other claim's source is not
    authorised, so that claim is withheld - identifier kept, content never shown - and
    D is still CONFLICTING: nothing is derived through it."""
    w = _World(repo).section_201()
    def_d = w.knowledge("D is five units.")
    w.link(w.d, def_d)
    hidden = w.knowledge("D is nine units.", w.other_source("closed", authorization=Authorization.NOT_AUTHORIZED))
    conflict = w.rows.conflict(def_d, hidden)

    result = _run(repo, inputs=(UserInput("E"), UserInput("D"), UserInput("B")))

    (method,) = result.methods
    d = _node(method, w.d)
    assert d.state is CONFLICTING
    assert [bl.reason for bl in _node(method, w.a).blocks] == [BlockReason.CONFLICTING_REQUIREMENT]
    assert _node(method, w.x).state is BLOCKED and method.state is BLOCKED
    assert result.status is AnswerStatus.CANNOT_DETERMINE
    assert [step.concept_id for step in method.steps] == [w.c.id]  # nothing derived through D
    (item,) = d.conflicts
    assert item.conflict == conflict and item.resolution == "not automatically selected"
    assert (item.claim_a.knowledge_id, item.claim_a.knowledge) == (def_d.id, def_d)
    assert item.claim_a.provenance.sources == (w.src,)
    assert (item.claim_b.knowledge_id, item.claim_b.knowledge) == (hidden.id, None)
    assert (item.claim_b.evidence, item.claim_b.provenance) == ((), Provenance())
    assert result.withheld.knowledge >= 1
    assert "nine units" not in to_json(result)  # the withheld content is never shown
    again = _run(repo, inputs=(UserInput("E"), UserInput("D"), UserInput("B")))
    assert to_json(again) == to_json(result)  # byte-identical with a withheld claim too


def test_a_conflicts_other_claim_stored_deleted_is_withheld_and_the_conflict_attaches(repo):
    w = _World(repo).section_201()
    def_d = w.knowledge("D is five units.")
    w.link(w.d, def_d)
    retired = w.knowledge("D is six units.", status=LifecycleStatus.DELETED)
    w.rows.conflict(def_d, retired)

    result = _run(repo, inputs=(UserInput("E"), UserInput("D"), UserInput("B")))

    d = _node(result.methods[0], w.d)
    assert d.state is CONFLICTING and result.methods[0].state is BLOCKED
    (item,) = d.conflicts
    assert (item.claim_b.knowledge_id, item.claim_b.knowledge) == (retired.id, None)
    assert result.withheld.excluded_by_lifecycle >= 1
    assert "six units" not in to_json(result)


def test_a_conflict_naming_an_admitted_item_belongs_to_its_node(repo):
    w = _World(repo).section_201()
    item = w.knowledge("D, as the request admits it.")
    rival = w.knowledge("D, as another passage states it.")
    w.rows.conflict(item, rival)

    (method,) = _run(
        repo, inputs=(UserInput("E"), UserInput("B")), admissions=(Admission("D", item.id),)
    ).methods

    assert _node(method, w.d).state is CONFLICTING
    assert method.state is BLOCKED


# ------------------------------------------------------------ what is not a requirement


def test_no_requirement_is_invented_and_only_the_approved_types_count(repo):
    """Only REQUIRES and DEPENDS_ON between concepts: USES, RELATED_TO,
    PREREQUISITE_OF and EQUIVALENT_TO are not requirements (ADR 0038 P10-3)."""
    w = _World(repo)
    x, a, u, r, p, q = w.concepts("XAURPQ")
    x_a = w.requires(x, a)
    w.edge(RelationType.USES, from_concept_id=x.id, to_concept_id=u.id)
    w.edge(RelationType.RELATED_TO, from_concept_id=x.id, to_concept_id=r.id)
    w.edge(RelationType.PREREQUISITE_OF, from_concept_id=p.id, to_concept_id=x.id)
    w.edge(RelationType.EQUIVALENT_TO, from_concept_id=q.id, to_concept_id=x.id)

    (method,) = _run(repo, inputs=(UserInput("A"),)).methods

    assert [link.relationship for link in _node(method, x).requirements] == [x_a]
    assert [n.concept.id for n in method.nodes] == [x.id, a.id]
    assert method.state is DERIVED


def test_a_target_with_no_stored_requirements_and_no_input_is_itself_missing(repo):
    w = _World(repo)
    (x,) = w.concepts("X")

    result = _run(repo)

    (method,) = result.methods
    assert result.status is AnswerStatus.CANNOT_DETERMINE and method.state is MISSING
    assert [(m.concept.id, m.required_by) for m in method.missing] == [(x.id, ())]
    assert result.message.endswith(f"missing: X ({x.id}) - the target itself.")


def test_d2_equivalence_is_never_dependency_evidence(repo):
    """A concept stated equivalent to X, or recorded as possibly equivalent, lends X
    none of its requirements (ADR 0039 P10-24; P9-4)."""
    w = _World(repo)
    x, twin, e = w.concepts("X") + w.concepts("W") + w.concepts("E")
    stated = w.edge(RelationType.EQUIVALENT_TO, from_concept_id=twin.id, to_concept_id=x.id)
    w.rows.equivalence(
        twin, x, status=ConceptEquivalenceStatus.POSSIBLE_EQUIVALENT, basis=STATED, relationship=stated
    )
    w.requires(twin, e)

    (method,) = _run(repo, inputs=(UserInput("E"),)).methods

    assert method.state is MISSING
    assert [n.concept.id for n in method.nodes] == [x.id]


def test_d1_no_knowledge_object_becomes_a_node(repo):
    """An equation or other knowledge object is never a requirement or a node (D1)."""
    w = _World(repo)
    x, e = w.concepts("XE")
    equation = w.knowledge("X = E + 1", kind=KnowledgeType.EQUATION)
    w.edge(REQUIRES, from_concept_id=x.id, to_knowledge_id=equation.id)
    w.link(x, equation)

    (method,) = _run(repo, inputs=(UserInput("E"),)).methods

    assert method.state is MISSING
    assert all(n.concept.id != equation.id for n in method.nodes)
    assert _node(method, x).requirements == ()


# ------------------------------------------------ provenance, authorisation, lifecycle


def test_every_requirement_carries_its_evidence_and_provenance(repo):
    w = _World(repo).section_201()

    (method,) = _run(repo, inputs=(UserInput("E"),)).methods

    (link,) = _node(method, w.c).requirements
    assert link.relationship == w.c_e and link.required_id == w.e.id
    assert [row.subject_id for row in link.evidence] == [w.c_e.id]
    assert link.provenance.sources == (w.src,)
    assert [d.document for d in link.provenance.documents] == [w.book]
    assert link.provenance.runs == (w.run,)


def test_user_input_and_an_admitted_item_stay_distinct_origins(repo):
    w = _World(repo).section_201()
    definition = w.knowledge("E is a stated quantity.")

    result = _run(repo, inputs=(UserInput("E"),), admissions=(Admission("E", definition.id),))

    e = _node(result.methods[0], w.e)
    assert e.origins == (Origin.USER_INPUT, Origin.ADMITTED_STORED_ITEM)
    assert e.admitted == (definition.id,)
    assert result.initial.admitted[0].provenance.sources == (w.src,)


def test_a_requirement_without_evidence_in_scope_blocks_and_is_never_shown(repo):
    """X requires A (stated in book) and B (stated only in a closed book): with A and B
    both supplied, X is still not derived from the rest of its set (P10-20)."""
    w = _World(repo)
    x, a, b = w.concepts("XAB")
    w.requires(x, a)
    closed = w.other_source("closed", authorization=Authorization.NOT_AUTHORIZED)
    w.requires(x, b, source=closed)

    (method,) = _run(repo, inputs=(UserInput("A"), UserInput("B"))).methods

    node = _node(method, x)
    assert node.state is BLOCKED
    assert [bl.reason for bl in node.blocks] == [BlockReason.WITHHELD_REQUIREMENT]
    assert node.withheld_requirements == 1 and len(node.requirements) == 1
    assert b.id not in {n.concept.id for n in method.nodes}


def test_a_requirement_concept_without_evidence_in_scope_is_withheld(repo):
    w = _World(repo)
    x = w.concept("X")
    hidden = w.concept("H", w.other_source("closed", authorization=Authorization.NOT_AUTHORIZED))
    w.requires(x, hidden)

    result = _run(repo, inputs=(UserInput("H"),))

    node = _node(result.methods[0], x)
    assert node.state is BLOCKED and node.withheld_requirements == 1
    assert result.withheld.concepts >= 1


def test_the_requested_scope_decides_what_is_used(repo):
    """Evidence from an authorised external source is outside "my books" but inside
    the AUTHORIZED scope (P9-5)."""
    w = _World(repo)
    x, a = w.concepts("XA")
    external = w.other_source("external", category=SourceCategory.AUTHORIZED_EXTERNAL_SOURCE)
    w.requires(x, a, source=external)

    mine = _run(repo, inputs=(UserInput("A"),)).methods[0]
    wider = _run(repo, inputs=(UserInput("A"),), scope=ReasoningScope.AUTHORIZED).methods[0]

    assert mine.state is BLOCKED and wider.state is DERIVED


def test_a_target_without_evidence_in_scope_is_insufficient_information(repo):
    w = _World(repo)
    w.concept("X", w.other_source("closed", authorization=Authorization.NOT_AUTHORIZED))

    result = _run(repo)

    assert result.status is AnswerStatus.INSUFFICIENT_AUTHORIZED_INFORMATION
    assert result.methods == ()


def test_deleted_and_archived_concepts_and_items_are_never_used(repo):
    from dataclasses import replace

    w = _World(repo)
    x, a = w.concepts("XA")
    w.requires(x, a)
    repo.update(replace(a, lifecycle_status=LifecycleStatus.DELETED))
    gone = w.concept("G")
    repo.update(replace(gone, lifecycle_status=LifecycleStatus.ARCHIVED))

    result = _run(repo, inputs=(UserInput("X"),))
    blocked = _run(repo, "X")

    node = _node(blocked.methods[0], x)
    assert node.state is BLOCKED and node.excluded_requirements == 1
    assert [bl.reason for bl in node.blocks] == [BlockReason.EXCLUDED_REQUIREMENT]
    assert blocked.withheld.excluded_by_lifecycle >= 1
    assert result.methods[0].state is AVAILABLE  # the request's own statement
    assert _run(repo, "G").status is AnswerStatus.NOT_FOUND  # an archived target is not used
    retired = w.knowledge("A deleted item.", status=LifecycleStatus.DELETED)
    refused = _run(repo, admissions=(Admission("X", retired.id),))
    assert refused.methods[0].state is not AVAILABLE and refused.initial.refused


def test_an_inactive_relationship_is_not_a_requirement(repo):
    w = _World(repo)
    x, a = w.concepts("XA")
    w.requires(x, a, status=LifecycleStatus.SUPERSEDED)

    (method,) = _run(repo, inputs=(UserInput("A"),)).methods

    assert method.state is MISSING  # no ACTIVE stored requirement: nothing to derive X from


def test_a_superseded_admitted_item_is_used_and_keeps_its_pointer(repo):
    w = _World(repo).section_201()
    canonical = w.knowledge("D is a stated quantity.")
    duplicate = w.knowledge("D is a stated quantity (twin).")
    w.rows.supersede(canonical, duplicate)

    result = _run(repo, inputs=(UserInput("E"), UserInput("B")), admissions=(Admission("D", duplicate.id),))

    assert result.methods[0].state is DERIVED
    (admitted,) = result.initial.admitted
    assert admitted.knowledge.lifecycle_status is LifecycleStatus.SUPERSEDED
    assert admitted.superseded_by == canonical.id
    assert _node(result.methods[0], w.d).origins == (Origin.ADMITTED_STORED_ITEM,)


def test_stored_knowledge_is_not_available_without_an_admission(repo):
    """OI-1 = Option B: D's stored, linked definition does not make D available."""
    w = _World(repo).section_201()
    w.link(w.d, w.knowledge("D is a stated quantity."))

    (method,) = _run(repo, inputs=(UserInput("E"), UserInput("B"))).methods

    assert _node(method, w.d).state is MISSING and method.state is BLOCKED


# -------------------------------------------------------------------- assumptions


def test_an_assumption_makes_its_node_available_and_the_result_conditional(repo):
    w = _World(repo).section_201()

    result = _run(repo, inputs=(UserInput("E"), UserInput("D")), assumptions=(Assumption("B", "B holds."),))

    (method,) = result.methods
    assert method.state is DERIVED
    assert _node(method, w.b).origins == (Origin.ASSUMPTION,)
    assert _node(method, w.x).conditional_on == (w.b.id,)
    assert _node(method, w.a).conditional_on == ()
    assert method.steps[-1].conditional_on == (w.b.id,)
    assert any("assumption" in note for note in result.notes)
    assert "conditional on the assumption(s)" in result.message


# ---------------------------------------------------------------- forward reasoning


def test_forward_reasoning_derives_what_follows_and_agrees_with_backward(repo):
    w = _World(repo).section_201()
    unrelated = w.concept("U")
    inputs = (UserInput("E"), UserInput("D"), UserInput("B"))

    forward = reason(repo, ReasoningRequest.forward(inputs=inputs))
    backward = _run(repo, inputs=inputs)

    assert forward.status is AnswerStatus.DETERMINED
    assert forward.forward.derived == (w.c.id, w.a.id, w.x.id)
    assert [step.concept_id for step in forward.forward.steps] == [w.c.id, w.a.id, w.x.id]
    assert unrelated.id not in {n.concept.id for n in forward.forward.nodes}
    ahead = {n.concept.id: n.state for n in forward.forward.nodes}
    behind = {n.concept.id: n.state for n in backward.methods[0].nodes}
    assert ahead == behind


def test_forward_reasoning_from_e_alone_derives_only_c(repo):
    w = _World(repo).section_201()

    result = reason(repo, ReasoningRequest.forward(inputs=(UserInput("E"),)))

    assert result.forward.derived == (w.c.id,)
    states = {n.concept.canonical_name: n.state for n in result.forward.nodes}
    assert states == {"X": BLOCKED, "A": BLOCKED, "B": MISSING, "C": DERIVED, "D": MISSING, "E": AVAILABLE}


# ------------------------------------------------------- answers, determinism, writes


def test_a_target_nothing_answers_to_is_not_found_and_an_invalid_request_raises(repo):
    _World(repo).section_201()

    assert _run(repo, "Nowhere").status is AnswerStatus.NOT_FOUND
    assert _run(repo, format_id(EntityKind.CONCEPT, 999_999)).status is AnswerStatus.NOT_FOUND
    with pytest.raises(InvalidInputError):
        _run(repo, inputs=(UserInput("Nowhere"),))
    with pytest.raises(InvalidInputError):
        reason(repo, ReasoningRequest.of_target("K-00000001"))


def test_the_output_is_deterministic(repo):
    w = _World(repo).section_201()
    inputs = (UserInput("E"), UserInput("D"), UserInput("B"))

    first = to_json(_run(repo, inputs=inputs))
    assert to_json(_run(repo, inputs=inputs)) == first  # byte-identical every time
    reordered = _run(repo, inputs=tuple(reversed(inputs)))
    assert to_json(reordered.methods) == to_json(_run(repo, inputs=inputs).methods)
    assert w.x.id in first


def test_the_path_listing_reports_when_it_reaches_its_limit(repo, monkeypatch):
    w = _World(repo)
    x, a, b, e = w.concepts("XABE")
    w.requires(x, a)
    w.requires(x, b)
    w.requires(a, e)
    w.requires(b, e)
    monkeypatch.setattr(engine_module, "MAX_PATHS", 1)

    result = _run(repo, inputs=(UserInput("E"),))

    (method,) = result.methods
    assert method.paths_complete is False and len(method.paths) == 1
    assert method.state is DERIVED  # states and steps stay complete
    assert any("path listing stopped" in note for note in result.notes)


def test_reasoning_writes_nothing_and_runs_on_a_read_only_connection(repo, db_path):
    w = _World(repo)
    w.section_201()
    w.rows.commit()
    inputs = (UserInput("E"), UserInput("D"), UserInput("B"))

    before = repo.connection.total_changes
    writable = _run(repo, inputs=inputs)
    reason(repo, ReasoningRequest.forward(inputs=inputs))
    assert repo.connection.total_changes == before
    counts = {
        table: repo.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("derivation", "calculation", "query", "relationship")
    }
    assert counts["derivation"] == counts["calculation"] == counts["query"] == 0
    assert counts["relationship"] == 5  # exactly the stored section 201 relationships

    reader = connect(db_path, read_only=True)
    try:
        read_only = reason(Repository(reader), ReasoningRequest.of_target("X", inputs=inputs))
    finally:
        reader.close()
    assert writable.read_only_connection is False and read_only.read_only_connection is True
    assert to_json(read_only.methods) == to_json(writable.methods)
