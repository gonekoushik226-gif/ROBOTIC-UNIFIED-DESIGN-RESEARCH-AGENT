"""A valid instance of every persisted entity.

Used to prove that the Part 5 section 185 criteria hold for every entity rather
than for a convenient few. Foreign keys are satisfied in dependency order, so this
doubles as a check that the schema's references are coherent.

Covers the 22 entities of Part 5 section 184, the three added by Phase 3
(ADR 0008, ADR 0010), the one added by Phase 4 (ADR 0015), the two
added by Phase 5 (ADR 0018, ADR 0022), the one added by Phase 6 (ADR 0027) and
the two added by Phase 8 (ADR 0034).
Callers assert both: that the section 184 set is fully
covered - the guarantee Phase 2 signed off - and that nothing in `ENTITIES` is
missing.
"""

from __future__ import annotations

from app.models import (
    Action,
    AuditEvent,
    Authorization,
    Calculation,
    CertaintyState,
    Concept,
    ConceptAlias,
    ConceptEquivalence,
    ConceptEquivalenceBasis,
    ConceptEquivalenceStatus,
    ConceptOccurrence,
    Conflict,
    ConflictCause,
    Derivation,
    Document,
    DocumentKind,
    DocumentProcessingStatus,
    DocumentSegment,
    DocumentStructure,
    DocumentVersion,
    Equation,
    ExecutionPlan,
    ExtractionIssue,
    ExtractionIssueType,
    ExtractionRun,
    ExtractionRunStatus,
    ExtractionTrigger,
    Intent,
    KnowledgeEquivalence,
    KnowledgeEquivalenceOutcome,
    KnowledgeObject,
    KnowledgeType,
    LifecycleStatus,
    MemoryCategory,
    MemoryItem,
    Procedure,
    ProcedureDocumentationStatus,
    Query,
    Relationship,
    RelationshipInference,
    RelationshipOccurrence,
    RelationshipOrigin,
    RelationType,
    RiskLevel,
    Rule,
    Source,
    SourceAvailability,
    SourceCategory,
    SourceOccurrence,
    StructureOrigin,
    TaskClass,
    TextOrigin,
    Variable,
    Verification,
    VerificationStatus,
    normalize_alias,
)
from app.models.base import utc_now


