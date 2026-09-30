"""The Part 5 section 187 flow, as operations (Phase 3).

    Create concept -> Attach definition -> Attach equation -> Attach relationship
    -> Attach prerequisite -> Attach provenance -> Persist -> Retrieve

Each step is a method, because section 187 says "All operations must be testable"
and a step that only exists inside a larger routine is not separately testable.

Two rules shape this module:

* **Decision D-21** - a concept and a knowledge object are joined only by a
  `Relationship` row. There is no `concept_id` column on `knowledge_object`, and
  none is simulated here.
* **Decision D-28 (ADR 0011)** - an `EXPLICIT` relationship must carry evidence.
  That is enforced by the *signature*: `evidence` is a required argument whenever
  `origin` is EXPLICIT, so an omission fails at the call site rather than being
  caught later. It cannot be a database constraint - the occurrence is written
  after the edge, and SQLite has no deferrable custom constraints - which makes
  this the one Phase 3 rule that lives somewhere a determined writer can go around.
  `app.storage.queries.graph_integrity` is what detects that.
"""

from dataclasses import dataclass, replace

from app.core.errors import InvalidInputError
from app.models.base import utc_now
from app.models.entities import (
    Concept,
    ConceptAlias,
    ConceptOccurrence,
    Equation,
    KnowledgeObject,
    Relationship,
    RelationshipOccurrence,
)
from app.models.enums import (
    CertaintyState,
    KnowledgeType,
    LifecycleStatus,
    RelationType,
    RelationshipOrigin,
)
from app.models.naming import normalize_alias
from app.models.views import ConceptView
from app.storage import queries
from app.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class Evidence:
    """Where something was found (Part 3 section 75, Part 2 section 49).

    A plain value object, not an entity: it carries the *facts about a location*
    that an occurrence row needs, and the service turns it into the right row for
    whichever subject is being attached.
    """

    source_id: str
    document_id: str
    text: str
    extraction_method: str = "manual"
    document_version_id: str | None = None
    segment_id: str | None = None
    page_number: int | None = None
    section: str | None = None
    #: Offsets into the segment's text (decision D-40, ADR 0019). Set only when
    #: the caller genuinely knows them; never recomputed by searching the page.
    char_start: int | None = None
    char_end: int | None = None
    #: The extraction run that observed this (decision D-35, ADR 0018). None for
    #: evidence entered by hand, which no run produced.
    extraction_run_id: str | None = None


