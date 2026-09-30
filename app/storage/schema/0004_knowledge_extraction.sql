-- RUDRA knowledge database, schema version 4 (Phase 5 — knowledge extraction).
--
-- Decisions implemented here, all approved 2026-09-21:
--   D-35  ADR 0018  extraction_run; supersedes U-3's Phase 4 deferral
--   D-41  ADR 0022  extraction_issue; NOT the audit log
--   D-37  ADR 0019  knowledge_id on variable / rule / procedure (no new
--                   occurrence tables — sections 74-75 route provenance through
--                   knowledge_id, and `equation` already proves the pattern)
--   D-40  ADR 0019  char_start / char_end on the THREE OCCURRENCE TABLES ONLY
--   D-36  ADR 0020  nothing here computes normalized_hash; it stays NULL
--
-- This migration is ADDITIVE throughout: two CREATE TABLEs, nine ADD COLUMNs, and
-- one view rebuilt. NO table is rebuilt. Contrast migration 0002, which had to
-- rebuild `relationship` because a CHECK referenced a column being dropped.
--
-- ---------------------------------------------------------------------------
-- STATEMENT ORDER IS LOAD-BEARING. Measured 2026-09-21 with throwaway in-memory
-- probes (nothing written to the project):
--
--   * `ALTER TABLE ... ADD COLUMN ... CHECK (...)` IS accepted on a STRICT table
--     and IS enforced — on insert and on UPDATE of pre-existing rows. Phase 3 had
--     only measured that `ALTER TABLE ADD CONSTRAINT` is rejected; this is new.
--   * A CHECK on an added column may reference a column added EARLIER in the same
--     migration. `char_end`'s CHECK references `char_start`, so **char_start must
--     be added first**. Reversing these two lines silently produces a column whose
--     constraint cannot compile.
--   * Adding a column does NOT expose it through an existing view, so `evidence`
--     must be dropped and recreated to surface the spans.
-- ---------------------------------------------------------------------------
--
-- No id_sequence statement appears here, deliberately. Counters come from the live
-- EntityKind registry via _seed() on every migration (A-8, ADR 0013), so adding
-- EXTRACTION_RUN ("RUN") and EXTRACTION_ISSUE ("XIS") to the enum is sufficient.

-- ------------------------------------------------------------ extraction runs
--
-- Part 2 section 62: "Use versioned extraction runs ... This makes extraction
-- reproducible and debuggable." The six triggers in `trigger` are section 62's
-- own list; FIRST_EXTRACTION is added for a run that is not a re-run, and is
-- recorded in ADR 0018 as an interpretation rather than a quotation.
--
-- `status` is RUNNING until the caller commits, honouring Part 6 section 19's
-- "If ingestion fails before commit, the system should not present incomplete
-- knowledge as fully ingested knowledge."
CREATE TABLE extraction_run (
    id                TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at        TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at        TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    document_id       TEXT NOT NULL REFERENCES document(id) ON DELETE RESTRICT,
    run_number        INTEGER NOT NULL CHECK (run_number >= 1),
    trigger           TEXT NOT NULL CHECK (trigger IN ('FIRST_EXTRACTION','EXTRACTION_ENGINE_IMPROVED','OCR_IMPROVED','KNOWLEDGE_SCHEMA_CHANGED','USER_REQUESTED','DOCUMENT_METADATA_CHANGED','PROCESSING_FAILED')),
    extractor_version TEXT NOT NULL CHECK (length(trim(extractor_version)) > 0),
    parser_name       TEXT NOT NULL CHECK (length(trim(parser_name)) > 0),
    started_at        TEXT NOT NULL CHECK (started_at GLOB '????-??-??T??:??:??.???Z'),
    completed_at      TEXT CHECK (completed_at IS NULL OR completed_at GLOB '????-??-??T??:??:??.???Z'),
    status            TEXT NOT NULL CHECK (status IN ('RUNNING','COMPLETED','PARTIAL','FAILED')),
    -- A document cannot have two run 1s. Verified enforced on a STRICT table.
    UNIQUE (document_id, run_number),
    -- A run that is still RUNNING has not completed, by definition.
    CHECK (status <> 'RUNNING' OR completed_at IS NULL)
) STRICT;

