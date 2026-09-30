-- RUDRA knowledge database, schema version 6 (Phase 8 — knowledge deduplication).
--
-- Decisions implemented here, approved 2026-09-23:
--   P8-22  ADR 0034  a knowledge-equivalence table, a concept-equivalence table,
--                    and `relationship` rebuilt so its relation-type CHECK admits
--                    HAS_PROPERTY. Nothing else in the schema changes.
--   P8-24  ADR 0034  HAS_PROPERTY: concept -> PROPERTY knowledge object
--   P8-18  ADR 0033  knowledge_object.normalized_hash stays NULL and
--                    ux_knowledge_object_normalized is not touched
--
-- NOT changed: knowledge_object, document, document_version, conflict, the three
-- occurrence tables, the `evidence` view, extraction_run, extraction_issue (the two
-- stage-15 issue types are already in its CHECK) and concept_alias.
--
-- No id_sequence statement appears here, deliberately. Counters come from the live
-- EntityKind registry via _seed() on every migration (A-8, ADR 0013), so adding
-- KNOWLEDGE_EQUIVALENCE ("KE") and CONCEPT_EQUIVALENCE ("CE") is sufficient.
--
-- FOREIGN KEYS DURING THIS MIGRATION. `relationship_occurrence` and
-- `relationship_inference` reference `relationship` ON DELETE RESTRICT, so dropping
-- the old table with enforcement on is refused, and deferring enforcement only moves
-- the refusal to COMMIT (both measured 2026-09-23 on a scratch database). The
-- migrator therefore applies every migration with enforcement off and runs
-- `PRAGMA foreign_key_check` before committing - SQLite's documented procedure for
-- a table rebuild. Any violation rolls the whole migration back.

-- ---------------------------------------------------- knowledge_equivalence
--
-- Part 3 sections 72-73 and 79: each comparison's outcome, and the review state of
-- an uncertain case, persists. One row per comparison (ADR 0033 P8-12 ... P8-19):
--
--   canonical_knowledge_id  the existing (canonical) knowledge object
--   other_knowledge_id      the new object of a non-exact comparison, or the object
--                           `merge` superseded - the pointer (no merged_into column)
--   linked_occurrence_id    the new source occurrence of an exact duplicate linked
--                           at extraction time, when no new object exists
--   extraction_run_id       the run of the comparison; NULL for a `merge` record
--   conflict_id             the conflict a CONTRADICTORY outcome created
--
-- Exactly one of other_knowledge_id / linked_occurrence_id is set. That rule lives
-- in triggers, not a table CHECK, so a later kind of "other side" is an ADD COLUMN
-- plus two trigger rewrites rather than a rebuild (the ADR 0009 / 0027 pattern).
--
-- No score and no evidence columns (ADR 0033 P8-13; evidence stays in the
-- occurrence tables). `rule` and `rule_version` are free text, as in
-- relationship_inference. Every reference is ON DELETE RESTRICT.
CREATE TABLE knowledge_equivalence (
    id                     TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at             TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at             TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    canonical_knowledge_id TEXT NOT NULL REFERENCES knowledge_object(id)  ON DELETE RESTRICT,
    other_knowledge_id     TEXT          REFERENCES knowledge_object(id)  ON DELETE RESTRICT,
    linked_occurrence_id   TEXT          REFERENCES source_occurrence(id) ON DELETE RESTRICT,
    extraction_run_id      TEXT          REFERENCES extraction_run(id)    ON DELETE RESTRICT,
    -- Sections 72-73 in full: seven outcomes plus POSSIBLE_DUPLICATE. Phase 8 emits
    -- three (EXACT_DUPLICATE, POSSIBLE_DUPLICATE, CONTRADICTORY); the vocabulary is
    -- complete because widening a CHECK means rebuilding the table.
    outcome                TEXT NOT NULL CHECK (outcome IN ('EXACT_DUPLICATE','SEMANTICALLY_EQUIVALENT','PARTIALLY_OVERLAPPING','RELATED_BUT_DISTINCT','CONTEXT_DEPENDENT','CONTRADICTORY','UNKNOWN','POSSIBLE_DUPLICATE')),
    rule                   TEXT NOT NULL CHECK (length(trim(rule)) > 0),
    rule_version           TEXT NOT NULL CHECK (length(trim(rule_version)) > 0),
    conflict_id            TEXT          REFERENCES conflict(id)          ON DELETE RESTRICT,
    -- A conflict belongs to a CONTRADICTORY outcome and to nothing else (P8-15).
    CHECK ((outcome = 'CONTRADICTORY') = (conflict_id IS NOT NULL)),
    -- An occurrence is linked only as an exact duplicate (P8-12).
    CHECK (linked_occurrence_id IS NULL OR outcome = 'EXACT_DUPLICATE'),
    CHECK (other_knowledge_id IS NULL OR other_knowledge_id <> canonical_knowledge_id)
) STRICT;

