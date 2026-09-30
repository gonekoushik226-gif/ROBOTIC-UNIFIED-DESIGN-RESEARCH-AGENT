"""Read queries for the knowledge graph (Phase 3).

**All SQL lives in `app.storage`** - `tests/unit/test_code_rules.py` fails the build
if it appears anywhere else, which is what keeps Part 1 section 4's "the database is
replaceable" true rather than aspirational. `app.knowledge` therefore holds assembly
and rules, and calls into here for every row it needs.

These are queries only: nothing here writes. Writes go through `Repository`, which
validates first.

Derived relations (decision D-23, ADR 0009). Only one direction of each inverse pair
is stored: `PARENT_OF` is a row, `CHILD_OF` is this module reading it the other way,
and ancestors and descendants are recursive closures. `UNION` rather than `UNION ALL`
in the recursive terms is what makes a cyclic graph terminate - Phase 0 check #4
verified that behaviour before it was relied on.
"""

import sqlite3
from collections.abc import Iterable, Mapping

from app.models.entities import (
    Concept,
    ConceptAlias,
    ConceptEquivalence,
    ConceptOccurrence,
    Document,
    DocumentSegment,
    DocumentVersion,
    Equation,
    ExtractionRun,
    KnowledgeObject,
    Relationship,
    RelationshipOccurrence,
    Source,
    SourceOccurrence,
)
from app.models.enums import (
    KnowledgeType,
    LifecycleStatus,
    RelationType,
    RelationshipOrigin,
)
from app.models.views import (
    AttachedKnowledge,
    DefinitionEvidence,
    EvidenceContext,
    GraphIntegrityReport,
)
from app.storage.mapping import from_row

#: Every column of `relationship`, qualified, for joins that also select a peer row.
_REL_COLUMNS = (
    "r.id, r.created_at, r.updated_at, r.relation_type, r.origin, r.lifecycle_status, "
    "r.from_concept_id, r.from_knowledge_id, r.to_concept_id, r.to_knowledge_id"
)


