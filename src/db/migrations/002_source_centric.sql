-- Migration: Source-centric ingestion model
-- Introduces a `sources` table as the primary entity, with ingestion_jobs
-- and kb_files linked to it via foreign keys.

-- -----------------------------------------------------------------
-- sources
-- -----------------------------------------------------------------
CREATE TABLE sources (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    url               TEXT NOT NULL UNIQUE,
    region            TEXT NOT NULL,
    brand             TEXT NOT NULL,
    last_ingested_at  TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_sources_region ON sources (region);
CREATE INDEX idx_sources_brand  ON sources (brand);
CREATE INDEX idx_sources_url    ON sources (url);

-- -----------------------------------------------------------------
-- Add source_id FK to ingestion_jobs
-- -----------------------------------------------------------------
ALTER TABLE ingestion_jobs
    ADD COLUMN source_id UUID REFERENCES sources(id);

CREATE INDEX idx_ingestion_jobs_source_id ON ingestion_jobs (source_id);

-- -----------------------------------------------------------------
-- Add source_id and job_id FKs to kb_files
-- -----------------------------------------------------------------
ALTER TABLE kb_files
    ADD COLUMN source_id UUID REFERENCES sources(id),
    ADD COLUMN job_id    UUID REFERENCES ingestion_jobs(id);

CREATE INDEX idx_kb_files_source_id ON kb_files (source_id);
CREATE INDEX idx_kb_files_job_id    ON kb_files (job_id);
