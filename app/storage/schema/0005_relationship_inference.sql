-- RUDRA knowledge database, schema version 5 (Phase 6 — automatic classification).
--
-- Decisions implemented here, approved 2026-09-21:
--   P6-2a  ADR 0027  every INFERRED edge Phase 6 writes carries a recorded basis,
--                    written in the same transaction as the edge
--   P6-2b  ADR 0027  the basis is ONE additive table, relationship_inference
--                    (identifier prefix RI); no classification-run table, and the
--                    `derivation` table is not used
--
-- This migration is ADDITIVE ONLY: one CREATE TABLE, its indexes and its two
-- triggers. No existing table is altered or rebuilt, no CHECK is widened, no view
-- changes (a basis is not source evidence, so `evidence` is untouched - Part 6
-- section 9), and nothing here writes a row.
--
-- No id_sequence statement appears here, deliberately. Counters come from the live
-- EntityKind registry via _seed() on every migration (A-8, ADR 0013), so adding
-- RELATIONSHIP_INFERENCE ("RI") to the enum is sufficient.

-- ------------------------------------------------------ relationship_inference
--
-- Part 2 section 49: every significant knowledge item should answer "Where did
-- this come from?", including its "Transformation history" and "Relationship
-- origin". An INFERRED origin says THAT RUDRA inferred an edge; this row says FROM
-- WHAT - the rule, the rule's version and the evidence it read.
--
-- `rule` and `rule_version` are free text: closed vocabularies have already cost
-- this project a table rebuild (ADR 0018), and `extraction_method` is free text
-- for the same reason (ADR 0019).
--
-- `basis_occurrence_id` is the ONLY basis column in Phase 6 and is NULLABLE on
-- purpose. "At least one basis column is set" lives in the triggers below rather
-- than in a table CHECK, because a table CHECK cannot be widened in place while a
-- trigger can be dropped and recreated - so a later basis kind is one ADD COLUMN
-- plus two trigger rewrites, never a rebuild (the ADR 0009 pattern). For rule R1
-- it is always set, and the Phase 6 writer requires that (ADR 0028).
--
-- Every reference is ON DELETE RESTRICT, extending the Part 7 guarantee: the edge
-- and the evidence a basis names can never be removed out from under it.
CREATE TABLE relationship_inference (
    id                  TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at          TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at          TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    relationship_id     TEXT NOT NULL REFERENCES relationship(id)      ON DELETE RESTRICT,
    rule                TEXT NOT NULL CHECK (length(trim(rule)) > 0),
    rule_version        TEXT NOT NULL CHECK (length(trim(rule_version)) > 0),
    basis_occurrence_id TEXT          REFERENCES source_occurrence(id) ON DELETE RESTRICT,
    -- The concept name as it appears in the basis text (for R1: the D-30-normalised
    -- definition text the rule matched against).
    matched_text        TEXT CHECK (matched_text IS NULL OR length(trim(matched_text)) > 0)
) STRICT;

-- Lookups by edge use the unique index below, whose first column is relationship_id.
CREATE INDEX ix_relationship_inference_basis ON relationship_inference(basis_occurrence_id);

-- Idempotence: a repeated classification adds nothing.
--
-- COALESCE is not decoration. SQLite treats NULLs as DISTINCT in unique indexes,
-- so a plain index over the two nullable columns would never conflict on a row
-- that leaves either of them NULL - measured for relationship endpoints in Phase 3
-- (ADR 0009) and stated as ARCHITECTURE.md section 6.4 rule 7. '' cannot collide
-- with a real value: an identifier is never empty, and matched_text's CHECK
-- refuses a blank string.
CREATE UNIQUE INDEX ux_relationship_inference_basis ON relationship_inference(
    relationship_id,
    rule,
    rule_version,
    COALESCE(basis_occurrence_id, ''),
    COALESCE(matched_text, '')
);

-- At least one basis column must be set, on INSERT and on UPDATE. Phase 6 has one
-- basis column; a later basis kind widens these WHEN clauses by DROP + CREATE.
CREATE TRIGGER trg_relationship_inference_basis_insert
BEFORE INSERT ON relationship_inference
WHEN NEW.basis_occurrence_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'relationship_inference: at least one basis column must be set');
END;

CREATE TRIGGER trg_relationship_inference_basis_update
BEFORE UPDATE ON relationship_inference
WHEN NEW.basis_occurrence_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'relationship_inference: at least one basis column must be set');
END;
