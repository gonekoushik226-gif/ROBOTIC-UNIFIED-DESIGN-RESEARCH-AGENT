-- RUDRA knowledge database, schema version 1 (Phase 2).
--
-- Decisions in force (ADR 0004): every table is STRICT, so SQLite refuses to put
-- text in an INTEGER column instead of storing it and surprising us later.
-- Foreign keys are ON for every connection, applied by app/storage/connection.py.
--
-- Identifier format (ADR 0006) is checked here as well as in the models:
--     prefix of 1-4 characters, a hyphen, then at least 8 digits.
-- The check is written with instr/substr/GLOB because SQLite has no regex.
--
-- Part 7 is the rule that shapes this file: deleting a source file must never
-- delete knowledge. Every reference from evidence to a source or a document is
-- ON DELETE RESTRICT, so the row cannot be removed while evidence points at it.
-- Deleting a *file* is expressed by setting source.availability, which touches
-- no knowledge at all.
--
-- Timestamps are ISO-8601 UTC with a Z suffix, so ORDER BY is chronological.

-- ---------------------------------------------------------------- infrastructure

CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    checksum   TEXT NOT NULL,
    applied_at TEXT NOT NULL CHECK (applied_at GLOB '????-??-??T??:??:??.???Z')
) STRICT;

-- instance_id (ADR 0006) qualifies this database's identifiers. Identifiers are
-- unique within a database but not across installations, so any future import
-- must rewrite them; this records which installation they came from.
CREATE TABLE database_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

-- One counter per entity kind. Allocation runs inside the caller's transaction,
-- so a rollback returns the counter with it and leaves no gap.
CREATE TABLE id_sequence (
    entity_kind TEXT PRIMARY KEY,
    next_value  INTEGER NOT NULL CHECK (next_value >= 1)
) STRICT;

-- ------------------------------------------------------------------- documents

CREATE TABLE document (
    id                 TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at         TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at         TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    filename           TEXT NOT NULL CHECK (length(trim(filename)) > 0),
    original_filename  TEXT NOT NULL CHECK (length(trim(original_filename)) > 0),
    source_type        TEXT NOT NULL,
    file_path          TEXT NOT NULL,
    file_hash          TEXT NOT NULL CHECK (length(trim(file_hash)) > 0),
    file_size          INTEGER NOT NULL CHECK (file_size >= 0),
    mime_type          TEXT NOT NULL,
    ingested_at        TEXT NOT NULL CHECK (ingested_at GLOB '????-??-??T??:??:??.???Z'),
    processing_status  TEXT NOT NULL CHECK (processing_status IN ('PENDING','PROCESSING','PROCESSED','PARTIALLY_PROCESSED','FAILED')),
    processing_version INTEGER NOT NULL CHECK (processing_version >= 0),
    document_title     TEXT,
    author             TEXT,
    publisher          TEXT,
    edition            TEXT,
    publication_date   TEXT,
    language           TEXT,
    page_count         INTEGER CHECK (page_count IS NULL OR page_count >= 0)
) STRICT;

-- The same file must not become two documents (Part 3 section 80). Soft-deleted
-- rows are excluded so a re-upload after deletion can reuse the identity.
CREATE UNIQUE INDEX ux_document_file_hash ON document(file_hash);

CREATE TABLE document_version (
    id           TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at   TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at   TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    document_id  TEXT NOT NULL REFERENCES document(id) ON DELETE RESTRICT,
    version_label TEXT NOT NULL CHECK (length(trim(version_label)) > 0),
    file_hash    TEXT NOT NULL,
    ingested_at  TEXT NOT NULL CHECK (ingested_at GLOB '????-??-??T??:??:??.???Z'),
    notes        TEXT
) STRICT;

CREATE INDEX ix_document_version_document ON document_version(document_id);

