"""The structured diagram specification, from stored knowledge (ADR 0051 P19-2 ... P19-5).

Section 144's first half: identify the subject, retrieve authorized knowledge, identify
parameters, validate, construct the specification. The specification is the diagram's
data (section 145): the SVG is drawn from it alone.

    nodes       the subject; what it is composed of, followed down; what it is part of;
                its other stated relations, one hop - each a stored concept
    edges       each a stored EXPLICIT relationship with its evidence in scope; an INFERRED
                edge is not drawn (it is noted)
    parameters  known: from the request (REQUEST) - never guessed from text
    unknowns    what the diagram would need and the knowledge does not state: a
                composition's count, how a request parameter maps onto the structure
"""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.errors import InvalidInputError
from app.models.enums import KnowledgeType, RelationType, RelationshipOrigin
from app.nlu import InterpretationStatus, interpret
from app.query import AnswerStatus, QueryEngine, QueryRequest, SourceScope

#: How far a composition is followed down.
MAX_DEPTH = 3
#: Relation types drawn as structure (composition), and as "part of".
_COMPOSITION = RelationType.COMPOSED_OF
_PART = RelationType.PART_OF
#: A request parameter: "8-bit", "4 stage", "16-input".
_PARAMETER = re.compile(r"\b(?P<value>\d{1,4})[- ](?P<unit>bits?|stages?|inputs?|outputs?|channels?|ports?|ways?|lanes?)\b",
                        re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)