CREATE INDEX ix_knowledge_equivalence_canonical  ON knowledge_equivalence(canonical_knowledge_id);
CREATE INDEX ix_knowledge_equivalence_other      ON knowledge_equivalence(other_knowledge_id);
CREATE INDEX ix_knowledge_equivalence_occurrence ON knowledge_equivalence(linked_occurrence_id);
CREATE INDEX ix_knowledge_equivalence_run        ON knowledge_equivalence(extraction_run_id);
CREATE INDEX ix_knowledge_equivalence_conflict   ON knowledge_equivalence(conflict_id);

-- One outcome per comparison under one rule version. COALESCE collapses the two
-- "other side" columns, exactly one of which is set; an identifier is never empty
-- and K- and S- identifiers cannot collide, so '' never matches a real value
-- (ARCHITECTURE.md section 6.4 rule 7 - NULLs are distinct in a unique index).
CREATE UNIQUE INDEX ux_knowledge_equivalence_comparison ON knowledge_equivalence(
    canonical_knowledge_id,
    COALESCE(other_knowledge_id, linked_occurrence_id, ''),
    rule,
    rule_version
);

CREATE TRIGGER trg_knowledge_equivalence_other_insert
BEFORE INSERT ON knowledge_equivalence
WHEN ((NEW.other_knowledge_id IS NOT NULL) + (NEW.linked_occurrence_id IS NOT NULL)) <> 1
BEGIN
    SELECT RAISE(ABORT, 'knowledge_equivalence: exactly one of other_knowledge_id and linked_occurrence_id must be set');
END;

CREATE TRIGGER trg_knowledge_equivalence_other_update
BEFORE UPDATE ON knowledge_equivalence
WHEN ((NEW.other_knowledge_id IS NOT NULL) + (NEW.linked_occurrence_id IS NOT NULL)) <> 1
BEGIN
    SELECT RAISE(ABORT, 'knowledge_equivalence: exactly one of other_knowledge_id and linked_occurrence_id must be set');
END;

