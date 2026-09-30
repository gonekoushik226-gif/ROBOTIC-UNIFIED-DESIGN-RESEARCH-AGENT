"""Exact search by identifier (ADR 0036 P9-24).

Accepted kinds (`EXACT_KINDS`): a concept - answered as a concept section, exactly as
concept mode answers a name; a knowledge object - with its evidence, companions,
conflicts, assessment records, superseded pointer and the concepts whose stored
`DEFINED_BY` or `HAS_PROPERTY` edge reaches it (the only ways an object has a
concept, D-21); a relationship - with its label, evidence or recorded bases, and its
endpoints named (a knowledge-object endpoint only under concept mode's rule: allowed
by the lifecycle default and the filters, and with evidence of its own in scope;
otherwise it is withheld and only its stored identifier remains, in the relationship
row); a document - with its declared editions, preserved-file presence, sources in
scope and every extraction run (P9-6).

Identity is exact. Nothing found is an answer, not a failure (section 230); an item
whose evidence is all withheld is *"Insufficient authorized information"* (section 67).
"""

from app.models.entities import Document, KnowledgeObject
from app.models.enums import RelationType
from app.models.identifiers import EntityKind, parse_id
from app.query.concepts import build_section
from app.query.conflicts import conflict_items, knowledge_item
from app.query.pages import document_provenance, document_sources_in_scope
from app.query.provenance import cited_rows, provenance_of
from app.query.results import AnswerStatus, ConceptSection, EdgeEnd, ExactResult, Link
from app.query.scope import QueryContext, counter
from app.query.traversal import label
from app.storage import queries

Answer = tuple[AnswerStatus, str, ExactResult | None, ConceptSection | None]


def exact_result(context: QueryContext, identifier: str) -> Answer:
    """The stored item with this identifier, or why it cannot be shown."""
    kind = parse_id(identifier)[0]
    if kind is EntityKind.CONCEPT:
        return _concept(context, identifier)
    if kind is EntityKind.KNOWLEDGE_OBJECT:
        return _knowledge(context, identifier)
    if kind is EntityKind.RELATIONSHIP:
        return _relationship(context, identifier)
    return _document(context, identifier)


def _absent(identifier: str) -> Answer:
    return AnswerStatus.NOT_FOUND, f"Nothing is stored with id {identifier}; nothing was guessed.", None, None


def _withheld(context: QueryContext, identifier: str) -> Answer:
    status, message = context.withheld_answer((identifier,), f"{identifier} is stored")
    return status, message, None, None


def _concept(context: QueryContext, identifier: str) -> Answer:
    concept = context.concept(identifier)
    if concept is None:
        return _absent(identifier)
    section = build_section(context, ((concept, None),))
    if section is None:
        return _withheld(context, identifier)
    return AnswerStatus.FOUND, f"Concept {identifier} ({concept.canonical_name}).", None, section


def _knowledge(context: QueryContext, identifier: str) -> Answer:
    knowledge = context.knowledge(identifier)
    if knowledge is None:
        return _absent(identifier)
    if not context.knowledge_allowed(knowledge):
        context.filter_out(identifier)
        return _withheld(context, identifier)
    evidence = context.judge_item(identifier, context.evidence(identifier))
    if not evidence:
        return _withheld(context, identifier)
    edges = [
        queries.active_relationship_to_knowledge(
            context.connection,
            relation_type=RelationType.DEFINED_BY,
            from_concept_id=concept_id,
            to_knowledge_id=identifier,
        )
        for concept_id in queries.concepts_defined_by(context.connection, identifier)
    ]
    edges.extend(queries.has_property_edges_to(context.connection, identifier))
    links = []
    for edge in edges:
        if edge is None or edge.from_concept_id is None or not context.edge_allowed(edge):
            continue
        linked = label(context, edge)
        if linked is not None:
            links.append(Link(concept_id=edge.from_concept_id, concept_end=EdgeEnd.FROM, edge=linked))
    links.sort(key=lambda l: (counter(l.concept_id), counter(l.edge.relationship.id)))
    item = knowledge_item(context, knowledge, tuple(links), evidence)
    context.listed(identifier)
    conflicts = conflict_items(context, (identifier,))
    result = ExactResult(
        identifier=identifier,
        kind=EntityKind.KNOWLEDGE_OBJECT.name,
        knowledge=item,
        conflicts=conflicts,
        provenance=provenance_of(context, cited_rows((item, conflicts))),
    )
    return AnswerStatus.FOUND, f"Knowledge object {identifier} ({knowledge.knowledge_type.value}).", result, None


