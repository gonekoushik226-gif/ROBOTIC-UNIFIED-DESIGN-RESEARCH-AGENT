"""Read models (decision D-32, ADR 0012).

A read model is what a retrieval returns. It is **not an entity**: it has no
`KIND`, no `TABLE`, is never persisted, and is excluded from `ALL_ENTITIES`,
`PHASE_3_ENTITIES` and `ENTITIES`. The generic entity invariants would fail on it,
correctly, because it is a different kind of thing.

It exists because `ARCHITECTURE.md` section 6.5 forbids "arbitrary dict payloads
across module boundaries": Part 5 section 187's final step, "Retrieve", has to hand
back something typed.

Frozen, like every model here, for the reason Part 1 section 24 gives - no shared
mutable state.
"""

from dataclasses import dataclass, field

from app.models.entities import (
    Concept,
    ConceptAlias,
    ConceptOccurrence,
    Document,
    DocumentVersion,
    Equation,
    ExtractionRun,
    KnowledgeObject,
    Relationship,
    Source,
    SourceOccurrence,
)


@dataclass(frozen=True, slots=True)
class GraphIntegrityReport:
    """What the schema could not prevent (decision D-28, ADR 0011).

    Phase 3 ships with one rule the database cannot hold - an `EXPLICIT`
    relationship must carry evidence - because the occurrence is written after the
    edge and SQLite has no deferrable custom constraints. The service signature
    prevents it; this reports anything that arrived another way.

    Empty tuples mean "nothing found", which is the only honest way to say it:
    a boolean would collapse "checked and clean" into "did not check".
    """

    #: EXPLICIT relationships with no `relationship_occurrence` (ADR 0011).
    explicit_without_evidence: tuple[str, ...] = ()
    #: Aliases whose concept is gone. Should be impossible - the foreign key says
    #: so - and is checked anyway, because a constraint believed rather than
    #: verified is not a constraint.
    orphaned_aliases: tuple[str, ...] = ()
    #: Raw `PRAGMA foreign_key_check` rows. Meaningful now that endpoints are real
    #: foreign keys (decision D-22); under the rejected trigger-only design this
    #: pragma would have been blind to them.
    foreign_key_violations: tuple[tuple, ...] = ()
    #: ACTIVE `INFERRED` relationships with no `relationship_inference` row
    #: (ADR 0027). **Reported, deliberately not part of `is_clean`**: the Phase 3
    #: service legitimately creates `INFERRED` edges without a basis (ADR 0011), so
    #: folding this in would redefine what the Phase 3 check means. Phase 6 writes
    #: every edge with its basis in one transaction; this report is how an edge
    #: written any other way is found.
    inferred_without_basis: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        """The Phase 3 contract, unchanged by Phase 6 (ADR 0027)."""
        return not (
            self.explicit_without_evidence
            or self.orphaned_aliases
            or self.foreign_key_violations
        )


@dataclass(frozen=True, slots=True)
class AttachedKnowledge:
    """One knowledge object reached from a concept, with the edge that reached it.

    The edge is carried alongside rather than discarded, because Part 2 section 40
    makes *how* the connection was established part of the answer: a caller must be
    able to tell a relationship a source stated from one RUDRA inferred, without a
    second query.
    """

    knowledge: KnowledgeObject
    relationship: Relationship


@dataclass(frozen=True, slots=True)
class ConceptView:
    """A concept and everything attached to it (Part 5 section 187, "Retrieve").

    Assembled by `app.knowledge`, never by a query alone. The lists are tuples
    because this object is frozen and shared.

    Deliberately narrower than Part 2 section 65's retrieval list (properties,
    applications, examples, dependencies, conflicts, and so on): section 65
    describes the Phase 9 query engine. Phase 3 returns what Part 5 section 187
    asks it to attach, and no more, so the object does not imply capabilities that
    do not exist.
    """

    concept: Concept
    aliases: tuple[ConceptAlias, ...] = ()
    definitions: tuple[AttachedKnowledge, ...] = ()
    equations: tuple[Equation, ...] = ()
    #: Every other edge from or to this concept, whatever its relation type.
    relationships: tuple[Relationship, ...] = ()
    #: Edges of type PREREQUISITE_OF pointing AT this concept, i.e. what must be
    #: understood first. Direction is asserted, never guessed (decision D-23).
    prerequisites: tuple[Relationship, ...] = ()
    #: Where this concept's name was found. Empty means no evidence exists - which
    #: is reported as absence, never filled in (Part 5 section 205).
    occurrences: tuple[ConceptOccurrence, ...] = field(default=())

    @property
    def has_provenance(self) -> bool:
        """Whether any evidence supports this concept at all.

        Part 5 section 205: when provenance does not exist the system must say so
        and "must NOT invent a citation". This is the honest answer to that.
        """
        return bool(self.occurrences)


@dataclass(frozen=True, slots=True)
class DefinitionEvidence:
    """One stored definition of a concept, with the evidence one run recorded for it.

    The input rule R1 reads (ADR 0028): a concept's `ACTIVE`, `EXPLICIT`
    `DEFINED_BY` edge to an `ACTIVE` `DEFINITION` knowledge object, and that
    object's source occurrences in the named run. Both counts are carried so the
    reader can refuse an ambiguous direction rather than guess: a definition with
    more than one `DEFINED_BY` edge, or more than one occurrence in the run, cannot
    say which concept's definition contained a mention (ADR 0027).
    """

    concept_id: str
    knowledge_id: str
    #: Every ACTIVE `DEFINED_BY` edge pointing at the knowledge object, whatever
    #: its origin or source endpoint.
    defined_by_edges: int
    #: The knowledge object's source occurrences whose run is the named run.
    occurrences: tuple[SourceOccurrence, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    """The stored rows a set of evidence rows names (ADR 0036 P9-18).

    An `evidence` row carries identifiers only; this holds the source, document, run
    and declared-edition rows behind them, exactly as stored, each once and in
    numeric identifier order (ADR 0006). Nothing is derived: stored availability
    stays stored availability (P9-20), and a run's status and extractor version are
    what the run recorded.

    A declared edition belongs to the document whose `file_hash` it carries - that
    is how `edition` files it (ADR 0034 P8-27). A document with no such row has no
    edition declared, which is reported as absence, never filled in.
    """

    sources: tuple[Source, ...] = ()
    documents: tuple[Document, ...] = ()
    runs: tuple[ExtractionRun, ...] = ()
    editions: tuple[DocumentVersion, ...] = ()