def _rows(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> list:
    return connection.execute(sql, params).fetchall()


# --------------------------------------------------------------------- documents


def document_by_hash(
    connection: sqlite3.Connection, file_hash: str
) -> Document | None:
    """The document with these exact bytes, if it is already known.

    Part 3 section 80: if the hash matches an existing source, the file must not be
    ingested as a new independent document. This is the lookup that makes that
    possible, and it is a plain equality test on an indexed column - the identity
    of a file is its content, not its name.
    """
    row = connection.execute(
        "SELECT * FROM document WHERE file_hash = ?", (file_hash,)
    ).fetchone()
    return None if row is None else from_row(Document, row)


def sources_for_document(
    connection: sqlite3.Connection, document_id: str
) -> tuple:
    """Every source record pointing at this document.

    Needed by Part 7 section 20: when the same file is uploaded again after its
    source was marked deleted, the existing source must be re-associated rather
    than duplicated. Finding it is the first half of that.
    """
    from app.models.entities import Source

    rows = connection.execute(
        "SELECT * FROM source WHERE document_id = ? ORDER BY created_at, id",
        (document_id,),
    ).fetchall()
    return tuple(from_row(Source, row) for row in rows)


def structure_for_document(
    connection: sqlite3.Connection, document_id: str
) -> tuple:
    """Every structural element of a document, in reading order (Part 2 section 35)."""
    from app.models.entities import DocumentStructure

    rows = connection.execute(
        "SELECT * FROM document_structure WHERE document_id = ? ORDER BY ordinal, id",
        (document_id,),
    ).fetchall()
    return tuple(from_row(DocumentStructure, row) for row in rows)


def segments_for_document(
    connection: sqlite3.Connection, document_id: str
) -> tuple:
    """Every page segment of a document, in page order (Part 2 section 32)."""
    from app.models.entities import DocumentSegment

    rows = connection.execute(
        "SELECT * FROM document_segment WHERE document_id = ? "
        "ORDER BY page_number, ordinal, id",
        (document_id,),
    ).fetchall()
    return tuple(from_row(DocumentSegment, row) for row in rows)


# ------------------------------------------------------------------ concept names


def aliases_for_concept(
    connection: sqlite3.Connection, concept_id: str, *, active_only: bool = True
) -> tuple[ConceptAlias, ...]:
    """Every curated name for a concept (Part 2 section 44)."""
    sql = "SELECT * FROM concept_alias WHERE concept_id = ?"
    params: tuple = (concept_id,)
    if active_only:
        sql += " AND lifecycle_status = ?"
        params += (LifecycleStatus.ACTIVE.value,)
    sql += " ORDER BY created_at, id"
    return tuple(from_row(ConceptAlias, row) for row in _rows(connection, sql, params))


def concepts_by_normalized_alias(
    connection: sqlite3.Connection, normalized_alias: str
) -> tuple[Concept, ...]:
    """Every concept that answers to this name.

    Returns *all* matches, never a best guess. Part 6 section 46 requires "Gain" to
    resolve to three different concepts, and Part 3 section 73 forbids merging
    concepts on name similarity - so picking one here would be the system making a
    judgement it is not entitled to make. Disambiguation belongs to the caller,
    with `Concept.context` in hand.
    """
    sql = (
        "SELECT c.* FROM concept c "
        "JOIN concept_alias a ON a.concept_id = c.id "
        "WHERE a.normalized_alias = ? AND a.lifecycle_status = ? "
        "ORDER BY c.created_at, c.id"
    )
    rows = _rows(connection, sql, (normalized_alias, LifecycleStatus.ACTIVE.value))
    return tuple(from_row(Concept, row) for row in rows)


# --------------------------------------------------------------------- evidence


def occurrences_for_concept(
    connection: sqlite3.Connection, concept_id: str
) -> tuple[ConceptOccurrence, ...]:
    sql = (
        "SELECT * FROM concept_occurrence WHERE concept_id = ? "
        "ORDER BY document_id, page_number, id"
    )
    return tuple(
        from_row(ConceptOccurrence, row) for row in _rows(connection, sql, (concept_id,))
    )


def occurrences_for_relationship(
    connection: sqlite3.Connection, relationship_id: str
) -> tuple[RelationshipOccurrence, ...]:
    sql = (
        "SELECT * FROM relationship_occurrence WHERE relationship_id = ? "
        "ORDER BY document_id, page_number, id"
    )
    return tuple(
        from_row(RelationshipOccurrence, row)
        for row in _rows(connection, sql, (relationship_id,))
    )


def evidence_for(connection: sqlite3.Connection, subject_id: str) -> tuple[dict, ...]:
    """Every piece of evidence for any subject, through the `evidence` view.

    This is Part 2 section 49's "Where did this come from?" as one query, across
    knowledge objects, concepts and relationships alike. Returns plain dictionaries
    because the view is a union of three different subjects and has no single entity
    type; callers that want typed rows use the per-subject functions above.
    """
    sql = (
        "SELECT * FROM evidence WHERE subject_id = ? "
        "ORDER BY subject_kind, document_id, page_number, id"
    )
    return tuple(dict(row) for row in _rows(connection, sql, (subject_id,)))


# ---------------------------------------------------------------- graph traversal


def relationships_for_concept(
    connection: sqlite3.Connection, concept_id: str, *, active_only: bool = True
) -> tuple[Relationship, ...]:
    """Every stored edge touching this concept, in either direction."""
    sql = (
        f"SELECT {_REL_COLUMNS} FROM relationship r "
        "WHERE (r.from_concept_id = ? OR r.to_concept_id = ?)"
    )
    params: tuple = (concept_id, concept_id)
    if active_only:
        sql += " AND r.lifecycle_status = ?"
        params += (LifecycleStatus.ACTIVE.value,)
    sql += " ORDER BY r.created_at, r.id"
    return tuple(from_row(Relationship, row) for row in _rows(connection, sql, params))


def attached_knowledge(
    connection: sqlite3.Connection,
    concept_id: str,
    *,
    relation_type: RelationType | None = None,
    knowledge_type: str | None = None,
) -> tuple[AttachedKnowledge, ...]:
    """Knowledge objects reached from this concept, with the edge that reached them.

    The edge is returned alongside because Part 2 section 40 makes *how* the
    connection was established part of the answer: a caller must be able to tell a
    source-stated relationship from an inferred one without a second query.
    """
    # `r.id AS relationship_id` rather than `r.*`: a join of two tables that both
    # have `id`, `created_at`, `updated_at` and `lifecycle_status` produces duplicate
    # column names, and `sqlite3.Row` resolves a duplicate name to the FIRST match -
    # so `k.*` would silently be shadowed by the relationship's values. Aliasing the
    # one column we need removes the ambiguity instead of relying on which side wins.
    sql = (
        "SELECT r.id AS relationship_id, k.* FROM relationship r "
        "JOIN knowledge_object k ON k.id = r.to_knowledge_id "
        "WHERE r.from_concept_id = ? AND r.lifecycle_status = ?"
    )
    params: tuple = (concept_id, LifecycleStatus.ACTIVE.value)
    if relation_type is not None:
        sql += " AND r.relation_type = ?"
        params += (relation_type.value,)
    if knowledge_type is not None:
        sql += " AND k.knowledge_type = ?"
        params += (knowledge_type,)
    sql += " ORDER BY r.created_at, r.id"

    attached = []
    for row in _rows(connection, sql, params):
        data = dict(row)
        relationship = one_relationship(connection, str(data["relationship_id"]))
        if relationship is None:  # pragma: no cover - the join guarantees it exists
            continue
        knowledge = from_row(
            KnowledgeObject,
            {name: data[name] for name in _column_names(KnowledgeObject)},
        )
        attached.append(AttachedKnowledge(knowledge=knowledge, relationship=relationship))
    return tuple(attached)


def one_relationship(
    connection: sqlite3.Connection, relationship_id: str
) -> Relationship | None:
    row = connection.execute(
        "SELECT * FROM relationship WHERE id = ?", (relationship_id,)
    ).fetchone()
    return None if row is None else from_row(Relationship, row)


def prerequisites_of_concept(
    connection: sqlite3.Connection, concept_id: str
) -> tuple[Relationship, ...]:
    """Edges saying "something must be understood before this concept".

    `PREREQUISITE_OF` is stored one way only (decision D-23): `from` is the
    prerequisite, `to` is what needs it. So the prerequisites *of* a concept are the
    edges pointing AT it.
    """
    sql = (
        f"SELECT {_REL_COLUMNS} FROM relationship r "
        "WHERE r.to_concept_id = ? AND r.relation_type = ? AND r.lifecycle_status = ? "
        "ORDER BY r.created_at, r.id"
    )
    params = (
        concept_id,
        RelationType.PREREQUISITE_OF.value,
        LifecycleStatus.ACTIVE.value,
    )
    return tuple(from_row(Relationship, row) for row in _rows(connection, sql, params))


def equations_for_concept(
    connection: sqlite3.Connection, concept_id: str
) -> tuple[Equation, ...]:
    """Equations reached from a concept through its knowledge objects.

    There is no concept-to-equation column: decision D-21 routes every
    concept/knowledge association through `relationship`, and `Equation` hangs off
    the knowledge object it belongs to. Two hops, by design.
    """
    sql = (
        "SELECT e.* FROM equation e "
        "JOIN relationship r ON r.to_knowledge_id = e.knowledge_id "
        "WHERE r.from_concept_id = ? AND r.lifecycle_status = ? "
        "AND e.lifecycle_status = ? "
        "ORDER BY e.created_at, e.id"
    )
    params = (concept_id, LifecycleStatus.ACTIVE.value, LifecycleStatus.ACTIVE.value)
    return tuple(from_row(Equation, row) for row in _rows(connection, sql, params))


def children_of_concept(
    connection: sqlite3.Connection, concept_id: str
) -> tuple[str, ...]:
    """Concepts one `PARENT_OF` hop below this one.

    `CHILD_OF` is never stored (decision D-23); it is this query.
    """
    sql = (
        "SELECT to_concept_id FROM relationship "
        "WHERE from_concept_id = ? AND relation_type = ? AND lifecycle_status = ? "
        "AND to_concept_id IS NOT NULL ORDER BY to_concept_id"
    )
    params = (concept_id, RelationType.PARENT_OF.value, LifecycleStatus.ACTIVE.value)
    return tuple(str(row[0]) for row in _rows(connection, sql, params))


def ancestors_of_concept(
    connection: sqlite3.Connection, concept_id: str
) -> tuple[str, ...]:
    """The transitive closure upward, following `PARENT_OF` backwards.

    `UNION`, not `UNION ALL`: that is what makes a cyclic hierarchy terminate rather
    than recurse forever. Part 2 section 42 warns that knowledge is a graph, not a
    tree, so a cycle is a state to survive, not to assume away.
    """
    sql = """
        WITH RECURSIVE up(id) AS (
            SELECT from_concept_id FROM relationship
             WHERE to_concept_id = ? AND relation_type = ?
               AND lifecycle_status = ? AND from_concept_id IS NOT NULL
            UNION
            SELECT r.from_concept_id FROM relationship r
              JOIN up ON r.to_concept_id = up.id
             WHERE r.relation_type = ? AND r.lifecycle_status = ?
               AND r.from_concept_id IS NOT NULL
        )
        SELECT id FROM up ORDER BY id
    """
    parent, active = RelationType.PARENT_OF.value, LifecycleStatus.ACTIVE.value
    params = (concept_id, parent, active, parent, active)
    return tuple(str(row[0]) for row in _rows(connection, sql, params))


def descendants_of_concept(
    connection: sqlite3.Connection, concept_id: str
) -> tuple[str, ...]:
    """The transitive closure downward. Terminates on cycles, as above."""
    sql = """
        WITH RECURSIVE down(id) AS (
            SELECT to_concept_id FROM relationship
             WHERE from_concept_id = ? AND relation_type = ?
               AND lifecycle_status = ? AND to_concept_id IS NOT NULL
            UNION
            SELECT r.to_concept_id FROM relationship r
              JOIN down ON r.from_concept_id = down.id
             WHERE r.relation_type = ? AND r.lifecycle_status = ?
               AND r.to_concept_id IS NOT NULL
        )
        SELECT id FROM down ORDER BY id
    """
    parent, active = RelationType.PARENT_OF.value, LifecycleStatus.ACTIVE.value
    params = (concept_id, parent, active, parent, active)
    return tuple(str(row[0]) for row in _rows(connection, sql, params))


# ------------------------------------------------------------------- integrity


def graph_integrity(connection: sqlite3.Connection) -> GraphIntegrityReport:
    """Report what the schema cannot prevent (decision D-28, ADR 0011).

    One rule in Phase 3 is not schema-enforced: an `EXPLICIT` relationship must
    carry evidence. It cannot be a constraint - the occurrence is inserted after the
    edge, and SQLite has no deferrable custom constraints - so the service signature
    prevents it and this query detects anything that got in another way.

    `PRAGMA foreign_key_check` is included because, with typed endpoints (decision
    D-22), it now genuinely covers relationship endpoints. Under the rejected
    trigger-only design it would have been blind to them.

    Phase 6 adds `inferred_without_basis` (ADR 0027): the same kind of gap for
    `INFERRED` edges, which the schema cannot prevent either. It is reported beside
    the Phase 3 fields and is not part of `is_clean`.
    """
    unsupported = tuple(
        str(row[0])
        for row in _rows(
            connection,
            "SELECT r.id FROM relationship r "
            "WHERE r.origin = ? AND r.lifecycle_status = ? "
            "AND NOT EXISTS (SELECT 1 FROM relationship_occurrence o "
            "                WHERE o.relationship_id = r.id) "
            "ORDER BY r.id",
            (RelationshipOrigin.EXPLICIT.value, LifecycleStatus.ACTIVE.value),
        )
    )
    mismatched = tuple(
        str(row[0])
        for row in _rows(
            connection,
            "SELECT id FROM concept_alias a WHERE NOT EXISTS "
            "(SELECT 1 FROM concept c WHERE c.id = a.concept_id) ORDER BY id",
        )
    )
    foreign_key_violations = tuple(
        tuple(row) for row in _rows(connection, "PRAGMA foreign_key_check")
    )
    # ADR 0027: reported separately, never folded into `is_clean`.
    inferred_without_basis = tuple(
        str(row[0])
        for row in _rows(
            connection,
            "SELECT r.id FROM relationship r "
            "WHERE r.origin = ? AND r.lifecycle_status = ? "
            "AND NOT EXISTS (SELECT 1 FROM relationship_inference i "
            "                WHERE i.relationship_id = r.id) "
            "ORDER BY r.id",
            (RelationshipOrigin.INFERRED.value, LifecycleStatus.ACTIVE.value),
        )
    )
    return GraphIntegrityReport(
        explicit_without_evidence=unsupported,
        orphaned_aliases=mismatched,
        foreign_key_violations=foreign_key_violations,
        inferred_without_basis=inferred_without_basis,
    )


def _column_names(entity_type: type) -> tuple[str, ...]:
    from app.storage.mapping import columns

    return columns(entity_type)


# ------------------------------------------------------ extraction (Phase 5)


def runs_for_document(connection: sqlite3.Connection, document_id: str) -> tuple:
    """Every extraction run of a document, oldest first (Part 2 section 62)."""
    from app.models.entities import ExtractionRun

    rows = connection.execute(
        "SELECT * FROM extraction_run WHERE document_id = ? ORDER BY run_number",
        (document_id,),
    ).fetchall()
    return tuple(from_row(ExtractionRun, row) for row in rows)


def next_run_number(connection: sqlite3.Connection, document_id: str) -> int:
    """The run number the next extraction of this document will take."""
    row = connection.execute(
        "SELECT COALESCE(MAX(run_number), 0) + 1 FROM extraction_run "
        "WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    return int(row[0])


def issues_for_run(connection: sqlite3.Connection, run_id: str) -> tuple:
    """Every extraction issue a run recorded, in page order (ADR 0022)."""
    from app.models.entities import ExtractionIssue

    rows = connection.execute(
        "SELECT * FROM extraction_issue WHERE extraction_run_id = ? "
        "ORDER BY page_number, id",
        (run_id,),
    ).fetchall()
    return tuple(from_row(ExtractionIssue, row) for row in rows)


def issue_counts_for_run(connection: sqlite3.Connection, run_id: str) -> dict[str, int]:
    """How many issues of each section 60 type a run recorded."""
    return {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT issue_type, count(*) FROM extraction_issue "
            "WHERE extraction_run_id = ? GROUP BY issue_type ORDER BY issue_type",
            (run_id,),
        ).fetchall()
    }


def active_relationship_between(
    connection: sqlite3.Connection,
    *,
    relation_type: RelationType,
    from_concept_id: str,
    to_concept_id: str,
) -> Relationship | None:
    """The one ACTIVE edge of this type between two concepts, if it exists.

    Decision D-24 (ADR 0009) allows exactly one: a relation stated twice is one edge
    with two occurrences, not two edges.
    """
    row = connection.execute(
        "SELECT * FROM relationship WHERE relation_type = ? "
        "AND from_concept_id = ? AND to_concept_id = ? AND lifecycle_status = ?",
        (relation_type.value, from_concept_id, to_concept_id, LifecycleStatus.ACTIVE.value),
    ).fetchone()
    return None if row is None else from_row(Relationship, row)


def knowledge_type_counts_for_run(
    connection: sqlite3.Connection, run_id: str
) -> dict[str, int]:
    """Knowledge objects whose evidence this run produced, by knowledge type.

    Counted through `source_occurrence`, because a run attaches to the evidence,
    never to the canonical object (ADR 0018).
    """
    return {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT k.knowledge_type, count(DISTINCT k.id) FROM knowledge_object k "
            "JOIN source_occurrence s ON s.knowledge_id = k.id "
            "WHERE s.extraction_run_id = ? GROUP BY k.knowledge_type "
            "ORDER BY k.knowledge_type",
            (run_id,),
        ).fetchall()
    }


