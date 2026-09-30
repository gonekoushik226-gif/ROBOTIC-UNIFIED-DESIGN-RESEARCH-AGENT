"""Stored rows for the Phase 9 query tests (ADRs 0035-0037).

One small builder, `QueryRows`, stores each row through the validating repository the
way the pipelines would, so the schema's own constraints hold throughout; and one
shared library, `mosfet_library`, used by the query-engine tests. All text is original
(no copyrighted material). Every database is a temporary one (`tests/conftest.py`).
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from app.models import (
    Authorization,
    CertaintyState,
    Concept,
    ConceptAlias,
    ConceptEquivalence,
    ConceptEquivalenceBasis,
    ConceptEquivalenceStatus,
    ConceptOccurrence,
    Conflict,
    ConflictCause,
    Document,
    DocumentProcessingStatus,
    DocumentSegment,
    ExtractionRun,
    ExtractionRunStatus,
    ExtractionTrigger,
    KnowledgeEquivalence,
    KnowledgeEquivalenceOutcome,
    KnowledgeObject,
    KnowledgeType,
    LifecycleStatus,
    Relationship,
    RelationshipInference,
    RelationshipOccurrence,
    RelationshipOrigin,
    RelationType,
    Source,
    SourceAvailability,
    SourceCategory,
    SourceOccurrence,
    TextOrigin,
    normalize_alias,
)
from app.models.base import utc_now

STATED = ConceptEquivalenceBasis.STATED_EQUIVALENT_TO


class QueryRows:
    """Stores the rows one test needs, through the validating repository."""

    def __init__(self, repo) -> None:
        self.repo = repo
        self.now = utc_now()

    def _add(self, entity_type: type, identifier: str | None = None, **fields):
        return self.repo.add(
            entity_type(
                id=identifier or self.repo.new_id(entity_type),
                created_at=self.now,
                updated_at=self.now,
                **fields,
            )
        )

    def commit(self) -> None:
        self.repo.connection.commit()

    def document(self, name: str, *, file_path: str | None = None) -> Document:
        return self._add(
            Document,
            filename=f"{name}.pdf",
            original_filename=f"{name}.pdf",
            source_type="PDF",
            file_path=file_path or f"/documents/{name}.pdf",
            file_hash=f"hash-{name}",
            file_size=1024,
            mime_type="application/pdf",
            ingested_at=self.now,
            processing_status=DocumentProcessingStatus.PROCESSED,
            processing_version=1,
            document_title=name,
        )

    def source(
        self,
        document: Document,
        identifier: str | None = None,
        *,
        category: SourceCategory = SourceCategory.USER_PROVIDED_SOURCE,
        authorization: Authorization = Authorization.AUTHORIZED,
    ) -> Source:
        return self._add(
            Source,
            identifier,
            name=f"{document.document_title} (source)",
            source_category=category,
            authorization=authorization,
            availability=SourceAvailability.AVAILABLE,
            document_id=document.id,
            file_hash=document.file_hash,
        )

    def segment(
        self, document: Document, page: int, ordinal: int = 0, *, text: str | None = None
    ) -> DocumentSegment:
        return self._add(
            DocumentSegment,
            document_id=document.id,
            page_number=page,
            ordinal=ordinal,
            text=f"Stored text of page {page}, part {ordinal}." if text is None else text,
            extraction_method="native",
            text_origin=TextOrigin.NATIVE_TEXT,
        )

    def run(
        self,
        document: Document,
        number: int = 1,
        *,
        version: str = "4",
        status: ExtractionRunStatus = ExtractionRunStatus.COMPLETED,
    ) -> ExtractionRun:
        return self._add(
            ExtractionRun,
            document_id=document.id,
            run_number=number,
            trigger=(
                ExtractionTrigger.FIRST_EXTRACTION if number == 1
                else ExtractionTrigger.USER_REQUESTED
            ),
            extractor_version=version,
            parser_name="pypdf",
            started_at=self.now,
            status=status,
            completed_at=self.now,
        )

    def concept(self, name: str) -> Concept:
        """A concept and its one ACTIVE alias, as extraction stores it."""
        concept = self._add(Concept, canonical_name=name, lifecycle_status=LifecycleStatus.ACTIVE)
        self._add(
            ConceptAlias,
            concept_id=concept.id,
            alias=name,
            normalized_alias=normalize_alias(name),
            lifecycle_status=LifecycleStatus.ACTIVE,
        )
        return concept

    def knowledge(
        self,
        knowledge_type: KnowledgeType,
        statement: str,
        *,
        status: LifecycleStatus = LifecycleStatus.ACTIVE,
    ) -> KnowledgeObject:
        return self._add(
            KnowledgeObject,
            knowledge_type=knowledge_type,
            canonical_name=statement.rstrip("."),
            statement=statement,
            lifecycle_status=status,
            certainty=CertaintyState.REPORTED_BY_SOURCE,
            knowledge_version=1,
        )

    def edge(
        self,
        relation_type: RelationType,
        identifier: str | None = None,
        *,
        status: LifecycleStatus = LifecycleStatus.ACTIVE,
        origin: RelationshipOrigin = RelationshipOrigin.EXPLICIT,
        **ends: str,
    ) -> Relationship:
        return self._add(
            Relationship,
            identifier,
            relation_type=relation_type,
            origin=origin,
            lifecycle_status=status,
            **ends,
        )

    def equivalence(
        self,
        one: Concept,
        other: Concept,
        identifier: str | None = None,
        *,
        status: ConceptEquivalenceStatus = ConceptEquivalenceStatus.POSSIBLE_EQUIVALENT,
        basis: ConceptEquivalenceBasis = ConceptEquivalenceBasis.SHARED_NAME_OTHER_DOCUMENT,
        relationship: Relationship | None = None,
        run: ExtractionRun | None = None,
    ) -> ConceptEquivalence:
        first, second = sorted((one.id, other.id))
        return self._add(
            ConceptEquivalence,
            identifier,
            concept_a_id=first,
            concept_b_id=second,
            status=status,
            basis=basis,
            rule="P8-16",
            rule_version="1",
            shared_name=None if basis is STATED else "shared name",
            relationship_id=None if relationship is None else relationship.id,
            extraction_run_id=None if run is None else run.id,
        )

    def conflict(self, claim_a: KnowledgeObject, claim_b: KnowledgeObject) -> Conflict:
        return self._add(
            Conflict,
            claim_a_id=claim_a.id,
            claim_b_id=claim_b.id,
            cause=ConflictCause.UNDETERMINED,
            lifecycle_status=LifecycleStatus.ACTIVE,
        )

    def supersede(self, canonical: KnowledgeObject, duplicate: KnowledgeObject) -> KnowledgeEquivalence:
        """What `merge` stores (ADR 0033 P8-19): the duplicate SUPERSEDED, and its pointer."""
        self.repo.update(replace(duplicate, lifecycle_status=LifecycleStatus.SUPERSEDED))
        return self._add(
            KnowledgeEquivalence,
            canonical_knowledge_id=canonical.id,
            outcome=KnowledgeEquivalenceOutcome.EXACT_DUPLICATE,
            rule="P8-19",
            rule_version="1",
            other_knowledge_id=duplicate.id,
        )

    def inference(
        self, relationship: Relationship, occurrence: SourceOccurrence, *, rule: str = "R1"
    ) -> RelationshipInference:
        return self._add(
            RelationshipInference,
            relationship_id=relationship.id,
            rule=rule,
            rule_version="1",
            basis_occurrence_id=occurrence.id,
            matched_text="mosfet",
        )

    def _located(self, source, page, segment, span, run) -> dict:
        start, end = span if span is not None else (None, None)
        return {
            "source_id": source.id,
            "document_id": source.document_id,
            "extraction_method": "manual",
            "extraction_timestamp": self.now,
            "segment_id": None if segment is None else segment.id,
            "page_number": page,
            "char_start": start,
            "char_end": end,
            "extraction_run_id": None if run is None else run.id,
        }

    def knowledge_occurrence(
        self, knowledge, source, *, page=None, segment=None, span=None, run=None, text=None
    ) -> SourceOccurrence:
        return self._add(
            SourceOccurrence,
            knowledge_id=knowledge.id,
            original_text=knowledge.statement if text is None else text,
            **self._located(source, page, segment, span, run),
        )

    def concept_occurrence(
        self, concept, source, *, page=None, segment=None, span=None, run=None
    ) -> ConceptOccurrence:
        return self._add(
            ConceptOccurrence,
            concept_id=concept.id,
            surface_form=concept.canonical_name,
            **self._located(source, page, segment, span, run),
        )

    def relationship_occurrence(
        self, relationship, source, *, page=None, segment=None, span=None, run=None
    ) -> RelationshipOccurrence:
        return self._add(
            RelationshipOccurrence,
            relationship_id=relationship.id,
            original_text="A MOSFET is used in an amplifier.",
            **self._located(source, page, segment, span, run),
        )


def mosfet_library(rows: QueryRows) -> SimpleNamespace:
    """Three books about MOSFETs, stored as Phases 4-8 store them.

    Book A (authorised, run v4 COMPLETED, page 1): MOSFET with a definition, an owned
    property, an application, a use in the other direction, a prerequisite, a parent
    and grandparent, an unlinked equation, and a BJT distractor whose definition
    mentions MOSFET (a rule-R1 edge). Book B (authorised, run v3 PARTIAL, page 3):
    another MOSFET concept linked to the same definition object, a conflicting
    property, and "MOS transistor" stated EQUIVALENT_TO MOSFET. Book C (NOT_AUTHORIZED,
    page 1): a third MOSFET with its own definition, which conflicts with book A's.
    """
    n = SimpleNamespace()
    n.book_a, n.book_b, n.book_c = (rows.document(name) for name in ("book-a", "book-b", "book-c"))
    n.src_a, n.src_b = rows.source(n.book_a), rows.source(n.book_b)
    n.src_c = rows.source(n.book_c, authorization=Authorization.NOT_AUTHORIZED)
    n.run_a = rows.run(n.book_a)
    n.run_b = rows.run(n.book_b, version="3", status=ExtractionRunStatus.PARTIAL)
    n.run_c = rows.run(n.book_c)
    n.seg_a, n.seg_b, n.seg_c = rows.segment(n.book_a, 1), rows.segment(n.book_b, 3), rows.segment(n.book_c, 1)

    def in_a(span):
        return {"page": 1, "segment": n.seg_a, "span": span, "run": n.run_a}

    def in_b(span):
        return {"page": 3, "segment": n.seg_b, "span": span, "run": n.run_b}

    def in_c(span):
        return {"page": 1, "segment": n.seg_c, "span": span, "run": n.run_c}

    def stated(edge, source, where):
        rows.relationship_occurrence(edge, source, **where)
        return edge

    # ---- book A
    names = ("MOSFET", "Amplifier", "Gate oxide", "Transistor", "Field-effect transistor",
             "Semiconductor device", "BJT")
    (n.mosfet_a, n.amplifier, n.gate_oxide, n.transistor, n.fet, n.semiconductor,
     n.bjt) = (rows.concept(name) for name in names)
    for offset, concept in enumerate((n.mosfet_a, n.amplifier, n.gate_oxide, n.transistor,
                                      n.fet, n.semiconductor, n.bjt)):
        rows.concept_occurrence(concept, n.src_a, **in_a((offset, offset + 1)))
    n.definition = rows.knowledge(KnowledgeType.DEFINITION, "A MOSFET is a voltage-controlled transistor.")
    n.def_a = rows.knowledge_occurrence(n.definition, n.src_a, **in_a((10, 54)))
    n.defined_a = stated(rows.edge(RelationType.DEFINED_BY, from_concept_id=n.mosfet_a.id,
                                   to_knowledge_id=n.definition.id), n.src_a, in_a((10, 54)))
    n.property = rows.knowledge(
        KnowledgeType.PROPERTY, "The characteristic of a MOSFET is its high input impedance.")
    n.prop_a = rows.knowledge_occurrence(n.property, n.src_a, **in_a((60, 120)))
    n.owns = stated(rows.edge(RelationType.HAS_PROPERTY, from_concept_id=n.mosfet_a.id,
                              to_knowledge_id=n.property.id), n.src_a, in_a((60, 120)))
    n.uses = stated(rows.edge(RelationType.USES, from_concept_id=n.mosfet_a.id,
                              to_concept_id=n.amplifier.id), n.src_a, in_a((130, 160)))
    n.used_in = stated(rows.edge(RelationType.USES, from_concept_id=n.gate_oxide.id,
                                 to_concept_id=n.mosfet_a.id), n.src_a, in_a((170, 200)))
    n.prereq = stated(rows.edge(RelationType.PREREQUISITE_OF, from_concept_id=n.transistor.id,
                                to_concept_id=n.mosfet_a.id), n.src_a, in_a((210, 240)))
    n.parent = stated(rows.edge(RelationType.PARENT_OF, from_concept_id=n.fet.id,
                                to_concept_id=n.mosfet_a.id), n.src_a, in_a((250, 280)))
    n.grandparent = stated(rows.edge(RelationType.PARENT_OF, from_concept_id=n.semiconductor.id,
                                     to_concept_id=n.fet.id), n.src_a, in_a((290, 320)))
    n.equation = rows.knowledge(KnowledgeType.EQUATION, "I_D = k (V_GS - V_T)^2")
    n.eq_a = rows.knowledge_occurrence(n.equation, n.src_a, **in_a((330, 352)))
    n.bjt_definition = rows.knowledge(
        KnowledgeType.DEFINITION, "Unlike a MOSFET, a BJT is a current-controlled transistor.")
    n.bjt_def_a = rows.knowledge_occurrence(n.bjt_definition, n.src_a, **in_a((360, 420)))
    stated(rows.edge(RelationType.DEFINED_BY, from_concept_id=n.bjt.id,
                     to_knowledge_id=n.bjt_definition.id), n.src_a, in_a((360, 420)))
    n.related = rows.edge(RelationType.RELATED_TO, origin=RelationshipOrigin.INFERRED,
                          from_concept_id=n.mosfet_a.id, to_concept_id=n.bjt.id)
    n.basis = rows.inference(n.related, n.bjt_def_a)

    # ---- book B
    n.mosfet_b, n.mos = rows.concept("MOSFET"), rows.concept("MOS transistor")
    rows.concept_occurrence(n.mosfet_b, n.src_b, **in_b((0, 6)))
    rows.concept_occurrence(n.mos, n.src_b, **in_b((7, 21)))
    n.def_b = rows.knowledge_occurrence(n.definition, n.src_b, **in_b((30, 74)))
    n.defined_b = stated(rows.edge(RelationType.DEFINED_BY, from_concept_id=n.mosfet_b.id,
                                   to_knowledge_id=n.definition.id), n.src_b, in_b((30, 74)))
    n.rival = rows.knowledge(
        KnowledgeType.PROPERTY, "The characteristic of a MOSFET is its low input impedance.")
    rows.knowledge_occurrence(n.rival, n.src_b, **in_b((80, 140)))
    n.owns_rival = stated(rows.edge(RelationType.HAS_PROPERTY, from_concept_id=n.mosfet_b.id,
                                    to_knowledge_id=n.rival.id), n.src_b, in_b((80, 140)))
    n.conflict = rows.conflict(n.property, n.rival)
    n.mos_definition = rows.knowledge(
        KnowledgeType.DEFINITION, "A MOS transistor is a metal-oxide-semiconductor transistor.")
    rows.knowledge_occurrence(n.mos_definition, n.src_b, **in_b((150, 210)))
    stated(rows.edge(RelationType.DEFINED_BY, from_concept_id=n.mos.id,
                     to_knowledge_id=n.mos_definition.id), n.src_b, in_b((150, 210)))
    n.same_as = stated(rows.edge(RelationType.EQUIVALENT_TO, from_concept_id=n.mos.id,
                                 to_concept_id=n.mosfet_b.id), n.src_b, in_b((220, 260)))

    # ---- book C: not authorised
    n.mosfet_c = rows.concept("MOSFET")
    rows.concept_occurrence(n.mosfet_c, n.src_c, **in_c((0, 6)))
    n.hidden = rows.knowledge(KnowledgeType.DEFINITION, "A MOSFET is a kind of vacuum tube.")
    rows.knowledge_occurrence(n.hidden, n.src_c, **in_c((10, 44)))
    stated(rows.edge(RelationType.DEFINED_BY, from_concept_id=n.mosfet_c.id,
                     to_knowledge_id=n.hidden.id), n.src_c, in_c((10, 44)))
    n.hidden_conflict = rows.conflict(n.definition, n.hidden)
    rows.commit()
    return n
