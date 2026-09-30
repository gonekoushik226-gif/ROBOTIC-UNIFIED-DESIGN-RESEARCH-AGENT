"""Stored rows for the Phase 10 command and acceptance tests (ADRs 0038-0040).

`ReasoningRows` stores concepts, dependency relationships and knowledge objects through
`QueryRows` - the validating repository, unchanged - each with evidence in one
authorised book, as extraction stores them, so every item is in the "my books" scope
unless a test gives it another source. This is the structured-fixture path of U12(c):
section 201's graph written directly, with no documents. All text is original.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.models import KnowledgeType, LifecycleStatus, RelationType
from tests.unit.query_rows import QueryRows


class ReasoningRows:
    """Concepts, relationships and knowledge objects with evidence in scope."""

    def __init__(self, repo) -> None:
        self.rows = QueryRows(repo)
        self.book = self.rows.document("reasoning-book")
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

    def edge(self, kind: RelationType, source=None, *, status=LifecycleStatus.ACTIVE, **ends):
        edge = self.rows.edge(kind, status=status, **ends)
        where_source, where = self._where(source)
        self.rows.relationship_occurrence(edge, where_source, **where)
        return edge

    def requires(self, x, a, kind: RelationType = RelationType.REQUIRES, source=None):
        return self.edge(kind, source, from_concept_id=x.id, to_concept_id=a.id)

    def knowledge(self, statement: str, source=None, *, kind=KnowledgeType.DEFINITION,
                  status=LifecycleStatus.ACTIVE):
        item = self.rows.knowledge(kind, statement, status=status)
        where_source, where = self._where(source)
        self.rows.knowledge_occurrence(item, where_source, **where)
        return item

    def link(self, concept, knowledge, kind: RelationType = RelationType.DEFINED_BY):
        return self.edge(kind, from_concept_id=concept.id, to_knowledge_id=knowledge.id)

    def other_source(self, name: str, **options):
        return self.rows.source(self.rows.document(name), **options)

    def commit(self) -> None:
        self.rows.commit()


def section_201(w: ReasoningRows) -> SimpleNamespace:
    """X requires A and B; A requires C and D (a DEPENDS_ON); C requires E."""
    g = SimpleNamespace()
    g.x, g.a, g.b, g.c, g.d, g.e = (w.concept(name) for name in "XABCDE")
    g.x_a = w.requires(g.x, g.a)
    g.x_b = w.requires(g.x, g.b)
    g.a_c = w.requires(g.a, g.c)
    g.a_d = w.requires(g.a, g.d, RelationType.DEPENDS_ON)
    g.c_e = w.requires(g.c, g.e)
    return g