def build_all(repo) -> list[object]:
    """Create and store one of every entity. Returns them in creation order."""
    now = utc_now()
    made: list[object] = []

    def add(entity):
        made.append(repo.add(entity))
        return entity

    document = add(
        Document(
            id=repo.new_id(Document),
            created_at=now,
            updated_at=now,
            filename="sample.pdf",
            original_filename="Sample.pdf",
            source_type="PDF",
            file_path="/documents/sample.pdf",
            file_hash="hash-of-sample",
            file_size=2048,
            mime_type="application/pdf",
            ingested_at=now,
            processing_status=DocumentProcessingStatus.PENDING,
            processing_version=1,
        )
    )
    version = add(
        DocumentVersion(
            id=repo.new_id(DocumentVersion),
            created_at=now,
            updated_at=now,
            document_id=document.id,
            version_label="2nd edition",
            file_hash="hash-of-sample",
            ingested_at=now,
        )
    )
    segment = add(
        DocumentSegment(
            id=repo.new_id(DocumentSegment),
            created_at=now,
            updated_at=now,
            document_id=document.id,
            page_number=214,
            ordinal=0,
            text="A flip-flop is a bistable circuit.",
            extraction_method="native",
            text_origin=TextOrigin.NATIVE_TEXT,
            document_version_id=version.id,
            confidence=None,
        )
    )
    # Phase 4 (ADR 0015). A chapter with a section beneath it, so the
    # self-referencing parent link is exercised by the shared sample too.
    chapter = add(
        DocumentStructure(
            id=repo.new_id(DocumentStructure),
            created_at=now,
            updated_at=now,
            document_id=document.id,
            kind=DocumentKind.CHAPTER,
            ordinal=0,
            origin=StructureOrigin.EXPLICIT_STRUCTURE,
            label="5",
            title="Sequential logic",
            page_start=210,
            page_end=260,
        )
    )
    add(
        DocumentStructure(
            id=repo.new_id(DocumentStructure),
            created_at=now,
            updated_at=now,
            document_id=document.id,
            kind=DocumentKind.SECTION,
            ordinal=1,
            origin=StructureOrigin.EXPLICIT_STRUCTURE,
            parent_id=chapter.id,
            label="5.3",
            title="Flip-flops",
            page_start=214,
            page_end=222,
        )
    )
    source = add(
        Source(
            id=repo.new_id(Source),
            created_at=now,
            updated_at=now,
            name="Sample textbook",
            source_category=SourceCategory.USER_PROVIDED_SOURCE,
            authorization=Authorization.AUTHORIZED,
            availability=SourceAvailability.AVAILABLE,
            document_id=document.id,
            file_hash="hash-of-sample",
        )
    )
    knowledge = add(
        KnowledgeObject(
            id=repo.new_id(KnowledgeObject),
            created_at=now,
            updated_at=now,
            knowledge_type=KnowledgeType.DEFINITION,
            canonical_name="Flip-flop",
            statement="A flip-flop is a bistable circuit.",
            lifecycle_status=LifecycleStatus.ACTIVE,
            certainty=CertaintyState.REPORTED_BY_SOURCE,
            knowledge_version=1,
        )
    )
    other_knowledge = add(
        KnowledgeObject(
            id=repo.new_id(KnowledgeObject),
            created_at=now,
            updated_at=now,
            knowledge_type=KnowledgeType.CLAIM,
            canonical_name="Flip-flop (alternative account)",
            statement="A flip-flop is a monostable circuit.",
            lifecycle_status=LifecycleStatus.CONFLICTED,
            certainty=CertaintyState.CONFLICTING,
            knowledge_version=1,
        )
    )
    occurrence = add(
        SourceOccurrence(
            id=repo.new_id(SourceOccurrence),
            created_at=now,
            updated_at=now,
            knowledge_id=knowledge.id,
            source_id=source.id,
            document_id=document.id,
            original_text="A flip-flop is a bistable circuit.",
            extraction_method="manual",
            extraction_timestamp=now,
            document_version_id=version.id,
            segment_id=segment.id,
            page_number=214,
            section="5.3",
        )
    )
    concept = add(
        Concept(
            id=repo.new_id(Concept),
            created_at=now,
            updated_at=now,
            canonical_name="Sequential logic",
            lifecycle_status=LifecycleStatus.ACTIVE,
        )
    )
    # Endpoints are typed foreign keys (decision D-22, ADR 0009). This is the
    # KnowledgeObject -> Concept direction; the other three combinations are
    # exercised in the Phase 3 tests.
    relationship = add(
        Relationship(
            id=repo.new_id(Relationship),
            created_at=now,
            updated_at=now,
            relation_type=RelationType.PART_OF,
            origin=RelationshipOrigin.EXPLICIT,
            lifecycle_status=LifecycleStatus.ACTIVE,
            from_knowledge_id=knowledge.id,
            to_concept_id=concept.id,
        )
    )
    # Phase 3 evidence. The edge above is EXPLICIT, so ADR 0011 requires it to
    # carry evidence; building it here keeps the sample internally consistent.
    add(
        RelationshipOccurrence(
            id=repo.new_id(RelationshipOccurrence),
            created_at=now,
            updated_at=now,
            relationship_id=relationship.id,
            source_id=source.id,
            document_id=document.id,
            original_text="Sequential logic is built from flip-flops.",
            extraction_method="manual",
            extraction_timestamp=now,
            document_version_id=version.id,
            segment_id=segment.id,
            page_number=214,
            section="5.3",
        )
    )
    add(
        ConceptAlias(
            id=repo.new_id(ConceptAlias),
            created_at=now,
            updated_at=now,
            concept_id=concept.id,
            alias="Sequential logic",
            normalized_alias=normalize_alias("Sequential logic"),
            lifecycle_status=LifecycleStatus.ACTIVE,
            source_id=source.id,
        )
    )
    add(
        ConceptOccurrence(
            id=repo.new_id(ConceptOccurrence),
            created_at=now,
            updated_at=now,
            concept_id=concept.id,
            source_id=source.id,
            document_id=document.id,
            surface_form="sequential logic",
            extraction_method="manual",
            extraction_timestamp=now,
            document_version_id=version.id,
            segment_id=segment.id,
            page_number=214,
            section="5.3",
        )
    )
    add(
        Equation(
            id=repo.new_id(Equation),
            created_at=now,
            updated_at=now,
            expression="I = V / R",
            lifecycle_status=LifecycleStatus.ACTIVE,
            knowledge_id=knowledge.id,
        )
    )
    add(
        Variable(
            id=repo.new_id(Variable),
            created_at=now,
            updated_at=now,
            symbol="I",
            lifecycle_status=LifecycleStatus.ACTIVE,
            name="Current",
            unit="A",
        )
    )
    add(
        Rule(
            id=repo.new_id(Rule),
            created_at=now,
            updated_at=now,
            name="Ohm's law",
            statement="Current equals voltage divided by resistance.",
            lifecycle_status=LifecycleStatus.ACTIVE,
        )
    )
    add(
        Procedure(
            id=repo.new_id(Procedure),
            created_at=now,
            updated_at=now,
            name="Open application",
            documentation_status=ProcedureDocumentationStatus.DOCUMENTED_PROCEDURE,
            lifecycle_status=LifecycleStatus.ACTIVE,
            execution_count=0,
            successful_executions=0,
            failed_executions=0,
        )
    )
    derivation = add(
        Derivation(
            id=repo.new_id(Derivation),
            created_at=now,
            updated_at=now,
            method="substitution",
            summary="I = V / R with V = 10 V and R = 20 ohm",
            target_id=knowledge.id,
        )
    )
    add(
        Calculation(
            id=repo.new_id(Calculation),
            created_at=now,
            updated_at=now,
            formula="I = V / R",
            verification_status=VerificationStatus.PENDING,
            result_value="0.5",
            result_unit="A",
            derivation_id=derivation.id,
        )
    )
    conflict = add(
        Conflict(
            id=repo.new_id(Conflict),
            created_at=now,
            updated_at=now,
            claim_a_id=knowledge.id,
            claim_b_id=other_knowledge.id,
            # Never invented: Part 2 section 46.
            cause=ConflictCause.UNDETERMINED,
            lifecycle_status=LifecycleStatus.ACTIVE,
        )
    )
    query = add(
        Query(
            id=repo.new_id(Query),
            created_at=now,
            updated_at=now,
            text="Explain JK flip-flop excitation.",
            task_class=TaskClass.KNOWLEDGE_QUERY,
        )
    )
    intent = add(
        Intent(
            id=repo.new_id(Intent),
            created_at=now,
            updated_at=now,
            intent_type="KNOWLEDGE_QUERY",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            target="JK flip-flop excitation",
            query_id=query.id,
        )
    )
    add(
        Action(
            id=repo.new_id(Action),
            created_at=now,
            updated_at=now,
            name="OPEN_APPLICATION",
            risk_level=RiskLevel.LOW,
        )
    )
    plan = add(
        ExecutionPlan(
            id=repo.new_id(ExecutionPlan),
            created_at=now,
            updated_at=now,
            status="DRAFT",
            step_count=0,
            intent_id=intent.id,
        )
    )
    add(
        Verification(
            id=repo.new_id(Verification),
            created_at=now,
            updated_at=now,
            subject_id=plan.id,
            status=VerificationStatus.PENDING,
        )
    )
    add(
        MemoryItem(
            id=repo.new_id(MemoryItem),
            created_at=now,
            updated_at=now,
            category=MemoryCategory.LONG_TERM_MEMORY,
            key="preferred_units",
            value="SI",
            lifecycle_status=LifecycleStatus.ACTIVE,
        )
    )
    add(
        AuditEvent(
            id=repo.new_id(AuditEvent),
            created_at=now,
            updated_at=now,
            event_type="PHASE2_SELF_TEST",
            occurred_at=now,
            actor="tests",
            result="stored",
            document_id=document.id,
            knowledge_id=knowledge.id,
        )
    )
    # Phase 5 (ADR 0018, ADR 0022): a completed run over the sample document, and
    # one issue it recorded.
    run = add(
        ExtractionRun(
            id=repo.new_id(ExtractionRun),
            created_at=now,
            updated_at=now,
            document_id=document.id,
            run_number=1,
            trigger=ExtractionTrigger.FIRST_EXTRACTION,
            extractor_version="1",
            parser_name="pypdf",
            started_at=now,
            status=ExtractionRunStatus.PARTIAL,
            completed_at=now,
        )
    )
    add(
        ExtractionIssue(
            id=repo.new_id(ExtractionIssue),
            created_at=now,
            updated_at=now,
            extraction_run_id=run.id,
            document_id=document.id,
            issue_type=ExtractionIssueType.INVALID_EQUATION_STRUCTURE,
            detail="equation not stored: two '=' signs run together",
            page_number=214,
            excerpt="dQ== or v",
        )
    )
    # Phase 6 (ADR 0027): the recorded basis of one inferred edge. The definition
    # above ("A flip-flop is a bistable circuit.") names a second concept, so the
    # edge joins the two, and its basis points at that definition's occurrence.
    defined = add(
        Concept(
            id=repo.new_id(Concept),
            created_at=now,
            updated_at=now,
            canonical_name="Flip-flop",
            lifecycle_status=LifecycleStatus.ACTIVE,
        )
    )
    named = add(
        Concept(
            id=repo.new_id(Concept),
            created_at=now,
            updated_at=now,
            canonical_name="Bistable circuit",
            lifecycle_status=LifecycleStatus.ACTIVE,
        )
    )
    inferred = add(
        Relationship(
            id=repo.new_id(Relationship),
            created_at=now,
            updated_at=now,
            relation_type=RelationType.RELATED_TO,
            origin=RelationshipOrigin.INFERRED,
            lifecycle_status=LifecycleStatus.ACTIVE,
            from_concept_id=defined.id,
            to_concept_id=named.id,
        )
    )
    add(
        RelationshipInference(
            id=repo.new_id(RelationshipInference),
            created_at=now,
            updated_at=now,
            relationship_id=inferred.id,
            rule="R1",
            rule_version="1",
            basis_occurrence_id=occurrence.id,
            matched_text="bistable circuit",
        )
    )
    # Phase 8 (ADR 0034): the assessment record of the conflicting pair above, which
    # rule C1 would write, and a concept-equivalence record for two concepts that
    # share a name (canonical order: the smaller identifier first).
    add(
        KnowledgeEquivalence(
            id=repo.new_id(KnowledgeEquivalence),
            created_at=now,
            updated_at=now,
            canonical_knowledge_id=knowledge.id,
            outcome=KnowledgeEquivalenceOutcome.CONTRADICTORY,
            rule="C1",
            rule_version="1",
            other_knowledge_id=other_knowledge.id,
            extraction_run_id=run.id,
            conflict_id=conflict.id,
        )
    )
    first, second = sorted((concept.id, defined.id))
    add(
        ConceptEquivalence(
            id=repo.new_id(ConceptEquivalence),
            created_at=now,
            updated_at=now,
            concept_a_id=first,
            concept_b_id=second,
            status=ConceptEquivalenceStatus.POSSIBLE_EQUIVALENT,
            basis=ConceptEquivalenceBasis.SHARED_NAME_OTHER_DOCUMENT,
            rule="P8-16",
            rule_version="1",
            shared_name="flip-flop",
        )
    )
    # One KnowledgeObject is created twice above (the conflicting pair), and three
    # Concepts and two Relationships exist, so the list is longer than the number
    # of entity types.
    return made