-- ------------------------------------------------------- concept_equivalence
--
-- Part 2 section 44: "Use statuses such as CONFIRMED_EQUIVALENT / POSSIBLE_EQUIVALENT
-- / NOT_EQUIVALENT / UNKNOWN" and "do not automatically merge concepts when the
-- evidence is insufficient". One record per UNORDERED pair, stored in canonical
-- order (concept_a_id < concept_b_id, the P6-4b precedent). Concepts are never
-- merged; this row records what is known about the pair.
CREATE TABLE concept_equivalence (
    id                TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at        TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at        TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    concept_a_id      TEXT NOT NULL REFERENCES concept(id)        ON DELETE RESTRICT,
    concept_b_id      TEXT NOT NULL REFERENCES concept(id)        ON DELETE RESTRICT,
    status            TEXT NOT NULL CHECK (status IN ('CONFIRMED_EQUIVALENT','POSSIBLE_EQUIVALENT','NOT_EQUIVALENT','UNKNOWN')),
    -- Which evidence produced the record (ADR 0033 P8-16).
    basis             TEXT NOT NULL CHECK (basis IN ('SHARED_NAME_SAME_DOCUMENT','SHARED_NAME_OTHER_DOCUMENT','STATED_EQUIVALENT_TO')),
    -- The ACTIVE normalised alias the two concepts share (shared-name bases).
    shared_name       TEXT CHECK (shared_name IS NULL OR length(trim(shared_name)) > 0),
    -- The source-stated EQUIVALENT_TO edge (STATED_EQUIVALENT_TO).
    relationship_id   TEXT          REFERENCES relationship(id)   ON DELETE RESTRICT,
    rule              TEXT NOT NULL CHECK (length(trim(rule)) > 0),
    rule_version      TEXT NOT NULL CHECK (length(trim(rule_version)) > 0),
    extraction_run_id TEXT          REFERENCES extraction_run(id) ON DELETE RESTRICT,
    CHECK (concept_a_id < concept_b_id),
    CHECK ((basis = 'STATED_EQUIVALENT_TO') = (relationship_id IS NOT NULL)),
    CHECK ((basis = 'STATED_EQUIVALENT_TO') = (shared_name IS NULL))
) STRICT;

CREATE UNIQUE INDEX ux_concept_equivalence_pair ON concept_equivalence(concept_a_id, concept_b_id);
CREATE INDEX ix_concept_equivalence_b   ON concept_equivalence(concept_b_id);
CREATE INDEX ix_concept_equivalence_run ON concept_equivalence(extraction_run_id);

-- ------------------------------------------ relationship, rebuilt (P8-22, P8-24)
--
-- The only change is 'HAS_PROPERTY' in the relation_type CHECK, which SQLite cannot
-- widen in place. Every row and identifier is copied unchanged; every dependant is
-- recreated exactly as migration 0002 created it: the five ix_relationship_*
-- indexes, the D-24 unique index, the six shape triggers and the origin trigger.
-- The foreign keys of relationship_occurrence and relationship_inference name the
-- table by name, so they resolve to the rebuilt table (verified by
-- foreign_key_check before commit). Migration 0002's rebuild is the precedent.

CREATE TABLE relationship_new (
    id                TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at        TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at        TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    relation_type     TEXT NOT NULL CHECK (relation_type IN ('PARENT_OF','CHILD_OF','PREREQUISITE_OF','DEPENDS_ON','RELATED_TO','PART_OF','COMPOSED_OF','INSTANCE_OF','USES','APPLIES_TO','DERIVED_FROM','DEFINED_BY','CONTRADICTS','EQUIVALENT_TO','SIMILAR_TO','ALTERNATIVE_TO','REQUIRES','PRODUCES','CONSTRAINS','HAS_PROPERTY')),
    -- Mandatory distinction of Part 2 section 40.
    origin            TEXT NOT NULL CHECK (origin IN ('EXPLICIT','INFERRED')),
    lifecycle_status  TEXT NOT NULL CHECK (lifecycle_status IN ('ACTIVE','STALE','SUPERSEDED','CONFLICTED','UNVERIFIED','INVALID','ARCHIVED','DEPRECATED','UNCERTAIN','DELETED')),
    -- Exactly one from_* and exactly one to_* is set; that rule lives in the
    -- triggers below, not in a table CHECK (decision D-22, ADR 0009).
    from_concept_id   TEXT REFERENCES concept(id)          ON DELETE RESTRICT,
    from_knowledge_id TEXT REFERENCES knowledge_object(id) ON DELETE RESTRICT,
    to_concept_id     TEXT REFERENCES concept(id)          ON DELETE RESTRICT,
    to_knowledge_id   TEXT REFERENCES knowledge_object(id) ON DELETE RESTRICT
) STRICT;

INSERT INTO relationship_new (
    id, created_at, updated_at, relation_type, origin, lifecycle_status,
    from_concept_id, from_knowledge_id, to_concept_id, to_knowledge_id
)
SELECT
    id, created_at, updated_at, relation_type, origin, lifecycle_status,
    from_concept_id, from_knowledge_id, to_concept_id, to_knowledge_id
