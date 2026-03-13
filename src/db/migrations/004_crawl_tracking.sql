-- Migration 004: Add crawl tracking columns to ingestion_jobs
-- and key/namespace columns to kb_files for deep crawl ingestion.
--
-- ingestion_jobs gains:
--   max_depth      – the effective (clamped) crawl depth for this job
--   pages_crawled  – running count of URLs processed during the crawl
--   current_depth  – the BFS depth level currently being processed
--
-- kb_files gains:
--   key        – AEM component key name (e.g. "contentcardelement_821372053")
--   namespace  – content category inferred from the AEM URL path

ALTER TABLE ingestion_jobs
    ADD COLUMN IF NOT EXISTS max_depth      INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS pages_crawled  INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS current_depth  INTEGER NOT NULL DEFAULT 0;

ALTER TABLE kb_files
    ADD COLUMN IF NOT EXISTS key       TEXT,
    ADD COLUMN IF NOT EXISTS namespace TEXT;