def relationship_counts_for_run(
    connection: sqlite3.Connection, run_id: str
) -> dict[str, int]:
    """Edges this run evidenced, by relation type and origin."""
    return {
        f"{row[0]}/{row[1]}": int(row[2])
        for row in connection.execute(
            "SELECT r.relation_type, r.origin, count(DISTINCT r.id) FROM relationship r "
            "JOIN relationship_occurrence o ON o.relationship_id = r.id "
            "WHERE o.extraction_run_id = ? GROUP BY r.relation_type, r.origin "
            "ORDER BY r.relation_type",
            (run_id,),
        ).fetchall()
    }


def concept_count_for_run(connection: sqlite3.Connection, run_id: str) -> int:
    """Concepts this run evidenced."""
    row = connection.execute(
        "SELECT count(DISTINCT concept_id) FROM concept_occurrence "
        "WHERE extraction_run_id = ?",
        (run_id,),
    ).fetchone()
    return int(row[0])


# ------------------------------------------------- classification (Phase 6)
#
# Read-only. Phase 6 writes through `ConceptService` and `Repository`; nothing below
# writes. None of these queries reads `document_segment` or `document_structure`:
# classification reads stored Phase 5 output only (ADR 0026, P6-5 and P6-17).

#: The identifiers of one run's concepts: ACTIVE concepts with at least one
#: occurrence, every one of which that run recorded (ADR 0028). Phase 5 matches
#: concepts only within a run (interpretation I-A), so on Phase 5 data each concept
#: belongs to exactly one run; a concept evidenced by two runs, or by hand, belongs
#: to none. Parameters: (ACTIVE, run id, run id).
_RUN_CONCEPT_IDS = (
    "SELECT c.id FROM concept c WHERE c.lifecycle_status = ? "
    "AND EXISTS (SELECT 1 FROM concept_occurrence o "
    "            WHERE o.concept_id = c.id AND o.extraction_run_id = ?) "
    "AND NOT EXISTS (SELECT 1 FROM concept_occurrence o WHERE o.concept_id = c.id "
    "                AND (o.extraction_run_id IS NULL OR o.extraction_run_id <> ?))"
)


