-- Full schema: drops all tables and recreates from scratch.
-- Run with: python run_migration.py src/db/migrations/001_full_schema.sql
--
-- Derived from all queries in src/db/queries.py, src/services/pipeline.py,
-- src/services/revalidation.py, and src/models/schemas.py.

DROP TABLE IF EXISTS kb_files CASCADE;
DROP TABLE IF EXISTS ingestion_jobs CASCADE;
DROP TABLE IF EXISTS revalidation_jobs CASCADE;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- -----------------------------------------------------------------
-- kb_files
-- -----------------------------------------------------------------
-- Columns used by insert_kb_file():
--   filename, title, content_type, content_hash, source_url,
--   component_type, aem_node_id, md_content, modify_date,
--   parent_context, region, brand, validation_score,
--   validation_breakdown, validation_issues, status
--
-- Columns set by update_kb_file_status() via kwargs:
--   doc_type, s3_bucket, s3_key, s3_uploaded_at,
--   reviewed_by, reviewed_at, review_notes,
--   validation_score, validation_breakdown, validation_issues
-- -----------------------------------------------------------------
CREATE TABLE kb_files (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filename             TEXT NOT NULL,
    title                TEXT NOT NULL,
    content_type         TEXT NOT NULL,
    content_hash         TEXT NOT NULL,
    source_url           TEXT NOT NULL,
    component_type       TEXT NOT NULL,
    aem_node_id          TEXT,
    md_content           TEXT NOT NULL,
    doc_type             TEXT,
    modify_date          TIMESTAMPTZ,
    parent_context       TEXT,
    region               TEXT NOT NULL,
    brand                TEXT NOT NULL,
    validation_score     FLOAT,
    validation_breakdown JSONB,
    validation_issues    JSONB,
    status               TEXT NOT NULL DEFAULT 'pending_review',
    s3_bucket            TEXT,
    s3_key               TEXT,
    s3_uploaded_at       TIMESTAMPTZ,
    reviewed_by          TEXT,
    reviewed_at          TIMESTAMPTZ,
    review_notes         TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------
-- ingestion_jobs
-- -----------------------------------------------------------------
-- Columns used by insert_ingestion_job(): source_url, status, started_at
-- Columns set by update_ingestion_job() via kwargs:
--   total_nodes_found, files_created, files_auto_approved,
--   files_pending_review, files_auto_rejected, duplicates_skipped,
--   status, error_message, completed_at
-- -----------------------------------------------------------------
CREATE TABLE ingestion_jobs (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_url           TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'in_progress',
    total_nodes_found    INTEGER,
    files_created        INTEGER NOT NULL DEFAULT 0,
    files_auto_approved  INTEGER NOT NULL DEFAULT 0,
    files_pending_review INTEGER NOT NULL DEFAULT 0,
    files_auto_rejected  INTEGER NOT NULL DEFAULT 0,
    duplicates_skipped   INTEGER NOT NULL DEFAULT 0,
    error_message        TEXT,
    started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at         TIMESTAMPTZ
);

-- -----------------------------------------------------------------
-- revalidation_jobs
-- -----------------------------------------------------------------
-- Columns used by insert_revalidation_job(): total_files, status, started_at
-- Columns set by update_revalidation_job() via kwargs:
--   completed, failed, not_found, status, error_message, completed_at
-- -----------------------------------------------------------------
CREATE TABLE revalidation_jobs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    status          TEXT NOT NULL DEFAULT 'in_progress',
    total_files     INTEGER NOT NULL,
    completed       INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    not_found       INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

-- -----------------------------------------------------------------
-- Indexes
-- -----------------------------------------------------------------

-- kb_files: used by find_by_content_hash, list_kb_files filters, ORDER BY
CREATE INDEX idx_kb_files_content_hash  ON kb_files (content_hash);
CREATE INDEX idx_kb_files_status        ON kb_files (status);
CREATE INDEX idx_kb_files_region        ON kb_files (region);
CREATE INDEX idx_kb_files_brand         ON kb_files (brand);
CREATE INDEX idx_kb_files_source_url    ON kb_files (source_url);
CREATE INDEX idx_kb_files_content_type  ON kb_files (content_type);
CREATE INDEX idx_kb_files_doc_type      ON kb_files (doc_type);
CREATE INDEX idx_kb_files_created_at    ON kb_files (created_at);

-- ingestion_jobs
CREATE INDEX idx_ingestion_jobs_status  ON ingestion_jobs (status);

-- revalidation_jobs
CREATE INDEX idx_revalidation_jobs_status ON revalidation_jobs (status);
