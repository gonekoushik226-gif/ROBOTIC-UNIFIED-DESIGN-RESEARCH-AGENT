-- RUDRA knowledge database, schema version 2 (Phase 3 — knowledge representation).
--
-- Decisions implemented here, all approved 2026-09-20:
--   D-19/D-20/D-33  ADR 0008  concept_occurrence, relationship_occurrence, evidence view
--   D-22            ADR 0009  typed foreign-key endpoints on relationship
--   D-23/D-24       ADR 0009  one ACTIVE edge per endpoints-and-type
--   Q-1/D-26/D-25   ADR 0010  concept_alias, concept.context
--   D-30            ADR 0010  alias_normalization_version
--
-- `source_occurrence` is NOT touched. The alternative — one evidence table with a
-- nullable knowledge_id — was rejected: Part 3 section 75 lists knowledge_id in its
-- REQUIRED field block, and ADR 0007 had already rejected the same pattern (a
-- required field emptied so a second kind of thing can occupy the table) one level
-- up, when it declined to make Concept a role over knowledge_object.
--
-- No id_sequence statement appears in this file, deliberately. Counters come from
-- the live EntityKind registry via _seed() on every migration (ADR 0013); a
-- migration that also inserted them would collide on a fresh database and be
-- missing on an upgraded one.

-- ------------------------------------------------------------- concept naming

-- Part 2 section 44: "The system should retain the original terminology."
-- This is the CURATED name set RUDRA matches on — a decision. What a source
-- actually printed is a fact and lives in concept_occurrence.surface_form. The two
-- are never derived from each other: promoting a surface form to an alias
-- automatically would merge concepts on name similarity, which Part 3 section 73
-- forbids.
CREATE TABLE concept_alias (
    id               TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at       TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at       TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    concept_id       TEXT NOT NULL REFERENCES concept(id) ON DELETE RESTRICT,
    alias            TEXT NOT NULL CHECK (length(trim(alias)) > 0),
    -- NFKC + casefold + whitespace collapse (ADR 0010). The database cannot verify
    -- this value — nothing in SQLite can call the normalisation function — so the
    -- model's validate() is the only place the derivation is checked at all.
    normalized_alias TEXT NOT NULL CHECK (length(trim(normalized_alias)) > 0),
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('ACTIVE','STALE','SUPERSEDED','CONFLICTED','UNVERIFIED','INVALID','ARCHIVED','DEPRECATED','UNCERTAIN','DELETED')),
    -- Nullable so a USER_STATED name can still answer section 49's "Where did this
    -- come from?" without a document. Page-level evidence is concept_occurrence.
    source_id        TEXT REFERENCES source(id) ON DELETE RESTRICT
) STRICT;

-- Unique WITHIN one concept only. Deliberately NOT globally unique: Part 6 section
-- 46 requires "Gain" to belong to a voltage amplifier, a current amplifier and a
-- control system at the same time.
CREATE UNIQUE INDEX ux_concept_alias ON concept_alias(concept_id, normalized_alias);
CREATE INDEX ix_concept_alias_normalized ON concept_alias(normalized_alias);
CREATE INDEX ix_concept_alias_source ON concept_alias(source_id);

-- The disambiguator for the "Gain" case (Part 6 section 46). Free text in Phase 3;
-- a reference to a context concept is the expected Phase 6 successor, and that
-- change is additive.
--
-- There is deliberately NO unique constraint over (canonical_name, context): one
-- would force an automatic merge-or-refuse decision at insert time, and section 44
-- says "do not automatically merge concepts when the evidence is insufficient"
-- while Part 3 section 73 says "Never merge two concepts merely because they have
-- similar names." Duplicate concepts are DETECTED, not prevented; Phase 8 resolves
-- them with evidence.
ALTER TABLE concept ADD COLUMN context TEXT;

-- ------------------------------------------------- relationship, rebuilt (D-22)
--
-- A rebuild is required, not chosen: ALTER TABLE ... DROP COLUMN from_id is
-- rejected because CHECK (from_id <> to_id) references it, and dropping the two
-- indexes first does not help. The table is empty today, so nothing moves; the
-- structural point is that this happens ONCE, because every later endpoint kind is
-- additive (ALTER TABLE ADD COLUMN ... REFERENCES ... is accepted and enforces).