def _run_concept_params(run_id: str) -> tuple:
    return (LifecycleStatus.ACTIVE.value, run_id, run_id)


def concepts_of_run(connection: sqlite3.Connection, run_id: str) -> tuple[Concept, ...]:
    """The concepts of one extraction run (ADR 0028), in identifier order."""
    sql = f"SELECT * FROM concept WHERE id IN ({_RUN_CONCEPT_IDS}) ORDER BY id"
    rows = _rows(connection, sql, _run_concept_params(run_id))
    return tuple(from_row(Concept, row) for row in rows)


def active_aliases_of_run(
    connection: sqlite3.Connection, run_id: str
) -> tuple[ConceptAlias, ...]:
    """The ACTIVE curated names of one run's concepts (Part 2 section 44)."""
    sql = (
        "SELECT * FROM concept_alias WHERE lifecycle_status = ? "
        f"AND concept_id IN ({_RUN_CONCEPT_IDS}) ORDER BY concept_id, id"
    )
    params = (LifecycleStatus.ACTIVE.value,) + _run_concept_params(run_id)
    return tuple(from_row(ConceptAlias, row) for row in _rows(connection, sql, params))


def definitions_of_run(
    connection: sqlite3.Connection, run_id: str
) -> tuple[DefinitionEvidence, ...]:
    """Every stored definition the run evidenced, with the concept that owns it.

    One entry per `ACTIVE` `EXPLICIT` `DEFINED_BY` edge from a concept to an
    `ACTIVE` `DEFINITION` knowledge object that has at least one source occurrence
    in this run. Only the stored evidence of the definition is returned - its
    source occurrences - never the page text it came from (ADR 0026, P6-17).
    """
    edges = _rows(
        connection,
        "SELECT r.from_concept_id, k.id, "
        "       (SELECT count(*) FROM relationship d "
        "         WHERE d.to_knowledge_id = k.id AND d.relation_type = ? "
        "           AND d.lifecycle_status = ?) "
        "FROM relationship r JOIN knowledge_object k ON k.id = r.to_knowledge_id "
        "WHERE r.relation_type = ? AND r.origin = ? AND r.lifecycle_status = ? "
        "AND r.from_concept_id IS NOT NULL "
        "AND k.knowledge_type = ? AND k.lifecycle_status = ? "
        "AND EXISTS (SELECT 1 FROM source_occurrence s "
        "            WHERE s.knowledge_id = k.id AND s.extraction_run_id = ?) "
        "ORDER BY r.from_concept_id, k.id",
        (
            RelationType.DEFINED_BY.value, LifecycleStatus.ACTIVE.value,
            RelationType.DEFINED_BY.value, RelationshipOrigin.EXPLICIT.value,
            LifecycleStatus.ACTIVE.value,
            KnowledgeType.DEFINITION.value, LifecycleStatus.ACTIVE.value,
            run_id,
        ),
    )
    occurrences: dict[str, list[SourceOccurrence]] = {}
    for row in _rows(
        connection,
        "SELECT s.* FROM source_occurrence s "
        "JOIN knowledge_object k ON k.id = s.knowledge_id "
        "WHERE s.extraction_run_id = ? AND k.knowledge_type = ? ORDER BY s.id",
        (run_id, KnowledgeType.DEFINITION.value),
    ):
        occurrence = from_row(SourceOccurrence, row)
        occurrences.setdefault(occurrence.knowledge_id, []).append(occurrence)
    return tuple(
        DefinitionEvidence(
            concept_id=str(concept_id),
            knowledge_id=str(knowledge_id),
            defined_by_edges=int(count),
            occurrences=tuple(occurrences.get(str(knowledge_id), ())),
        )
        for concept_id, knowledge_id, count in edges
    )


def active_edges_between(
    connection: sqlite3.Connection,
    *,
    relation_type: RelationType,
    concept_a: str,
    concept_b: str,
) -> tuple[Relationship, ...]:
    """ACTIVE edges of one type between two concepts, in **either** direction."""
    sql = (
        f"SELECT {_REL_COLUMNS} FROM relationship r "
        "WHERE r.relation_type = ? AND r.lifecycle_status = ? AND ("
        "  (r.from_concept_id = ? AND r.to_concept_id = ?) OR "
        "  (r.from_concept_id = ? AND r.to_concept_id = ?)) "
        "ORDER BY r.id"
    )
    params = (
        relation_type.value, LifecycleStatus.ACTIVE.value,
        concept_a, concept_b, concept_b, concept_a,
    )
    return tuple(from_row(Relationship, row) for row in _rows(connection, sql, params))


def inference_is_recorded(
    connection: sqlite3.Connection,
    *,
    relationship_id: str,
    rule: str,
    rule_version: str,
    basis_occurrence_id: str | None,
    matched_text: str | None,
) -> bool:
    """Whether exactly this basis is already stored (the ADR 0027 idempotence key)."""
    row = connection.execute(
        "SELECT 1 FROM relationship_inference WHERE relationship_id = ? AND rule = ? "
        "AND rule_version = ? AND COALESCE(basis_occurrence_id, '') = ? "
        "AND COALESCE(matched_text, '') = ?",
        (relationship_id, rule, rule_version, basis_occurrence_id or "", matched_text or ""),
    ).fetchone()
    return row is not None


def inferences_for_relationship(
    connection: sqlite3.Connection, relationship_id: str
) -> tuple:
    """Every recorded basis of one edge (ADR 0027), oldest first."""
    from app.models.entities import RelationshipInference

    rows = _rows(
        connection,
        "SELECT * FROM relationship_inference WHERE relationship_id = ? "
        "ORDER BY created_at, id",
        (relationship_id,),
    )
    return tuple(from_row(RelationshipInference, row) for row in rows)


def edges_among_run_concepts(
    connection: sqlite3.Connection,
    run_id: str,
    relation_types: tuple[RelationType, ...],
) -> tuple[Relationship, ...]:
    """ACTIVE concept-to-concept edges of these types whose both ends are this run's.

    The input of views V1 and V2 (ADR 0026). Whatever is stored is returned with
    its real relation type and origin; nothing is relabelled or inferred here.
    """
    if not relation_types:
        return ()
    marks = ", ".join("?" for _ in relation_types)
    sql = (
        f"SELECT {_REL_COLUMNS} FROM relationship r "
        f"WHERE r.relation_type IN ({marks}) AND r.lifecycle_status = ? "
        f"AND r.from_concept_id IN ({_RUN_CONCEPT_IDS}) "
        f"AND r.to_concept_id IN ({_RUN_CONCEPT_IDS}) "
        "ORDER BY r.relation_type, r.from_concept_id, r.to_concept_id, r.id"
    )
    params = (
        tuple(t.value for t in relation_types)
        + (LifecycleStatus.ACTIVE.value,)
        + _run_concept_params(run_id)
        + _run_concept_params(run_id)
    )
    return tuple(from_row(Relationship, row) for row in _rows(connection, sql, params))