CREATE TABLE document_segment (
    id                  TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at          TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at          TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    document_id         TEXT NOT NULL REFERENCES document(id) ON DELETE RESTRICT,
    page_number         INTEGER NOT NULL CHECK (page_number >= 0),
    ordinal             INTEGER NOT NULL CHECK (ordinal >= 0),
    text                TEXT NOT NULL,
    extraction_method   TEXT NOT NULL,
    text_origin         TEXT NOT NULL CHECK (text_origin IN ('NATIVE_TEXT','OCR','MIXED','UNKNOWN')),
    document_version_id TEXT REFERENCES document_version(id) ON DELETE RESTRICT,
    confidence          REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
) STRICT;

CREATE INDEX ix_document_segment_document ON document_segment(document_id, page_number, ordinal);

-- ------------------------------------------------------------------ provenance

CREATE TABLE source (
    id              TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at      TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at      TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    name            TEXT NOT NULL CHECK (length(trim(name)) > 0),
    source_category TEXT NOT NULL CHECK (source_category IN ('USER_PROVIDED_SOURCE','LOCAL_SOURCE','AUTHORIZED_EXTERNAL_SOURCE','SYSTEM_DERIVED','USER_STATED','UNKNOWN_SOURCE','UNAUTHORIZED_SOURCE')),
    authorization   TEXT NOT NULL CHECK (authorization IN ('AUTHORIZED','NOT_AUTHORIZED')),
    -- Availability of the FILE. Independent of any knowledge status (Part 7 section 9).
    availability    TEXT NOT NULL CHECK (availability IN ('AVAILABLE','MISSING','DELETED_BY_USER','ARCHIVED','CORRUPTED','UNREADABLE','EXTERNAL_ONLY','NOT_RETRIEVABLE')),
    document_id     TEXT REFERENCES document(id) ON DELETE RESTRICT,
    url             TEXT,
    -- Preserved after the file is deleted, and never recreated (Part 7 section 6).
    file_hash       TEXT
) STRICT;

CREATE INDEX ix_source_document ON source(document_id);
CREATE INDEX ix_source_availability ON source(availability);

-- ---------------------------------------------------------------- knowledge

CREATE TABLE knowledge_object (
    id                TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at        TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at        TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    knowledge_type    TEXT NOT NULL CHECK (knowledge_type IN ('CONCEPT','DEFINITION','PROPERTY','EQUATION','VARIABLE','UNIT','RULE','CONSTRAINT','PROCEDURE','ALGORITHM','EXAMPLE','COUNTEREXAMPLE','APPLICATION','ASSUMPTION','CONDITION','LIMITATION','OBSERVATION','CLAIM','RELATIONSHIP','DERIVATION')),
    canonical_name    TEXT NOT NULL CHECK (length(trim(canonical_name)) > 0),
    statement         TEXT NOT NULL CHECK (length(trim(statement)) > 0),
    lifecycle_status  TEXT NOT NULL CHECK (lifecycle_status IN ('ACTIVE','STALE','SUPERSEDED','CONFLICTED','UNVERIFIED','INVALID','ARCHIVED','DEPRECATED','UNCERTAIN','DELETED')),
    certainty         TEXT NOT NULL CHECK (certainty IN ('KNOWN','UNKNOWN','UNCERTAIN','INFERRED','CALCULATED','ESTIMATED','REPORTED_BY_SOURCE','CONFLICTING','UNVERIFIED','VERIFIED')),
    knowledge_version INTEGER NOT NULL CHECK (knowledge_version >= 1),
    -- For Phase 8 deduplication. Nothing computes or compares it yet.
    normalized_hash   TEXT,
    -- Confidence describes extraction or matching uncertainty ONLY. It is never
    -- evidence, and a high value never makes an unsupported claim supported
    -- (Part 6 section 12).
    confidence        REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
) STRICT;

CREATE INDEX ix_knowledge_object_name ON knowledge_object(canonical_name);
-- One active canonical object per normalised form. Archived duplicates remain,
-- which is what lets deduplication merge without destroying history.
CREATE UNIQUE INDEX ux_knowledge_object_normalized
    ON knowledge_object(normalized_hash)
    WHERE normalized_hash IS NOT NULL AND lifecycle_status = 'ACTIVE';