def _relationship(context: QueryContext, identifier: str) -> Answer:
    edge = context.relationship(identifier)
    if edge is None:
        return _absent(identifier)
    if not context.edge_allowed(edge):
        context.filter_out(identifier)
        return _withheld(context, identifier)
    linked = label(context, edge)
    if linked is None:
        return _withheld(context, identifier)
    context.listed(identifier)
    endpoints, withheld = [], []
    for concept_id, knowledge_id in (
        (edge.from_concept_id, edge.from_knowledge_id),
        (edge.to_concept_id, edge.to_knowledge_id),
    ):
        if concept_id is not None:
            concept = context.concept(concept_id)
            if concept is not None:
                endpoints.append(concept)  # named with the edge (P9-14)
        elif knowledge_id is not None:
            knowledge = _endpoint_knowledge(context, knowledge_id)
            if knowledge is None:
                withheld.append(knowledge_id)
            else:
                endpoints.append(knowledge)
    result = ExactResult(
        identifier=identifier,
        kind=EntityKind.RELATIONSHIP.name,
        relationship=linked,
        endpoints=tuple(endpoints),
        provenance=provenance_of(context, cited_rows(linked)),
    )
    message = f"Relationship {identifier} ({edge.relation_type.value}, {edge.origin.value})."
    for knowledge_id in withheld:
        why = (
            "excluded by the request's filters or the lifecycle default"
            if context.reason(knowledge_id) == "filtered"
            else "it has no evidence from a source that is authorised and in the requested scope"
        )
        message += f" Its endpoint {knowledge_id} is withheld: {why}; its content is not shown."
    return AnswerStatus.FOUND, message, result, None


def _endpoint_knowledge(context: QueryContext, knowledge_id: str) -> KnowledgeObject | None:
    """A knowledge-object endpoint, under concept mode's rule for an object reached
    through an edge: the lifecycle default and the request's filters, then evidence of
    its own in scope (ADR 0035 P9-5; ADR 0036 P9-23). None when it is withheld."""
    knowledge = context.knowledge(knowledge_id)
    if knowledge is None:  # pragma: no cover - endpoints are foreign keys
        return None
    if not context.knowledge_allowed(knowledge):
        context.filter_out(knowledge_id)
        return None
    if not context.judge_item(knowledge_id, context.evidence(knowledge_id)):
        return None
    context.listed(knowledge_id)
    return knowledge


def _document(context: QueryContext, identifier: str) -> Answer:
    document = context.get(Document, identifier)
    if document is None:
        return _absent(identifier)
    if context.filters.document_ids and identifier not in context.filters.document_ids:
        context.filter_out(identifier)
        return _withheld(context, identifier)
    sources = document_sources_in_scope(context, document)
    if not sources:
        context.withhold(identifier, "not authorized")
        return _withheld(context, identifier)
    context.listed(identifier)
    f = context.filters
    runs = tuple(
        run
        for run in queries.runs_for_document(context.connection, identifier)
        if (not f.run_ids or run.id in f.run_ids)
        and (not f.run_statuses or run.status in f.run_statuses)
        and (not f.extractor_versions or run.extractor_version in f.extractor_versions)
    )
    result = ExactResult(
        identifier=identifier,
        kind=EntityKind.DOCUMENT.name,
        document=document_provenance(context, document),
        runs=runs,
        sources=sources,
    )
    return AnswerStatus.FOUND, f"Document {identifier} ({document.filename}).", result, None