CREATE INDEX ix_extraction_run_document ON extraction_run(document_id, run_number);
CREATE INDEX ix_extraction_run_status   ON extraction_run(status);

-- ---------------------------------------------------------- extraction issues
--
-- Part 2 section 60's nine checks, all nine in the vocabulary so no later phase
-- needs a CHECK widening (which SQLite makes a table rebuild). Phase 5 emits
-- seven; DUPLICATE_CONCEPT and POTENTIAL_CONTRADICTION need the Phase 8
-- equivalence machinery and are refused by ExtractionIssue.validate().
--
-- NOT the audit log: section 131's record shape is user_request / intent /
-- action / risk / authorization / execution / verification — an action audit.
-- Recording that page 412 had unbalanced delimiters is a different shape, and
-- nothing has ever written to `audit_event` (ADR 0022).
--
-- Deliberately absent: `severity` (section 60 assigns none; inventing a scale
-- would be an unauthorised claim, cf. Part 6 section 12) and `resolved` (a retry
-- is a NEW run with its own issues; mutating history would destroy section 62's
-- reproducibility).
CREATE TABLE extraction_issue (
    id                TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at        TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at        TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    extraction_run_id TEXT NOT NULL REFERENCES extraction_run(id)   ON DELETE RESTRICT,
    document_id       TEXT NOT NULL REFERENCES document(id)         ON DELETE RESTRICT,
    segment_id        TEXT          REFERENCES document_segment(id) ON DELETE RESTRICT,
    issue_type        TEXT NOT NULL CHECK (issue_type IN ('MISSING_SOURCE_REFERENCE','INVALID_EQUATION_STRUCTURE','UNKNOWN_VARIABLE','UNKNOWN_UNIT','BROKEN_RELATIONSHIP','DUPLICATE_CONCEPT','POTENTIAL_CONTRADICTION','OCR_UNCERTAINTY','MALFORMED_METADATA')),
    page_number       INTEGER CHECK (page_number IS NULL OR page_number >= 0),
    detail            TEXT NOT NULL CHECK (length(trim(detail)) > 0),
    -- The offending text, verbatim. Never a repaired version of it.
    excerpt           TEXT
) STRICT;

CREATE INDEX ix_extraction_issue_run  ON extraction_issue(extraction_run_id, issue_type);
CREATE INDEX ix_extraction_issue_page ON extraction_issue(document_id, page_number);

-- ------------------------------- provenance path for Variable/Rule/Procedure
--
-- Decision D-37 (ADR 0019). Part 3 section 87 puts "SOURCE: Document X, Page Y"
-- inside a rule's own representation; section 113 lists `source` among a
-- procedure's fields; section 75 keys the occurrence model on `knowledge_id`.
-- A nullable foreign key is therefore the MINIMUM provenance-safe representation —
-- three tables' worth of occurrence rows would carry no information these do not.
--
-- ADR 0008's D-20 deferred these "to the phases that populate them". That phase
-- is this one, and the deferral is discharged, not extended.
--
-- Nullable on purpose: a Variable may exist before it is attached to a claim.
ALTER TABLE variable  ADD COLUMN knowledge_id TEXT REFERENCES knowledge_object(id) ON DELETE RESTRICT;
ALTER TABLE rule      ADD COLUMN knowledge_id TEXT REFERENCES knowledge_object(id) ON DELETE RESTRICT;
ALTER TABLE procedure ADD COLUMN knowledge_id TEXT REFERENCES knowledge_object(id) ON DELETE RESTRICT;

CREATE INDEX ix_variable_knowledge  ON variable(knowledge_id);
CREATE INDEX ix_rule_knowledge      ON rule(knowledge_id);
CREATE INDEX ix_procedure_knowledge ON procedure(knowledge_id);