def concepts_defined_by(connection: sqlite3.Connection, knowledge_id: str) -> tuple[str, ...]:
    """The concepts whose ACTIVE `DEFINED_BY` edge points at this knowledge object.

    How a rule R1 basis tells which concept's definition contained the mention
    (ADR 0027): the basis names a source occurrence, the occurrence names its
    knowledge object, and this names the concept that object defines.
    """
    rows = _rows(
        connection,
        "SELECT from_concept_id FROM relationship WHERE to_knowledge_id = ? "
        "AND relation_type = ? AND lifecycle_status = ? "
        "AND from_concept_id IS NOT NULL ORDER BY from_concept_id",
        (knowledge_id, RelationType.DEFINED_BY.value, LifecycleStatus.ACTIVE.value),
    )
    return tuple(str(row[0]) for row in rows)


# ------------------------------------------------- deduplication (Phase 8)
#
# Read-only. Stage 15, `merge`, `review` and `edition` (ADRs 0032-0034) write through
# `Repository` and `ConceptService`; nothing below writes. Every query has a fixed
# order, so nothing built from it depends on SQLite's row order.


def active_knowledge_statements(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str], ...]:
    """(id, knowledge type, statement) of every ACTIVE knowledge object, by id."""
    rows = _rows(
        connection,
        "SELECT id, knowledge_type, statement FROM knowledge_object "
        "WHERE lifecycle_status = ? ORDER BY id",
        (LifecycleStatus.ACTIVE.value,),
    )
    return tuple((str(r[0]), str(r[1]), str(r[2])) for r in rows)


def active_knowledge_locations(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, int, int, int], ...]:
    """(knowledge id, document, page, char start, char end) of every located
    occurrence of an ACTIVE knowledge object - the location ADR 0019 stores.

    Occurrences without a page or a span are left out: a location that is not known
    cannot be "the same location" as anything (ADR 0033, P8-12).
    """
    rows = _rows(
        connection,
        "SELECT s.knowledge_id, s.document_id, s.page_number, s.char_start, s.char_end "
        "FROM source_occurrence s JOIN knowledge_object k ON k.id = s.knowledge_id "
        "WHERE k.lifecycle_status = ? AND s.page_number IS NOT NULL "
        "AND s.char_start IS NOT NULL AND s.char_end IS NOT NULL ORDER BY s.id",
        (LifecycleStatus.ACTIVE.value,),
    )
    return tuple((str(r[0]), str(r[1]), int(r[2]), int(r[3]), int(r[4])) for r in rows)


def active_concept_links(connection: sqlite3.Connection) -> tuple[tuple[str, str, str], ...]:
    """(knowledge id, concept id, relation type) of every ACTIVE `DEFINED_BY` or
    `HAS_PROPERTY` edge from an ACTIVE concept to an ACTIVE knowledge object.

    A knowledge object has a concept only through such an edge (D-21).
    """
    rows = _rows(
        connection,
        "SELECT r.to_knowledge_id, r.from_concept_id, r.relation_type FROM relationship r "
        "JOIN concept c ON c.id = r.from_concept_id "
        "JOIN knowledge_object k ON k.id = r.to_knowledge_id "
        "WHERE r.relation_type IN (?, ?) AND r.lifecycle_status = ? "
        "AND c.lifecycle_status = ? AND k.lifecycle_status = ? ORDER BY r.id",
        (
            RelationType.DEFINED_BY.value, RelationType.HAS_PROPERTY.value,
            LifecycleStatus.ACTIVE.value, LifecycleStatus.ACTIVE.value,
            LifecycleStatus.ACTIVE.value,
        ),
    )
    return tuple((str(r[0]), str(r[1]), str(r[2])) for r in rows)