CREATE TABLE relationship_new (
    id                TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at        TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at        TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    relation_type     TEXT NOT NULL CHECK (relation_type IN ('PARENT_OF','CHILD_OF','PREREQUISITE_OF','DEPENDS_ON','RELATED_TO','PART_OF','COMPOSED_OF','INSTANCE_OF','USES','APPLIES_TO','DERIVED_FROM','DEFINED_BY','CONTRADICTS','EQUIVALENT_TO','SIMILAR_TO','ALTERNATIVE_TO','REQUIRES','PRODUCES','CONSTRAINS')),
    -- Mandatory distinction of Part 2 section 40.
    origin            TEXT NOT NULL CHECK (origin IN ('EXPLICIT','INFERRED')),
    lifecycle_status  TEXT NOT NULL CHECK (lifecycle_status IN ('ACTIVE','STALE','SUPERSEDED','CONFLICTED','UNVERIFIED','INVALID','ARCHIVED','DEPRECATED','UNCERTAIN','DELETED')),
    -- Exactly one from_* and exactly one to_* is set. That rule lives in triggers
    -- below, NOT in a table CHECK, because a table CHECK cannot be widened in place
    -- (ALTER TABLE ADD CONSTRAINT is rejected) whereas a trigger can be dropped and
    -- recreated. That single fact is what keeps a later endpoint kind additive.
    from_concept_id   TEXT REFERENCES concept(id)          ON DELETE RESTRICT,
    from_knowledge_id TEXT REFERENCES knowledge_object(id) ON DELETE RESTRICT,
    to_concept_id     TEXT REFERENCES concept(id)          ON DELETE RESTRICT,
    to_knowledge_id   TEXT REFERENCES knowledge_object(id) ON DELETE RESTRICT
) STRICT;

-- A temporary guard for the copy only. The old from_id/to_id were untyped, so a row
-- could point at an equation, variable, rule or procedure — endpoint kinds Phase 3
-- does not yet carry. Rather than silently dropping such a row to NULL, abort the
-- whole migration; a failed migration leaves schema version 1 and its data intact.
CREATE TRIGGER trg_relationship_migration_guard
BEFORE INSERT ON relationship_new
WHEN (NEW.from_concept_id IS NULL AND NEW.from_knowledge_id IS NULL)
  OR (NEW.to_concept_id   IS NULL AND NEW.to_knowledge_id   IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'migration 0002: a relationship endpoint is not a concept or knowledge object; Phase 3 cannot represent it');
END;

INSERT INTO relationship_new (
    id, created_at, updated_at, relation_type, origin, lifecycle_status,
    from_concept_id, from_knowledge_id, to_concept_id, to_knowledge_id
)
SELECT
    id, created_at, updated_at, relation_type, origin, lifecycle_status,
    CASE WHEN substr(from_id, 1, instr(from_id,'-') - 1) = 'CPT' THEN from_id END,
    CASE WHEN substr(from_id, 1, instr(from_id,'-') - 1) = 'K'   THEN from_id END,
    CASE WHEN substr(to_id,   1, instr(to_id,'-')   - 1) = 'CPT' THEN to_id   END,
    CASE WHEN substr(to_id,   1, instr(to_id,'-')   - 1) = 'K'   THEN to_id   END
FROM relationship;

DROP TRIGGER trg_relationship_migration_guard;
DROP TABLE relationship;
ALTER TABLE relationship_new RENAME TO relationship;

CREATE INDEX ix_relationship_from_concept   ON relationship(from_concept_id);
CREATE INDEX ix_relationship_from_knowledge ON relationship(from_knowledge_id);
CREATE INDEX ix_relationship_to_concept     ON relationship(to_concept_id);
CREATE INDEX ix_relationship_to_knowledge   ON relationship(to_knowledge_id);
CREATE INDEX ix_relationship_type           ON relationship(relation_type);

-- One ACTIVE edge per endpoints-and-type (D-24).
--
-- COALESCE is not decoration. SQLite treats NULLs as DISTINCT in unique indexes, so
-- a plain index over the four nullable endpoint columns would never conflict and
-- would enforce NOTHING — measured: two identical ACTIVE edges were both accepted.
-- The expression collapses each side to its one non-NULL value.
--
-- `origin` is excluded from the key on purpose. A relation stated by two sources and
-- also inferred is ONE edge with several occurrences, which is Part 3 sections 69-70
-- ("RUDRA MUST NOT repeatedly create the same knowledge") applied to edges.
--
-- The lifecycle filter is what lets an archived twin exist, per the tombstone rule
-- in ARCHITECTURE section 6.4.
CREATE UNIQUE INDEX ux_relationship_active_edge ON relationship(
    COALESCE(from_concept_id, from_knowledge_id),
    COALESCE(to_concept_id,   to_knowledge_id),
    relation_type
) WHERE lifecycle_status = 'ACTIVE';

-- Shape rules. Six triggers: exactly-one per side and no-self-edge, each for INSERT
-- and UPDATE. Widening these for a new endpoint kind is DROP + CREATE, with no table
-- rebuild at any data volume.

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