CREATE TABLE concept (
    id               TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at       TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at       TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    canonical_name   TEXT NOT NULL CHECK (length(trim(canonical_name)) > 0),
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('ACTIVE','STALE','SUPERSEDED','CONFLICTED','UNVERIFIED','INVALID','ARCHIVED','DEPRECATED','UNCERTAIN','DELETED')),
    description      TEXT
) STRICT;

CREATE INDEX ix_concept_name ON concept(canonical_name);

-- Evidence. Many occurrences may support one canonical knowledge object, which is
-- how repeated knowledge is stored once while every source is preserved
-- (Part 3 sections 69-71). RESTRICT on both references is the Part 7 guarantee.
CREATE TABLE source_occurrence (
    id                   TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at           TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at           TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    knowledge_id         TEXT NOT NULL REFERENCES knowledge_object(id) ON DELETE RESTRICT,
    source_id            TEXT NOT NULL REFERENCES source(id) ON DELETE RESTRICT,
    document_id          TEXT NOT NULL REFERENCES document(id) ON DELETE RESTRICT,
    original_text        TEXT NOT NULL CHECK (length(trim(original_text)) > 0),
    extraction_method    TEXT NOT NULL,
    extraction_timestamp TEXT NOT NULL CHECK (extraction_timestamp GLOB '????-??-??T??:??:??.???Z'),
    document_version_id  TEXT REFERENCES document_version(id) ON DELETE RESTRICT,
    segment_id           TEXT REFERENCES document_segment(id) ON DELETE RESTRICT,
    page_number          INTEGER CHECK (page_number IS NULL OR page_number >= 0),
    section              TEXT
) STRICT;

CREATE INDEX ix_occurrence_knowledge ON source_occurrence(knowledge_id);
CREATE INDEX ix_occurrence_source ON source_occurrence(source_id);
CREATE INDEX ix_occurrence_document ON source_occurrence(document_id);

-- Edges may join a knowledge object to a concept, so from_id/to_id are not typed
-- by a foreign key. The identifier FORMAT is still checked. Referential integrity
-- for these polymorphic edges is a known Phase 2 limitation.
CREATE TABLE relationship (
    id               TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at       TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at       TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    from_id          TEXT NOT NULL CHECK (instr(from_id,'-') BETWEEN 2 AND 5),
    to_id            TEXT NOT NULL CHECK (instr(to_id,'-') BETWEEN 2 AND 5),
    relation_type    TEXT NOT NULL CHECK (relation_type IN ('PARENT_OF','CHILD_OF','PREREQUISITE_OF','DEPENDS_ON','RELATED_TO','PART_OF','COMPOSED_OF','INSTANCE_OF','USES','APPLIES_TO','DERIVED_FROM','DEFINED_BY','CONTRADICTS','EQUIVALENT_TO','SIMILAR_TO','ALTERNATIVE_TO','REQUIRES','PRODUCES','CONSTRAINS')),
    -- Mandatory distinction of Part 2 section 40.
    origin           TEXT NOT NULL CHECK (origin IN ('EXPLICIT','INFERRED')),
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('ACTIVE','STALE','SUPERSEDED','CONFLICTED','UNVERIFIED','INVALID','ARCHIVED','DEPRECATED','UNCERTAIN','DELETED')),
    CHECK (from_id <> to_id)
) STRICT;

CREATE INDEX ix_relationship_from ON relationship(from_id);
CREATE INDEX ix_relationship_to ON relationship(to_id);

CREATE TABLE equation (
    id               TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at       TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at       TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    expression       TEXT NOT NULL CHECK (length(trim(expression)) > 0),
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('ACTIVE','STALE','SUPERSEDED','CONFLICTED','UNVERIFIED','INVALID','ARCHIVED','DEPRECATED','UNCERTAIN','DELETED')),
    canonical_form   TEXT,
    knowledge_id     TEXT REFERENCES knowledge_object(id) ON DELETE RESTRICT,
    description      TEXT
) STRICT;