def active_concept_aliases(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    """(concept id, stored normalised alias) of every ACTIVE alias of an ACTIVE concept."""
    rows = _rows(
        connection,
        "SELECT a.concept_id, a.normalized_alias FROM concept_alias a "
        "JOIN concept c ON c.id = a.concept_id "
        "WHERE a.lifecycle_status = ? AND c.lifecycle_status = ? ORDER BY a.concept_id, a.id",
        (LifecycleStatus.ACTIVE.value, LifecycleStatus.ACTIVE.value),
    )
    return tuple((str(r[0]), str(r[1])) for r in rows)


def active_relationship_to_knowledge(
    connection: sqlite3.Connection,
    *,
    relation_type: RelationType,
    from_concept_id: str,
    to_knowledge_id: str,
) -> Relationship | None:
    """The one ACTIVE edge of this type from a concept to a knowledge object (D-24)."""
    row = connection.execute(
        "SELECT * FROM relationship WHERE relation_type = ? AND from_concept_id = ? "
        "AND to_knowledge_id = ? AND lifecycle_status = ?",
        (relation_type.value, from_concept_id, to_knowledge_id, LifecycleStatus.ACTIVE.value),
    ).fetchone()
    return None if row is None else from_row(Relationship, row)


def concept_scopes(connection: sqlite3.Connection) -> tuple[tuple[str, str | None, str], ...]:
    """(concept id, extraction run, document) of every concept occurrence, distinct.

    Which runs and documents evidence each concept: how P8-16 tells "a concept of
    another run" and "from the same document" (ADR 0033).
    """
    rows = _rows(
        connection,
        "SELECT DISTINCT concept_id, extraction_run_id, document_id FROM concept_occurrence "
        "ORDER BY concept_id, extraction_run_id, document_id",
    )
    return tuple((str(r[0]), None if r[1] is None else str(r[1]), str(r[2])) for r in rows)


def stated_equivalences(connection: sqlite3.Connection) -> tuple[Relationship, ...]:
    """ACTIVE `EXPLICIT` `EQUIVALENT_TO` edges between two ACTIVE concepts, by id.

    A source stating that two concepts are equivalent - section 44's different-name
    case (ADR 0033, P8-16).
    """
    sql = (
        f"SELECT {_REL_COLUMNS} FROM relationship r "
        "JOIN concept a ON a.id = r.from_concept_id JOIN concept b ON b.id = r.to_concept_id "
        "WHERE r.relation_type = ? AND r.origin = ? AND r.lifecycle_status = ? "
        "AND a.lifecycle_status = ? AND b.lifecycle_status = ? ORDER BY r.id"
    )
    params = (
        RelationType.EQUIVALENT_TO.value, RelationshipOrigin.EXPLICIT.value,
        LifecycleStatus.ACTIVE.value, LifecycleStatus.ACTIVE.value, LifecycleStatus.ACTIVE.value,
    )
    return tuple(from_row(Relationship, row) for row in _rows(connection, sql, params))


def compared_knowledge_pairs(connection: sqlite3.Connection) -> frozenset[frozenset[str]]:
    """Every pair of knowledge objects an assessment record already joins."""
    rows = _rows(
        connection,
        "SELECT canonical_knowledge_id, other_knowledge_id FROM knowledge_equivalence "
        "WHERE other_knowledge_id IS NOT NULL",
    )
    return frozenset(frozenset((str(r[0]), str(r[1]))) for r in rows)


def recorded_concept_pairs(connection: sqlite3.Connection) -> frozenset[tuple[str, str]]:
    """Every (concept a, concept b) pair a concept-equivalence record already holds."""
    rows = _rows(connection, "SELECT concept_a_id, concept_b_id FROM concept_equivalence")
    return frozenset((str(r[0]), str(r[1])) for r in rows)


def companions_of_knowledge(connection: sqlite3.Connection, knowledge_id: str) -> tuple:
    """The equation, variable, rule and procedure rows of one knowledge object (D-37)."""
    from app.models.entities import Procedure, Rule, Variable

    found: list = []
    for entity_type in (Equation, Variable, Rule, Procedure):
        rows = _rows(
            connection,
            f"SELECT * FROM {entity_type.TABLE} WHERE knowledge_id = ? ORDER BY id",
            (knowledge_id,),
        )
        found.extend(from_row(entity_type, row) for row in rows)
    return tuple(found)


def equivalences_of_knowledge(connection: sqlite3.Connection, knowledge_id: str) -> tuple:
    """Every assessment record naming this knowledge object on either side, by id."""
    from app.models.entities import KnowledgeEquivalence

    rows = _rows(
        connection,
        "SELECT * FROM knowledge_equivalence "
        "WHERE canonical_knowledge_id = ? OR other_knowledge_id = ? ORDER BY id",
        (knowledge_id, knowledge_id),
    )
    return tuple(from_row(KnowledgeEquivalence, row) for row in rows)


def conflicts_of_knowledge(connection: sqlite3.Connection, knowledge_id: str) -> tuple:
    """Every conflict in which this knowledge object is claim A or claim B, by id."""
    from app.models.entities import Conflict

    rows = _rows(
        connection,
        "SELECT * FROM conflict WHERE claim_a_id = ? OR claim_b_id = ? ORDER BY id",
        (knowledge_id, knowledge_id),
    )
    return tuple(from_row(Conflict, row) for row in rows)


def occurrences_of_knowledge(
    connection: sqlite3.Connection, knowledge_id: str
) -> tuple[SourceOccurrence, ...]:
    """Every source occurrence of one knowledge object, by id."""
    rows = _rows(
        connection,
        "SELECT * FROM source_occurrence WHERE knowledge_id = ? ORDER BY id",
        (knowledge_id,),
    )
    return tuple(from_row(SourceOccurrence, row) for row in rows)


def document_versions_by_hash(connection: sqlite3.Connection, file_hash: str) -> tuple:
    """Every declared edition row carrying these file bytes (ADR 0034, P8-27)."""
    from app.models.entities import DocumentVersion

    rows = _rows(
        connection,
        "SELECT * FROM document_version WHERE file_hash = ? ORDER BY id",
        (file_hash,),
    )
    return tuple(from_row(DocumentVersion, row) for row in rows)


def document_versions_of(connection: sqlite3.Connection, document_id: str) -> tuple:
    """Every declared edition row filed under one work document, by id."""
    from app.models.entities import DocumentVersion

    rows = _rows(
        connection,
        "SELECT * FROM document_version WHERE document_id = ? ORDER BY id",
        (document_id,),
    )
    return tuple(from_row(DocumentVersion, row) for row in rows)


# ------------------------------------------------- query engine (Phase 9)
#
# Read-only (ADRs 0035-0037). Nothing below writes, infers, merges or resolves:
# stored rows come back as stored, and choosing among them is the caller's. Order
# is by stored attributes and then by the numeric identifier counter (ADR 0036
# P9-22, ADR 0006) - never by the identifier string, which breaks above the
# fixed-width ceiling.


def _counter(column: str) -> str:
    """SQL for the numeric counter of the identifier in `column` (ADR 0006).

    Every identifier column's CHECK guarantees only digits after the first hyphen,
    so the cast is exact.
    """
    return f"CAST(substr({column}, instr({column}, '-') + 1) AS INTEGER)"


def equivalences_of_concept(
    connection: sqlite3.Connection, concept_id: str
) -> tuple[ConceptEquivalence, ...]:
    """Every concept-equivalence record naming this concept on either side (P9-17).

    Returned as stored, whatever the status or basis. A record says what is known
    about a pair and never makes the two one concept (P8-16); an empty result means
    no record is stored, not that no equivalence exists.
    """
    rows = _rows(
        connection,
        "SELECT * FROM concept_equivalence WHERE concept_a_id = ? OR concept_b_id = ? "
        f"ORDER BY {_counter('id')}",
        (concept_id, concept_id),
    )
    return tuple(from_row(ConceptEquivalence, row) for row in rows)


def has_property_edges_to(
    connection: sqlite3.Connection, knowledge_id: str, *, active_only: bool = True
) -> tuple[Relationship, ...]:
    """The `HAS_PROPERTY` edges pointing at this knowledge object: who owns it.

    `HAS_PROPERTY` is stored one way only, owner -> property (ADR 0034 P8-24; D-23),
    so the owners *of* a property are the edges pointing AT it - read backwards, as
    `prerequisites_of_concept` reads `PREREQUISITE_OF`. When stage 15 links a
    property sentence to a stored object, the run's owner gets its own edge to it,
    so one object can have several owners; every edge is returned, none is chosen.
    """
    sql = (
        f"SELECT {_REL_COLUMNS} FROM relationship r "
        "WHERE r.to_knowledge_id = ? AND r.relation_type = ?"
    )
    params: tuple = (knowledge_id, RelationType.HAS_PROPERTY.value)
    if active_only:
        sql += " AND r.lifecycle_status = ?"
        params += (LifecycleStatus.ACTIVE.value,)
    sql += f" ORDER BY {_counter('r.id')}"
    return tuple(from_row(Relationship, row) for row in _rows(connection, sql, params))


def segments_on_page(
    connection: sqlite3.Connection, document_id: str, page_number: int
) -> tuple[DocumentSegment, ...]:
    """The stored text of one page of a document, in reading order (P9-21).

    The page's stored segments only - the original PDF is never opened. Empty when
    nothing is stored for that page.
    """
    rows = _rows(
        connection,
        "SELECT * FROM document_segment WHERE document_id = ? AND page_number = ? "
        f"ORDER BY ordinal, {_counter('id')}",
        (document_id, page_number),
    )
    return tuple(from_row(DocumentSegment, row) for row in rows)


def evidence_on_page(
    connection: sqlite3.Connection, document_id: str, page_number: int
) -> tuple[dict, ...]:
    """Every knowledge, concept and relationship occurrence located on one page (P9-21).

    Through the `evidence` view, like `evidence_for` and for the same reason: the
    three subjects share no entity type. In text order - the segment's ordinal, then
    the character span - with occurrences whose segment or span is not recorded
    after those whose is, then by subject kind and numeric counter. An occurrence
    with no page recorded is on no page and is never returned.
    """
    sql = (
        "SELECT e.* FROM evidence e "
        "LEFT JOIN document_segment g ON g.id = e.segment_id "
        "WHERE e.document_id = ? AND e.page_number = ? "
        "ORDER BY g.ordinal NULLS LAST, e.char_start NULLS LAST, e.char_end NULLS LAST, "
        f"e.subject_kind, {_counter('e.id')}"
    )
    return tuple(dict(row) for row in _rows(connection, sql, (document_id, page_number)))


def evidence_context(
    connection: sqlite3.Connection, evidence_rows: Iterable[Mapping[str, object]]
) -> EvidenceContext:
    """The source, document, run and declared-edition rows these evidence rows name.

    `evidence_rows` are rows of the `evidence` view (`evidence_for`,
    `evidence_on_page`). Declared editions are the `document_version` rows carrying
    a named document's file hash - how `edition` files them (ADR 0034 P8-27; ADR
    0036 P9-18). A row's own `document_version_id` stays in the row as stored; an
    occurrence with no run names none.
    """
    rows = tuple(evidence_rows)
    documents = _rows_by_id(connection, Document, {row["document_id"] for row in rows})
    hashes = sorted({document.file_hash for document in documents})
    editions: tuple = ()
    if hashes:
        marks = ", ".join("?" for _ in hashes)
        editions = tuple(
            from_row(DocumentVersion, row)
            for row in _rows(
                connection,
                f"SELECT * FROM document_version WHERE file_hash IN ({marks}) "
                f"ORDER BY {_counter('id')}",
                tuple(hashes),
            )
        )
    return EvidenceContext(
        sources=_rows_by_id(connection, Source, {row["source_id"] for row in rows}),
        documents=documents,
        runs=_rows_by_id(
            connection, ExtractionRun, {row["extraction_run_id"] for row in rows}
        ),
        editions=editions,
    )


def all_documents(connection: sqlite3.Connection) -> tuple[Document, ...]:
    """Every stored document, by numeric counter - for the "fully ingested" report (P9-10)."""
    rows = _rows(connection, f"SELECT * FROM document ORDER BY {_counter('id')}")
    return tuple(from_row(Document, row) for row in rows)


def knowledge_types_linked_to_concepts(
    connection: sqlite3.Connection,
) -> frozenset[KnowledgeType]:
    """The knowledge types of every object an ACTIVE edge joins to a concept.

    How a concept result tells "nothing is stored for this concept" from "this kind
    of knowledge has no stored concept link at all" (ADR 0036 P9-13; D1, ADR 0035
    P9-3). Either direction counts; the object's own lifecycle does not matter.
    """
    rows = _rows(
        connection,
        "SELECT DISTINCT k.knowledge_type FROM relationship r JOIN knowledge_object k "
        "ON (k.id = r.to_knowledge_id AND r.from_concept_id IS NOT NULL) "
        "OR (k.id = r.from_knowledge_id AND r.to_concept_id IS NOT NULL) "
        "WHERE r.lifecycle_status = ?",
        (LifecycleStatus.ACTIVE.value,),
    )
    return frozenset(KnowledgeType(str(row[0])) for row in rows)


def connection_is_read_only(connection: sqlite3.Connection) -> bool:
    """Whether this connection refuses writes (`query_only`, ADR 0031 I7-B).

    Read from the connection rather than assumed, so a result can state it.
    """
    return bool(connection.execute("PRAGMA query_only").fetchone()[0])


def _rows_by_id(
    connection: sqlite3.Connection, entity_type: type, identifiers: Iterable[object]
) -> tuple:
    """The stored rows of one entity type with these identifiers, by numeric counter.

    `None` names nothing and is ignored; an empty set runs no query.
    """
    wanted = sorted(str(identifier) for identifier in identifiers if identifier is not None)
    if not wanted:
        return ()
    marks = ", ".join("?" for _ in wanted)
    rows = _rows(
        connection,
        f"SELECT * FROM {entity_type.TABLE} WHERE id IN ({marks}) "
        f"ORDER BY {_counter('id')}",
        tuple(wanted),
    )
    return tuple(from_row(entity_type, row) for row in rows)


# ----------------------------------------------- dependency reasoning (Phase 10)
#
# Phase 10 reasons over stored `REQUIRES` and `DEPENDS_ON` relationships between two
# concepts, read in their stored direction: `X REQUIRES A` and `X DEPENDS_ON A` both
# mean X requires A (ADR 0038 P10-3). These queries return such relationships exactly
# as stored and decide nothing: scope, authorisation, node states and derivations
# belong to `app.reasoning` (ADR 0039). A closure is computed as a set of concepts and
# returned as the relationships *from* them, so each concept's complete stored
# requirement set is present (ADR 0038 P10-4); a set cut short would let a node be
# derived from part of its requirements (ADR 0039 P10-20).

#: The relation types that form a Phase 10 dependency (ADR 0038 P10-3). `PREREQUISITE_OF`
#: is study order, not a derivation dependency, and is deliberately absent.
DEPENDENCY_TYPES: tuple[RelationType, ...] = (RelationType.REQUIRES, RelationType.DEPENDS_ON)

#: An ACTIVE dependency relationship between two concepts, for relationship alias `r`.
_DEPENDENCY = (
    "r.relation_type IN (?, ?) AND r.lifecycle_status = ? "
    "AND r.from_concept_id IS NOT NULL AND r.to_concept_id IS NOT NULL"
)


def _dependency_params() -> tuple:
    return (*(kind.value for kind in DEPENDENCY_TYPES), LifecycleStatus.ACTIVE.value)


def _dependency_closure(
    connection: sqlite3.Connection, concept_ids: Iterable[str], *, step: str
) -> tuple[Relationship, ...]:
    """The dependency relationships from every concept `step` reaches from the seeds.

    `step` is the recursive term's join: forward it follows `from -> to`, backward
    `to -> from`. `UNION`, not `UNION ALL`, is what makes a cycle terminate: a concept
    already reached is never added again. The seeds belong to the closure themselves.
    """
    seeds = sorted({str(identifier) for identifier in concept_ids})
    if not seeds:
        return ()
    marks = ", ".join("(?)" for _ in seeds)
    sql = f"""
        WITH RECURSIVE reach(id) AS (
            VALUES {marks}
            UNION
            {step}
             WHERE {_DEPENDENCY}
        )
        SELECT {_REL_COLUMNS} FROM relationship r
         WHERE r.from_concept_id IN (SELECT id FROM reach) AND {_DEPENDENCY}
         ORDER BY {_counter('r.id')}
    """
    params = (*seeds, *_dependency_params(), *_dependency_params())
    return tuple(from_row(Relationship, row) for row in _rows(connection, sql, params))


def dependency_closure(
    connection: sqlite3.Connection, concept_ids: Iterable[str]
) -> tuple[Relationship, ...]:
    """Every dependency relationship reachable forward from these concepts.

    Backward reasoning's graph (ADR 0039 P10-14): the given concepts, what they
    require, what that requires, and so on - and every ACTIVE dependency relationship
    *from* each concept reached, so each requirement set is complete. Ordered by the
    numeric identifier counter; an empty `concept_ids` returns an empty tuple.
    """
    return _dependency_closure(
        connection,
        concept_ids,
        step="SELECT r.to_concept_id FROM relationship r JOIN reach ON r.from_concept_id = reach.id",
    )


def dependent_closure(
    connection: sqlite3.Connection, concept_ids: Iterable[str]
) -> tuple[Relationship, ...]:
    """Every dependency relationship of the concepts that depend on these, transitively.

    Forward reasoning's graph (ADR 0039 P10-14): the given concepts, the concepts that
    require them, the concepts that require those, and so on - and every ACTIVE
    dependency relationship *from* each concept reached, including requirements that
    lie outside the closure. A concept outside the closure depends on none of the
    given concepts, so it cannot be derived from them; its presence as a requirement is
    what keeps a dependent from being derived from part of its set. Ordered by the
    numeric identifier counter; an empty `concept_ids` returns an empty tuple.
    """
    return _dependency_closure(
        connection,
        concept_ids,
        step="SELECT r.from_concept_id FROM relationship r JOIN reach ON r.to_concept_id = reach.id",
    )


def relationships_touching_knowledge(
    connection: sqlite3.Connection,
    knowledge_id: str,
    relation_type: RelationType,
    *,
    active_only: bool = True,
) -> tuple[Relationship, ...]:
    """Every stored relationship of one type with this knowledge object at either end.

    Phase 10 reads the `CONTRADICTS` relationships touching a stored knowledge item a
    request admits (ADR 0039 P10-19). Returned as stored; ordered by the numeric
    identifier counter.
    """
    sql = (
        f"SELECT {_REL_COLUMNS} FROM relationship r "
        "WHERE (r.from_knowledge_id = ? OR r.to_knowledge_id = ?) AND r.relation_type = ?"
    )
    params: tuple = (knowledge_id, knowledge_id, relation_type.value)
    if active_only:
        sql += " AND r.lifecycle_status = ?"
        params += (LifecycleStatus.ACTIVE.value,)
    sql += f" ORDER BY {_counter('r.id')}"
    return tuple(from_row(Relationship, row) for row in _rows(connection, sql, params))


# -------------------------------------------------------- Phase 11: calculation


def equations_of_knowledge(
    connection: sqlite3.Connection, knowledge_id: str
) -> tuple[Equation, ...]:
    """The stored `equation` row(s) of one knowledge object, by numeric identifier counter.

    Phase 11 reads the one stored equation a calculation request admits (ADR 0043 P11-27;
    N2). Extraction writes exactly one row per `EQUATION` knowledge object; every row is
    returned so that the caller can refuse zero or several rather than choose one.
    Returned as stored, whatever its lifecycle: the caller applies P9-23. Reads only.
    """
    sql = f"SELECT * FROM equation WHERE knowledge_id = ? ORDER BY {_counter('id')}"
    return tuple(from_row(Equation, row) for row in _rows(connection, sql, (knowledge_id,)))


# ---------------------------------------------------------- Phase 12: provenance


def evidence_row(connection: sqlite3.Connection, evidence_id: str) -> dict | None:
    """One row of the `evidence` view by its occurrence identifier, or None.

    Phase 12 reads a single occurrence (`S-`, `CO-`, `RO-`), and an INFERRED edge's
    basis occurrence (ADR 0027), exactly as stored (ADR 0044 P12-6, P12-7). Reads only.
    """
    row = connection.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
    return None if row is None else dict(row)


# ------------------------------------------------- Phase 16: procedural memory


def all_procedures(connection: sqlite3.Connection) -> tuple:
    """Every stored procedure, by numeric counter (ADR 0048 P16-2; P9-22).

    Returned as stored, whatever its lifecycle: the caller applies P9-23. Reads only.
    """
    from app.models.entities import Procedure

    sql = f"SELECT * FROM procedure ORDER BY {_counter('id')}"
    return tuple(from_row(Procedure, row) for row in _rows(connection, sql))


def verifications_of(connection: sqlite3.Connection, subject_id: str) -> tuple:
    """Every verification recorded for one subject, oldest first (ADR 0048 P16-8).

    Ordered by the recorded time, then by numeric counter (P9-22). Reads only.
    """
    from app.models.entities import Verification

    sql = f"SELECT * FROM verification WHERE subject_id = ? ORDER BY created_at, {_counter('id')}"
    return tuple(from_row(Verification, row) for row in _rows(connection, sql, (subject_id,)))


# ------------------------------------------ Phase 17: application documentation


def memory_items(connection: sqlite3.Connection, category: str, key_prefix: str) -> tuple:
    """The memory items of one store whose key begins with `key_prefix`, by numeric
    counter (ADR 0049 P17-2: the user's manual declarations). Reads only."""
    from app.models.entities import MemoryItem

    sql = f"SELECT * FROM memory_item WHERE category = ? AND substr(key, 1, ?) = ? ORDER BY {_counter('id')}"
    return tuple(from_row(MemoryItem, row) for row in _rows(connection, sql, (category, len(key_prefix), key_prefix)))


def occurrences_by_method(connection: sqlite3.Connection, document_id: str, method_prefix: str) -> tuple:
    """The source occurrences in one document whose extraction method begins with
    `method_prefix`, in page and span order (ADR 0049 P17-3). Reads only."""
    sql = ("SELECT * FROM source_occurrence WHERE document_id = ? AND substr(extraction_method, 1, ?) = ? "
           f"ORDER BY page_number, char_start, {_counter('id')}")
    return tuple(from_row(SourceOccurrence, row)
                 for row in _rows(connection, sql, (document_id, len(method_prefix), method_prefix)))


def procedures_in_document(connection: sqlite3.Connection, document_id: str) -> tuple:
    """Every procedure whose knowledge object has a source occurrence in the document, by
    numeric counter (ADR 0049 P17-9). Returned as stored, whatever its lifecycle. Reads only."""
    from app.models.entities import Procedure

    sql = ("SELECT * FROM procedure WHERE knowledge_id IN "
           f"(SELECT knowledge_id FROM source_occurrence WHERE document_id = ?) ORDER BY {_counter('id')}")
    return tuple(from_row(Procedure, row) for row in _rows(connection, sql, (document_id,)))


# --------------------------------------------------- Phase 18: controlled Internet


def external_claims(connection: sqlite3.Connection, canonical_name: str, method: str) -> tuple:
    """ACTIVE knowledge objects with this canonical name, each with its occurrence recorded
    by `method`, oldest retrieval first (ADR 0050 P18-9: the cache). Reads only."""
    sql = ("SELECT o.* FROM source_occurrence o JOIN knowledge_object k ON k.id = o.knowledge_id "
           "WHERE k.canonical_name = ? AND k.lifecycle_status = 'ACTIVE' AND o.extraction_method = ? "
           f"ORDER BY o.extraction_timestamp, {_counter('o.id')}")
    pairs = []
    for row in _rows(connection, sql, (canonical_name, method)):
        occurrence = from_row(SourceOccurrence, row)
        knowledge = connection.execute("SELECT * FROM knowledge_object WHERE id = ?",
                                       (occurrence.knowledge_id,)).fetchone()
        pairs.append((from_row(KnowledgeObject, knowledge), occurrence))
    return tuple(pairs)