class ConceptService:
    """Assemble, attach to, and retrieve concepts.

    Holds a `Repository` rather than a connection, so every write is validated
    before it reaches the database (validation happens in Python *and* in the
    schema; only the second cannot be bypassed).

    Nothing here commits. Callers decide transaction boundaries, which is what lets
    an edge and its first occurrence share one transaction - the property decision
    D-28 depends on.
    """

    __slots__ = ("_repository",)

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    @property
    def repository(self) -> Repository:
        return self._repository

    @property
    def _connection(self):
        return self._repository.connection

    # ------------------------------------------------------ 1. create concept

    def create_concept(
        self,
        canonical_name: str,
        *,
        context: str | None = None,
        description: str | None = None,
    ) -> Concept:
        """Create a concept and the alias row for its own canonical name.

        The canonical name gets a `concept_alias` row (sub-decision S-1) so that
        every lookup - canonical or alias - takes one index path instead of two
        different ones.

        `context` is Part 6 section 46's disambiguator. Two concepts may share a
        name: "Gain" in a voltage amplifier and "Gain" in a control system are
        different things, and nothing here prevents both existing. That is
        deliberate - a uniqueness constraint would force an automatic merge
        decision, which Part 2 section 44 and Part 3 section 73 forbid.
        """
        now = utc_now()
        concept = self._repository.add(
            Concept(
                id=self._repository.new_id(Concept),
                created_at=now,
                updated_at=now,
                canonical_name=canonical_name,
                lifecycle_status=LifecycleStatus.ACTIVE,
                description=description,
                context=context,
            )
        )
        self.add_alias(concept.id, canonical_name)
        return concept

    def add_alias(
        self, concept_id: str, alias: str, *, source_id: str | None = None
    ) -> ConceptAlias:
        """Record another name for this concept (Part 2 section 44).

        `source_id` is optional so a user-stated name can still answer "where did
        this come from?" without a document.
        """
        now = utc_now()
        return self._repository.add(
            ConceptAlias(
                id=self._repository.new_id(ConceptAlias),
                created_at=now,
                updated_at=now,
                concept_id=concept_id,
                alias=alias,
                normalized_alias=normalize_alias(alias),
                lifecycle_status=LifecycleStatus.ACTIVE,
                source_id=source_id,
            )
        )

    def resolve(self, name: str) -> tuple[Concept, ...]:
        """Every concept answering to `name`. Never picks one (Part 3 section 73)."""
        return queries.concepts_by_normalized_alias(
            self._connection, normalize_alias(name)
        )

    # --------------------------------------------------- 2. attach definition

    def attach_definition(
        self,
        concept_id: str,
        statement: str,
        *,
        label: str,
        evidence: Evidence | None = None,
        origin: RelationshipOrigin = RelationshipOrigin.EXPLICIT,
        certainty: CertaintyState = CertaintyState.REPORTED_BY_SOURCE,
    ) -> tuple[KnowledgeObject, Relationship]:
        """Attach a DEFINITION knowledge object to a concept.

        `label` names the *knowledge object*, not the concept (invariant I-KO-NAME,
        ADR 0012): `canonical_name` answers "what is this record called?", never
        "which concept is this about?". It carries no referential authority, and
        retrieval never uses it to find anything - which the name-coupling test
        proves rather than assumes.
        """
        knowledge = self._add_knowledge(
            KnowledgeType.DEFINITION, label, statement, certainty
        )
        relationship = self.attach_relationship(
            from_concept_id=concept_id,
            to_knowledge_id=knowledge.id,
            relation_type=RelationType.DEFINED_BY,
            origin=origin,
            evidence=evidence,
        )
        return knowledge, relationship

    # ----------------------------------------------------- 3. attach equation

    def attach_equation(
        self,
        concept_id: str,
        expression: str,
        *,
        label: str,
        relation_type: RelationType,
        description: str | None = None,
        evidence: Evidence | None = None,
        origin: RelationshipOrigin = RelationshipOrigin.EXPLICIT,
    ) -> tuple[Equation, KnowledgeObject, Relationship]:
        """Attach an equation to a concept.

        Three rows, because decision D-21 routes concept/knowledge association
        through `relationship` and `Equation` hangs off a knowledge object: an
        EQUATION knowledge object carrying the claim, the `Equation` row carrying
        the expression, and the edge.

        `canonical_form` is left NULL on purpose. Parsing an expression into a
        canonical form is Part 3 sections 91-92 and Part 5 section 202 - the
        calculation engine, Phase 11. Phase 3 stores the equation as written and
        does not pretend to understand it.

        **`relation_type` is required, and has no default.** The specification does
        not say which relation connects an equation to a concept. Part 2 section 39
        introduces its vocabulary with the word "Examples:", and `USES`,
        `APPLIES_TO` and `DERIVED_FROM` each appear exactly once in the whole
        specification - in that list, never applied to anything. Where equations and
        concepts are discussed together the wording stays deliberately untyped:
        "Which equations relate to X?" (section 21), "Which equations are associated
        with X?" (section 249), "Retrieve equations" (section 65).

        Picking one here would put an invented default into storage, where every
        later phase would inherit it as though the specification had said so. The
        caller states the relation it means.
        """
        knowledge = self._add_knowledge(
            KnowledgeType.EQUATION, label, expression, CertaintyState.REPORTED_BY_SOURCE
        )
        now = utc_now()
        equation = self._repository.add(
            Equation(
                id=self._repository.new_id(Equation),
                created_at=now,
                updated_at=now,
                expression=expression,
                lifecycle_status=LifecycleStatus.ACTIVE,
                canonical_form=None,
                knowledge_id=knowledge.id,
                description=description,
            )
        )
        relationship = self.attach_relationship(
            from_concept_id=concept_id,
            to_knowledge_id=knowledge.id,
            relation_type=relation_type,
            origin=origin,
            evidence=evidence,
        )
        return equation, knowledge, relationship

    # ------------------------------------- 4/5. attach relationship, prerequisite

    def attach_relationship(
        self,
        *,
        relation_type: RelationType,
        origin: RelationshipOrigin,
        evidence: Evidence | None = None,
        from_concept_id: str | None = None,
        from_knowledge_id: str | None = None,
        to_concept_id: str | None = None,
        to_knowledge_id: str | None = None,
    ) -> Relationship:
        """Create an edge, and its first occurrence when it is EXPLICIT.

        **Decision D-28.** `evidence` is required when `origin` is EXPLICIT, and
        refused as pointless when it is absent. Marking an edge EXPLICIT asserts
        that a source stated it; with nothing to point at, that is Part 1 section
        5's "UNSUPPORTED INFORMATION MUST NOT BE PRESENTED AS FACT" in storage form,
        and Part 6 section 12's warning that a flag is not a substitute for
        provenance.

        The edge and its occurrence are written in the caller's transaction, so
        either both land or neither does.
        """
        if origin is RelationshipOrigin.EXPLICIT and evidence is None:
            raise InvalidInputError.of(
                "An EXPLICIT relationship was not created because no evidence was given.",
                "Part 2 section 40 forbids presenting an inferred relationship as one "
                "a source stated. Marking an edge EXPLICIT asserts that a source "
                "stated it, so a source occurrence is required.",
                stage="knowledge.attach_relationship",
                missing=("evidence",),
                data_changed=False,
                retry_safe=True,
                next_options=(
                    "Pass evidence=Evidence(source_id=..., document_id=..., text=...).",
                    "Use origin=RelationshipOrigin.INFERRED if RUDRA derived this edge.",
                ),
            )

        now = utc_now()
        relationship = self._repository.add(
            Relationship(
                id=self._repository.new_id(Relationship),
                created_at=now,
                updated_at=now,
                relation_type=relation_type,
                origin=origin,
                lifecycle_status=LifecycleStatus.ACTIVE,
                from_concept_id=from_concept_id,
                from_knowledge_id=from_knowledge_id,
                to_concept_id=to_concept_id,
                to_knowledge_id=to_knowledge_id,
            )
        )
        if evidence is not None:
            self.attach_relationship_provenance(relationship.id, evidence)
        return relationship

    def attach_prerequisite(
        self,
        concept_id: str,
        prerequisite_concept_id: str,
        *,
        origin: RelationshipOrigin,
        evidence: Evidence | None = None,
    ) -> Relationship:
        """Record that `prerequisite_concept_id` must be understood first.

        Direction is asserted, never guessed: `PREREQUISITE_OF` is stored with the
        prerequisite as `from` and the dependent concept as `to`. Only one direction
        of each inverse pair is stored (decision D-23); the reverse view is a query.
        """
        return self.attach_relationship(
            from_concept_id=prerequisite_concept_id,
            to_concept_id=concept_id,
            relation_type=RelationType.PREREQUISITE_OF,
            origin=origin,
            evidence=evidence,
        )

    def promote_to_explicit(
        self, relationship: Relationship, evidence: Evidence
    ) -> Relationship:
        """Record that a source states an edge RUDRA had inferred.

        Permitted, and not a breach of Part 2 section 40: the edge *is* now
        explicit. The reverse is prohibited, in the schema as well as here.
        Evidence is required for the same reason it is required at creation.
        """
        self.attach_relationship_provenance(relationship.id, evidence)
        return self._repository.update(
            replace(relationship, origin=RelationshipOrigin.EXPLICIT)
        )

    # --------------------------------------------------- 6. attach provenance

    def attach_concept_provenance(
        self, concept_id: str, evidence: Evidence
    ) -> ConceptOccurrence:
        """Record where this concept's name was found.

        `surface_form` is the term **as the source printed it**, which is what Part
        2 section 44's "retain the original terminology" asks for. The concept's own
        `canonical_name` is RUDRA's choice and may differ.
        """
        now = utc_now()
        return self._repository.add(
            ConceptOccurrence(
                id=self._repository.new_id(ConceptOccurrence),
                created_at=now,
                updated_at=now,
                concept_id=concept_id,
                source_id=evidence.source_id,
                document_id=evidence.document_id,
                surface_form=evidence.text,
                extraction_method=evidence.extraction_method,
                extraction_timestamp=now,
                document_version_id=evidence.document_version_id,
                segment_id=evidence.segment_id,
                page_number=evidence.page_number,
                section=evidence.section,
                char_start=evidence.char_start,
                char_end=evidence.char_end,
                extraction_run_id=evidence.extraction_run_id,
            )
        )

    def attach_relationship_provenance(
        self, relationship_id: str, evidence: Evidence
    ) -> RelationshipOccurrence:
        """Record the sentence that states a relationship."""
        now = utc_now()
        return self._repository.add(
            RelationshipOccurrence(
                id=self._repository.new_id(RelationshipOccurrence),
                created_at=now,
                updated_at=now,
                relationship_id=relationship_id,
                source_id=evidence.source_id,
                document_id=evidence.document_id,
                original_text=evidence.text,
                extraction_method=evidence.extraction_method,
                extraction_timestamp=now,
                document_version_id=evidence.document_version_id,
                segment_id=evidence.segment_id,
                page_number=evidence.page_number,
                section=evidence.section,
                char_start=evidence.char_start,
                char_end=evidence.char_end,
                extraction_run_id=evidence.extraction_run_id,
            )
        )

    # -------------------------------------------------------- 7/8. retrieve

    def retrieve(self, concept_id: str) -> ConceptView | None:
        """Assemble everything attached to a concept (Part 5 section 187).

        Returns None when the concept does not exist. Absence is an answer, not an
        error.

        Nothing here looks anything up by name. That is the point of invariant
        I-KO-NAME, and the name-coupling test proves it: renaming a definition's
        `canonical_name` to something unrelated must not change what this returns.
        """
        concept = self._repository.get(Concept, concept_id)
        if concept is None:
            return None
        connection = self._connection
        definitions = queries.attached_knowledge(
            connection,
            concept_id,
            relation_type=RelationType.DEFINED_BY,
            knowledge_type=KnowledgeType.DEFINITION.value,
        )
        return ConceptView(
            concept=concept,
            aliases=queries.aliases_for_concept(connection, concept_id),
            definitions=definitions,
            equations=queries.equations_for_concept(connection, concept_id),
            relationships=queries.relationships_for_concept(connection, concept_id),
            prerequisites=queries.prerequisites_of_concept(connection, concept_id),
            occurrences=queries.occurrences_for_concept(connection, concept_id),
        )

    # ------------------------------------------------------------- internals

    def _add_knowledge(
        self,
        knowledge_type: KnowledgeType,
        label: str,
        statement: str,
        certainty: CertaintyState,
    ) -> KnowledgeObject:
        now = utc_now()
        return self._repository.add(
            KnowledgeObject(
                id=self._repository.new_id(KnowledgeObject),
                created_at=now,
                updated_at=now,
                knowledge_type=knowledge_type,
                canonical_name=label,
                statement=statement,
                lifecycle_status=LifecycleStatus.ACTIVE,
                certainty=certainty,
                knowledge_version=1,
            )
        )