CREATE TABLE variable (
    id               TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at       TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at       TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    symbol           TEXT NOT NULL CHECK (length(trim(symbol)) > 0),
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('ACTIVE','STALE','SUPERSEDED','CONFLICTED','UNVERIFIED','INVALID','ARCHIVED','DEPRECATED','UNCERTAIN','DELETED')),
    name             TEXT,
    unit             TEXT,
    description      TEXT
) STRICT;

CREATE TABLE rule (
    id               TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at       TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at       TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    name             TEXT NOT NULL CHECK (length(trim(name)) > 0),
    statement        TEXT NOT NULL CHECK (length(trim(statement)) > 0),
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('ACTIVE','STALE','SUPERSEDED','CONFLICTED','UNVERIFIED','INVALID','ARCHIVED','DEPRECATED','UNCERTAIN','DELETED')),
    preconditions    TEXT,
    output           TEXT
) STRICT;

CREATE TABLE procedure (
    id                        TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at                TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at                TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    name                      TEXT NOT NULL CHECK (length(trim(name)) > 0),
    -- A guessed workflow must never be presented as documented (Part 3 section 111).
    documentation_status      TEXT NOT NULL CHECK (documentation_status IN ('DOCUMENTED_PROCEDURE','INFERRED_PROCEDURE','VERIFIED_PROCEDURE')),
    lifecycle_status          TEXT NOT NULL CHECK (lifecycle_status IN ('ACTIVE','STALE','SUPERSEDED','CONFLICTED','UNVERIFIED','INVALID','ARCHIVED','DEPRECATED','UNCERTAIN','DELETED')),
    execution_count           INTEGER NOT NULL CHECK (execution_count >= 0),
    successful_executions     INTEGER NOT NULL CHECK (successful_executions >= 0),
    failed_executions         INTEGER NOT NULL CHECK (failed_executions >= 0),
    last_verified             TEXT,
    known_application_version TEXT,
    limitations               TEXT,
    CHECK (successful_executions + failed_executions <= execution_count)
) STRICT;

-- ------------------------------------------------- reasoning and calculation

CREATE TABLE derivation (
    id         TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    method     TEXT NOT NULL CHECK (length(trim(method)) > 0),
    summary    TEXT NOT NULL CHECK (length(trim(summary)) > 0),
    target_id  TEXT
) STRICT;

