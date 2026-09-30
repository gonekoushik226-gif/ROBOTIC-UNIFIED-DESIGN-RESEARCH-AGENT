-- RUDRA knowledge database, schema version 3 (Phase 4 — document ingestion).
--
-- Decisions implemented here, all approved 2026-09-20:
--   U-2   ADR 0015  document_structure, DocumentKind, StructureOrigin
--   U-5   ADR 0016  the seven "where available" §29 columns are DEFERRED, not added
--   U-3   ADR 0016  no extraction_run table
--   U-4   ADR 0016  no job table
--
-- This migration is purely ADDITIVE: one table and its indexes. No existing table
-- is altered, and nothing is rebuilt. Contrast migration 0002, which had to rebuild
-- `relationship` because a CHECK referenced the column being dropped.
--
-- No id_sequence statement appears here, deliberately. Counters come from the live
-- EntityKind registry via _seed() on every migration (ADR 0013).

-- ----------------------------------------------------------- document structure
--
-- Part 2 section 35's only imperative is "Structure detection must be adaptive",
-- and the section gives its own counter-examples: one textbook numbers itself
-- "Chapter 5 / 5.1 / 5.2.1", another "Unit III / Module 2 / Topic A", and a third
-- has no explicit hierarchy at all. Two columns carry that requirement:
--
--   * `label` is FREE TEXT. Parsing it into a numeric path would serve the first
--     document and fail the second.
--   * `parent_id` references this same table, so depth is whatever the document
--     has rather than a fixed chapter/section/subsection triple.
--
-- `origin` carries section 35's mandatory EXPLICIT_STRUCTURE vs INFERRED_STRUCTURE
-- distinction: a heading the document printed is a different claim from one RUDRA
-- inferred, and Part 1 section 5 forbids presenting the second as the first.
--
-- Deliberately absent (ADR 0015): bounding boxes (section 34 says "where
-- practical", and with pypdf it is not); any confidence score (Part 6 section 12 -
-- confidence must not stand in for evidence; `origin` carries the epistemic
-- distinction the specification actually asks for); and a segment->structure link,
-- which is additive later.
CREATE TABLE document_structure (
    id          TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at  TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at  TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    document_id TEXT NOT NULL REFERENCES document(id) ON DELETE RESTRICT,
    -- Self-referencing: adaptive depth (section 35). RESTRICT so removing a parent
    -- cannot silently orphan the elements beneath it.
    parent_id   TEXT REFERENCES document_structure(id) ON DELETE RESTRICT,
    kind        TEXT NOT NULL CHECK (kind IN ('TITLE','PREFACE','CHAPTER','SECTION','SUBSECTION','SUB_SUBSECTION','DEFINITION','THEOREM','LEMMA','PROOF','EXAMPLE','EXERCISE','PROBLEM','SOLUTION','EQUATION','TABLE','FIGURE','CAPTION','NOTE','REMARK','PROCEDURE','APPENDIX','REFERENCES')),
    -- "5.2.1" or "Unit III" or "Module 2". Never parsed.
    label       TEXT,
    title       TEXT,
    page_start  INTEGER CHECK (page_start IS NULL OR page_start >= 0),
    page_end    INTEGER CHECK (page_end IS NULL OR page_end >= 0),
    -- Part 2 section 32 requires extracted material to retain "position/order";
    -- structure needs it for the same reason, or reading order is lost.
    ordinal     INTEGER NOT NULL CHECK (ordinal >= 0),
    origin      TEXT NOT NULL CHECK (origin IN ('EXPLICIT_STRUCTURE','INFERRED_STRUCTURE')),
    CHECK (page_start IS NULL OR page_end IS NULL OR page_start <= page_end),
    CHECK (parent_id IS NULL OR parent_id <> id)
) STRICT;

CREATE INDEX ix_document_structure_document ON document_structure(document_id, ordinal);
CREATE INDEX ix_document_structure_parent   ON document_structure(parent_id);
CREATE INDEX ix_document_structure_kind     ON document_structure(document_id, kind);

-- Deliberately NOT created: a unique index on (document_id, label). A textbook may
-- legitimately repeat "5.1" across parts or volumes, and rejecting that would
-- refuse a valid document.