-- Part 2 section 40 forbids presenting an inferred relationship as source-stated.
-- The converse transition is legitimate: when a source is found to state an edge
-- RUDRA had inferred, the edge IS now explicit. The reverse is not, so it is blocked
-- here as well as in the service layer (D-24).
CREATE TRIGGER trg_relationship_origin_never_downgrades
BEFORE UPDATE ON relationship
WHEN OLD.origin = 'EXPLICIT' AND NEW.origin = 'INFERRED'
BEGIN
    SELECT RAISE(ABORT, 'relationship: origin may be upgraded INFERRED to EXPLICIT, never downgraded');
END;

-- ------------------------------------------------------------------- evidence
--
-- Part 3 section 71 requires four layers: "Canonical concept + Canonical normalized
-- knowledge + Source-specific statements + Source locations". Before Phase 3, layer
-- one had no path at all to layers three and four. These two tables complete it.
-- Every reference to a source or document is ON DELETE RESTRICT, extending the Part
-- 7 guarantee that deleting a source file never deletes knowledge.

CREATE TABLE concept_occurrence (
    id                   TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at           TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at           TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    concept_id           TEXT NOT NULL REFERENCES concept(id)          ON DELETE RESTRICT,
    source_id            TEXT NOT NULL REFERENCES source(id)           ON DELETE RESTRICT,
    document_id          TEXT NOT NULL REFERENCES document(id)         ON DELETE RESTRICT,
    -- The term AS THE SOURCE PRINTED IT. This is what section 44's "retain the
    -- original terminology" asks for; concept.canonical_name is RUDRA's own choice.
    surface_form         TEXT NOT NULL CHECK (length(trim(surface_form)) > 0),
    extraction_method    TEXT NOT NULL,
    extraction_timestamp TEXT NOT NULL CHECK (extraction_timestamp GLOB '????-??-??T??:??:??.???Z'),
    document_version_id  TEXT REFERENCES document_version(id)          ON DELETE RESTRICT,
    segment_id           TEXT REFERENCES document_segment(id)          ON DELETE RESTRICT,
    page_number          INTEGER CHECK (page_number IS NULL OR page_number >= 0),
    section              TEXT
) STRICT;

CREATE INDEX ix_concept_occurrence_concept  ON concept_occurrence(concept_id);
CREATE INDEX ix_concept_occurrence_source   ON concept_occurrence(source_id);
CREATE INDEX ix_concept_occurrence_document ON concept_occurrence(document_id);
CREATE INDEX ix_concept_occurrence_surface  ON concept_occurrence(surface_form);

CREATE TABLE relationship_occurrence (
    id                   TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at           TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at           TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    relationship_id      TEXT NOT NULL REFERENCES relationship(id)     ON DELETE RESTRICT,
    source_id            TEXT NOT NULL REFERENCES source(id)           ON DELETE RESTRICT,
    document_id          TEXT NOT NULL REFERENCES document(id)         ON DELETE RESTRICT,
    -- The sentence that states the relation.
    original_text        TEXT NOT NULL CHECK (length(trim(original_text)) > 0),
    extraction_method    TEXT NOT NULL,
    extraction_timestamp TEXT NOT NULL CHECK (extraction_timestamp GLOB '????-??-??T??:??:??.???Z'),
    document_version_id  TEXT REFERENCES document_version(id)          ON DELETE RESTRICT,
    segment_id           TEXT REFERENCES document_segment(id)          ON DELETE RESTRICT,
    page_number          INTEGER CHECK (page_number IS NULL OR page_number >= 0),
    section              TEXT
) STRICT;

CREATE INDEX ix_relationship_occurrence_rel      ON relationship_occurrence(relationship_id);
CREATE INDEX ix_relationship_occurrence_source   ON relationship_occurrence(source_id);
CREATE INDEX ix_relationship_occurrence_document ON relationship_occurrence(document_id);

-- Part 2 section 49 requires every significant knowledge item to answer "Where did
-- this come from?". Read-only, so it adds no constraint and weakens none: the three
-- base tables keep their own NOT NULLs and foreign keys.
CREATE VIEW evidence AS
    SELECT id, 'KNOWLEDGE_OBJECT' AS subject_kind, knowledge_id AS subject_id,
           source_id, document_id, original_text AS evidence_text,
           extraction_method, extraction_timestamp, document_version_id,
           segment_id, page_number, section
      FROM source_occurrence
    UNION ALL
    SELECT id, 'CONCEPT', concept_id,
           source_id, document_id, surface_form,
           extraction_method, extraction_timestamp, document_version_id,
           segment_id, page_number, section
      FROM concept_occurrence
    UNION ALL
    SELECT id, 'RELATIONSHIP', relationship_id,
           source_id, document_id, original_text,
           extraction_method, extraction_timestamp, document_version_id,
           segment_id, page_number, section
      FROM relationship_occurrence;

-- Which normalisation rule produced the stored normalized_alias values (ADR 0010).
-- Without this, a future change to the rule would silently leave old rows
-- unreachable by new lookups.
INSERT INTO database_metadata(key, value) VALUES ('alias_normalization_version', '1');