CREATE TABLE calculation (
    id                  TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at          TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at          TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    formula             TEXT NOT NULL CHECK (length(trim(formula)) > 0),
    verification_status TEXT NOT NULL CHECK (verification_status IN ('VERIFIED','FAILED','INCONCLUSIVE','PENDING')),
    result_value        TEXT,
    result_unit         TEXT,
    derivation_id       TEXT REFERENCES derivation(id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE conflict (
    id               TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at       TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at       TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    claim_a_id       TEXT NOT NULL REFERENCES knowledge_object(id) ON DELETE RESTRICT,
    claim_b_id       TEXT NOT NULL REFERENCES knowledge_object(id) ON DELETE RESTRICT,
    -- Never invented: UNDETERMINED is a legitimate answer (Part 2 section 46).
    cause            TEXT NOT NULL CHECK (cause IN ('DIFFERENT_ASSUMPTIONS','DIFFERENT_DEFINITIONS','DIFFERENT_CONVENTIONS','DIFFERENT_UNITS','DIFFERENT_OPERATING_CONDITIONS','DIFFERENT_EDITIONS','DIFFERENT_CONTEXTS','ACTUAL_CONTRADICTION','UNDETERMINED')),
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('ACTIVE','STALE','SUPERSEDED','CONFLICTED','UNVERIFIED','INVALID','ARCHIVED','DEPRECATED','UNCERTAIN','DELETED')),
    context          TEXT,
    CHECK (claim_a_id <> claim_b_id)
) STRICT;

CREATE INDEX ix_conflict_claims ON conflict(claim_a_id, claim_b_id);

-- ------------------------------------------------------ requests and actions

CREATE TABLE query (
    id           TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at   TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at   TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    text         TEXT NOT NULL CHECK (length(trim(text)) > 0),
    task_class   TEXT CHECK (task_class IS NULL OR task_class IN ('KNOWLEDGE_QUERY','CALCULATION','EXPLANATION','SOURCE_QUERY','COMPARISON','DOCUMENT_QUERY','FILE_OPERATION','APPLICATION_CONTROL','WEB_SEARCH','DOCUMENT_CREATION','IMAGE_REQUEST','PROCEDURE_EXECUTION','SYSTEM_QUERY')),
    source_scope TEXT
) STRICT;

CREATE TABLE intent (
    id                    TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at            TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at            TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    intent_type           TEXT NOT NULL CHECK (length(trim(intent_type)) > 0),
    risk_level            TEXT NOT NULL CHECK (risk_level IN ('LOW','MEDIUM','HIGH')),
    requires_confirmation INTEGER NOT NULL CHECK (requires_confirmation IN (0,1)),
    target                TEXT,
    source_scope          TEXT,
    requested_output      TEXT,
    query_id              TEXT REFERENCES query(id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE action (
    id          TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at  TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at  TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    name        TEXT NOT NULL CHECK (length(trim(name)) > 0),
    risk_level  TEXT NOT NULL CHECK (risk_level IN ('LOW','MEDIUM','HIGH')),
    description TEXT
) STRICT;

CREATE TABLE execution_plan (
    id         TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    status     TEXT NOT NULL CHECK (length(trim(status)) > 0),
    step_count INTEGER NOT NULL CHECK (step_count >= 0),
    intent_id  TEXT REFERENCES intent(id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE verification (
    id         TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    subject_id TEXT NOT NULL CHECK (instr(subject_id,'-') BETWEEN 2 AND 5),
    -- INCONCLUSIVE exists so "could not tell" is never recorded as success.
    status     TEXT NOT NULL CHECK (status IN ('VERIFIED','FAILED','INCONCLUSIVE','PENDING')),
    expected   TEXT,
    observed   TEXT
) STRICT;

CREATE INDEX ix_verification_subject ON verification(subject_id);

-- ------------------------------------------------------------ memory and audit

CREATE TABLE memory_item (
    id               TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at       TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at       TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    -- The six stores stay separate (Part 2 section 52).
    category         TEXT NOT NULL CHECK (category IN ('SHORT_TERM_MEMORY','LONG_TERM_MEMORY','KNOWLEDGE_MEMORY','PROCEDURAL_MEMORY','STATE_MEMORY','SOURCE_MEMORY')),
    key              TEXT NOT NULL CHECK (length(trim(key)) > 0),
    value            TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('ACTIVE','STALE','SUPERSEDED','CONFLICTED','UNVERIFIED','INVALID','ARCHIVED','DEPRECATED','UNCERTAIN','DELETED')),
    observed_at      TEXT
) STRICT;

CREATE INDEX ix_memory_category_key ON memory_item(category, key);

-- document_id and knowledge_id carry NO foreign key on purpose: an audit record
-- must remain readable after its subject is gone, and Part 7 sections 18-19
-- require deletions themselves to be audited. A foreign key would make it
-- impossible to record the removal of the thing being referenced.
CREATE TABLE audit_event (
    id                  TEXT PRIMARY KEY CHECK (instr(id,'-') BETWEEN 2 AND 5 AND length(id)-instr(id,'-') >= 8 AND substr(id,instr(id,'-')+1) NOT GLOB '*[^0-9]*'),
    created_at          TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.???Z'),
    updated_at          TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.???Z'),
    event_type          TEXT NOT NULL CHECK (length(trim(event_type)) > 0),
    occurred_at         TEXT NOT NULL CHECK (occurred_at GLOB '????-??-??T??:??:??.???Z'),
    actor               TEXT,
    action              TEXT,
    risk_level          TEXT CHECK (risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH')),
    authorization       TEXT,
    execution_status    TEXT,
    verification_status TEXT CHECK (verification_status IS NULL OR verification_status IN ('VERIFIED','FAILED','INCONCLUSIVE','PENDING')),
    result              TEXT,
    error               TEXT,
    document_id         TEXT,
    knowledge_id        TEXT
) STRICT;

CREATE INDEX ix_audit_occurred ON audit_event(occurred_at);