-- ------------------------------------ character spans + run link on occurrences
--
-- Decision D-40 (ADR 0019). Part 2 section 34 lists "text span" under "Where
-- practical, also preserve"; Part 3 section 75 lists `text_span` under "Optional
-- fields may include". With page-level segments it IS practical: the span is a
-- pair of offsets into text already stored.
--
-- `document_segment` is NOT touched. Phase 4 writes no occurrence rows, so no
-- Phase 4 table, constraint or test is affected by any line below.
--
-- `char_start` requires `segment_id`: an offset into an unnamed segment means
-- nothing. `char_end` requires `char_start`, which is why the order matters.

ALTER TABLE source_occurrence       ADD COLUMN char_start INTEGER CHECK (char_start IS NULL OR (char_start >= 0 AND segment_id IS NOT NULL));
ALTER TABLE source_occurrence       ADD COLUMN char_end   INTEGER CHECK (char_end IS NULL OR (char_start IS NOT NULL AND char_end >= char_start));
ALTER TABLE source_occurrence       ADD COLUMN extraction_run_id TEXT REFERENCES extraction_run(id) ON DELETE RESTRICT;

ALTER TABLE concept_occurrence      ADD COLUMN char_start INTEGER CHECK (char_start IS NULL OR (char_start >= 0 AND segment_id IS NOT NULL));
ALTER TABLE concept_occurrence      ADD COLUMN char_end   INTEGER CHECK (char_end IS NULL OR (char_start IS NOT NULL AND char_end >= char_start));
ALTER TABLE concept_occurrence      ADD COLUMN extraction_run_id TEXT REFERENCES extraction_run(id) ON DELETE RESTRICT;

ALTER TABLE relationship_occurrence ADD COLUMN char_start INTEGER CHECK (char_start IS NULL OR (char_start >= 0 AND segment_id IS NOT NULL));
ALTER TABLE relationship_occurrence ADD COLUMN char_end   INTEGER CHECK (char_end IS NULL OR (char_start IS NOT NULL AND char_end >= char_start));
ALTER TABLE relationship_occurrence ADD COLUMN extraction_run_id TEXT REFERENCES extraction_run(id) ON DELETE RESTRICT;

CREATE INDEX ix_occurrence_run              ON source_occurrence(extraction_run_id);
CREATE INDEX ix_concept_occurrence_run      ON concept_occurrence(extraction_run_id);
CREATE INDEX ix_relationship_occurrence_run ON relationship_occurrence(extraction_run_id);

-- ------------------------------------------------------------- evidence view
--
-- Rebuilt so Part 2 section 49 can still be answered in one query, now with the
-- span and the run. A view selects named columns, so ADD COLUMN alone leaves it
-- unchanged — measured, not assumed.
DROP VIEW evidence;

CREATE VIEW evidence AS
    SELECT id, 'KNOWLEDGE_OBJECT' AS subject_kind, knowledge_id AS subject_id,
           source_id, document_id, original_text AS evidence_text,
           extraction_method, extraction_timestamp, document_version_id,
           segment_id, page_number, section, char_start, char_end, extraction_run_id
      FROM source_occurrence
    UNION ALL
    SELECT id, 'CONCEPT', concept_id,
           source_id, document_id, surface_form,
           extraction_method, extraction_timestamp, document_version_id,
           segment_id, page_number, section, char_start, char_end, extraction_run_id
      FROM concept_occurrence
    UNION ALL
    SELECT id, 'RELATIONSHIP', relationship_id,
           source_id, document_id, original_text,
           extraction_method, extraction_timestamp, document_version_id,
           segment_id, page_number, section, char_start, char_end, extraction_run_id
      FROM relationship_occurrence;

-- Deliberately NO database-wide `extractor_version` row. The extractor version is
-- a property of each run (`extraction_run.extractor_version`, ADR 0018); a single
-- static value here would go stale the first time the extractor changed.