_NOT_DRAWN = re.compile(r"\b(?:schematic|circuit diagram|wiring|chart|graph|plot|picture|photo|image)\b",
                        re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Node:
    concept_id: str
    name: str
    #: The subject is level 0; composition parts below (1, 2, ...); wholes above (-1);
    #: other relations beside (level 0, `side` True).
    level: int
    side: bool = False
    definition_id: str | None = None
    definition: str | None = None


@dataclass(frozen=True, slots=True)
class Edge:
    relationship_id: str
    relation_type: str
    from_id: str
    to_id: str
    label: str
    evidence: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Parameter:
    text: str
    value: str
    unit: str
    origin: str
    note: str


@dataclass(frozen=True, slots=True)
class Specification:
    request: str
    subject: str
    scope: str
    title: str
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    parameters: tuple[Parameter, ...]
    unknowns: tuple[str, ...]
    notes: tuple[str, ...]
    #: Every stored identifier used: concepts, knowledge, relationships, evidence.
    identifiers: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False, indent=2)


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why no diagram is drawn: insufficient information, ambiguity, or not drawable."""

    status: str
    message: str
    candidates: tuple[str, ...] = ()


def refuse(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="diagrams", data_changed=False, retry_safe=True,
        next_options=('python -m app diagram "Draw a diagram of an 8-bit ripple carry adder"',
                      'python -m app diagram --name "ripple carry adder"'),
    )


def read_request(text: str) -> tuple[str, tuple[Parameter, ...], bool]:
    """The subject, the request's parameters, and whether the request asks for something
    RUDRA does not draw (a schematic, a chart, a picture)."""
    interpretation = interpret(text)
    intents = interpretation.intents
    if (interpretation.status is not InterpretationStatus.INTERPRETED or len(intents) != 1
            or intents[0].intent_type != "IMAGE_REQUEST" or not intents[0].target):
        raise refuse(f"{text!r} is not a diagram request.", 'Ask e.g. "Draw a diagram of the ripple carry adder".')
    return _subject_and_parameters(intents[0].target, text)


def _subject_and_parameters(target: str, request: str) -> tuple[str, tuple[Parameter, ...], bool]:
    parameters = tuple(
        Parameter(m.group(0), m["value"], m["unit"].casefold().rstrip("s"), "REQUEST",
                  "not applied - the knowledge used does not state how it maps onto the structure")
        for m in _PARAMETER.finditer(target))
    subject = _PARAMETER.sub(" ", target)
    subject = " ".join(_ARTICLE.sub("", " ".join(subject.split())).split()).strip(" .")
    subject = _ARTICLE.sub("", subject)
    return subject, parameters, bool(_NOT_DRAWN.search(request))


class SpecificationBuilder:
    def __init__(self, connection, *, database_path: str | Path) -> None:
        self.engine = QueryEngine(connection, database_path=database_path)

    def build(self, request: str, subject: str, parameters: tuple[Parameter, ...],
              scope: SourceScope = SourceScope.MY_BOOKS) -> Specification | Refusal:
        first = self.engine.run(QueryRequest.concept(subject, scope=scope))
        if first.status is not AnswerStatus.FOUND or first.concept is None:
            return Refusal("INSUFFICIENT_AUTHORIZED_INFORMATION",
                           f"Insufficient authorized information: no stored concept {subject!r} in scope "
                           f"{scope.value} - nothing is drawn.")
        resolved = first.concept.concepts
        if len(resolved) > 1:
            return Refusal("AMBIGUOUS", f"{len(resolved)} stored concepts answer to {subject!r}; none was chosen.",
                           tuple(f"{c.concept.id} {c.concept.canonical_name}" for c in resolved))
        root = resolved[0].concept
        nodes: dict[str, Node] = {}
        edges: dict[str, Edge] = {}
        notes: list[str] = []
        nodes[root.id] = self._node(first, root, 0)
        self._collect(first, root.id, 0, nodes, edges, notes, subject_level=True)
        frontier = [(cid, 1) for cid in list(nodes) if nodes[cid].level == 1]
        while frontier:
            concept_id, level = frontier.pop(0)
            if level >= MAX_DEPTH:
                continue
            result = self.engine.run(QueryRequest.concept(nodes[concept_id].name, scope=scope))
            before = set(nodes)
            self._collect(result, concept_id, level, nodes, edges, notes, subject_level=False)
            frontier.extend((cid, level + 1) for cid in nodes if cid not in before and nodes[cid].level == level + 1)
        if not edges:
            return Refusal("INSUFFICIENT_AUTHORIZED_INFORMATION",
                           f"Insufficient authorized information: {root.canonical_name} ({root.id}) has no stored "
                           "relationship in scope - a single box is not a diagram, and none is invented.")
        unknowns = [f"how many {nodes[e.to_id].name} a {nodes[e.from_id].name} has - the knowledge used does not "
                    "state it" for e in edges.values() if e.relation_type == _COMPOSITION.value]
        unknowns += [f"how {p.text} maps onto the structure - the knowledge used does not state it" for p in parameters]
        identifiers = {root.id}
        for node in nodes.values():
            identifiers.add(node.concept_id)
            if node.definition_id:
                identifiers.add(node.definition_id)
        for edge in edges.values():
            identifiers.add(edge.relationship_id)
            identifiers.update(evidence_id for evidence_id, _ in edge.evidence)
        ordered_nodes = tuple(sorted(nodes.values(), key=lambda n: (n.level, n.side, _counter(n.concept_id))))
        ordered_edges = tuple(sorted(edges.values(), key=lambda e: _counter(e.relationship_id)))
        return Specification(
            request=request, subject=root.canonical_name, scope=scope.value,
            title=f"{root.canonical_name} - structure from stored knowledge",
            nodes=ordered_nodes, edges=ordered_edges, parameters=parameters, unknowns=tuple(unknowns),
            notes=tuple(dict.fromkeys(notes)), identifiers=tuple(sorted(identifiers, key=_sort_key)),
        )

    # ------------------------------------------------------------ the parts

    @staticmethod
    def _node(result, concept, level: int, side: bool = False) -> Node:
        definition = None
        if result is not None and result.concept is not None:
            for group in result.concept.groups:
                for item in group.items:
                    knowledge = getattr(item, "knowledge", None)
                    if knowledge is not None and knowledge.knowledge_type is KnowledgeType.DEFINITION:
                        definition = definition or knowledge
        return Node(concept.id, concept.canonical_name, level, side,
                    None if definition is None else definition.id, None if definition is None else definition.statement)

    def _collect(self, result, concept_id: str, level: int, nodes, edges, notes, *, subject_level: bool) -> None:
        if result.concept is None:
            return
        if not subject_level and concept_id in nodes and nodes[concept_id].definition_id is None:
            resolved = next((c.concept for c in result.concept.concepts if c.concept.id == concept_id), None)
            if resolved is not None:
                nodes[concept_id] = self._node(result, resolved, nodes[concept_id].level, nodes[concept_id].side)
        for group in result.concept.groups:
            for item in group.items:
                links = getattr(item, "links", ())
                if not hasattr(item, "neighbours") or not any(link.concept_id == concept_id for link in links):
                    continue  # a knowledge item, or an edge of another concept of the same name
                edge = item.edge
                relationship = edge.relationship
                if relationship.from_concept_id is None or relationship.to_concept_id is None:
                    continue
                if relationship.origin is not RelationshipOrigin.EXPLICIT or not edge.evidence:
                    notes.append("inferred or unevidenced edges are not drawn")
                    continue
                kind = relationship.relation_type
                outward = relationship.from_concept_id == concept_id
                other_id = relationship.to_concept_id if outward else relationship.from_concept_id
                other = next((n for n in item.neighbours if n.id == other_id), None)
                if other is None:
                    continue
                if kind is _COMPOSITION and outward:
                    placement = (level + 1, False)
                elif kind is _PART and not outward:
                    placement = (level + 1, False)
                elif (kind is _COMPOSITION and not outward) or (kind is _PART and outward):
                    if not subject_level:
                        continue  # only the subject's wholes are drawn
                    placement = (-1, False)
                else:
                    if not subject_level:
                        continue  # other relations: one hop from the subject only
                    placement = (0, True)
                if other_id not in nodes:
                    nodes[other_id] = Node(other_id, other.canonical_name, placement[0], placement[1])
                label = kind.value + (" (count not stated)" if kind in (_COMPOSITION, _PART) else "")
                edges[relationship.id] = Edge(
                    relationship.id, kind.value, relationship.from_concept_id, relationship.to_concept_id, label,
                    tuple((row.id, row.evidence_text) for row in edge.evidence))


def _counter(identifier: str) -> int:
    return int(identifier.split("-", 1)[1])


def _sort_key(identifier: str) -> tuple[str, int]:
    prefix, _, number = identifier.partition("-")
    return prefix, int(number)