FROM relationship;

DROP TABLE relationship;
ALTER TABLE relationship_new RENAME TO relationship;

CREATE INDEX ix_relationship_from_concept   ON relationship(from_concept_id);
CREATE INDEX ix_relationship_from_knowledge ON relationship(from_knowledge_id);
CREATE INDEX ix_relationship_to_concept     ON relationship(to_concept_id);
CREATE INDEX ix_relationship_to_knowledge   ON relationship(to_knowledge_id);
CREATE INDEX ix_relationship_type           ON relationship(relation_type);

-- One ACTIVE edge per endpoints-and-type (D-24); COALESCE because NULLs are distinct
-- in a unique index (see migration 0002).
CREATE UNIQUE INDEX ux_relationship_active_edge ON relationship(
    COALESCE(from_concept_id, from_knowledge_id),
    COALESCE(to_concept_id,   to_knowledge_id),
    relation_type
) WHERE lifecycle_status = 'ACTIVE';

CREATE TRIGGER trg_relationship_from_exactly_one_insert
BEFORE INSERT ON relationship
WHEN ((NEW.from_concept_id IS NOT NULL) + (NEW.from_knowledge_id IS NOT NULL)) <> 1
BEGIN
    SELECT RAISE(ABORT, 'relationship: exactly one from_* endpoint must be set');
END;

CREATE TRIGGER trg_relationship_to_exactly_one_insert
BEFORE INSERT ON relationship
WHEN ((NEW.to_concept_id IS NOT NULL) + (NEW.to_knowledge_id IS NOT NULL)) <> 1
BEGIN
    SELECT RAISE(ABORT, 'relationship: exactly one to_* endpoint must be set');
END;

CREATE TRIGGER trg_relationship_no_self_edge_insert
BEFORE INSERT ON relationship
WHEN (NEW.from_concept_id   IS NOT NULL AND NEW.from_concept_id   = NEW.to_concept_id)
  OR (NEW.from_knowledge_id IS NOT NULL AND NEW.from_knowledge_id = NEW.to_knowledge_id)
BEGIN
    SELECT RAISE(ABORT, 'relationship: an edge may not point at itself');
END;

CREATE TRIGGER trg_relationship_from_exactly_one_update
BEFORE UPDATE ON relationship
WHEN ((NEW.from_concept_id IS NOT NULL) + (NEW.from_knowledge_id IS NOT NULL)) <> 1
BEGIN
    SELECT RAISE(ABORT, 'relationship: exactly one from_* endpoint must be set');
END;

CREATE TRIGGER trg_relationship_to_exactly_one_update
BEFORE UPDATE ON relationship
WHEN ((NEW.to_concept_id IS NOT NULL) + (NEW.to_knowledge_id IS NOT NULL)) <> 1
BEGIN
    SELECT RAISE(ABORT, 'relationship: exactly one to_* endpoint must be set');
END;

CREATE TRIGGER trg_relationship_no_self_edge_update
BEFORE UPDATE ON relationship
WHEN (NEW.from_concept_id   IS NOT NULL AND NEW.from_concept_id   = NEW.to_concept_id)
  OR (NEW.from_knowledge_id IS NOT NULL AND NEW.from_knowledge_id = NEW.to_knowledge_id)
BEGIN
    SELECT RAISE(ABORT, 'relationship: an edge may not point at itself');
END;

-- Part 2 section 40: origin may be upgraded INFERRED -> EXPLICIT, never downgraded.
CREATE TRIGGER trg_relationship_origin_never_downgrades
BEFORE UPDATE ON relationship
WHEN OLD.origin = 'EXPLICIT' AND NEW.origin = 'INFERRED'
BEGIN
    SELECT RAISE(ABORT, 'relationship: origin may be upgraded INFERRED to EXPLICIT, never downgraded');
END;
