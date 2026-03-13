-- Migration 003: Store discovered child page URLs on ingestion_jobs
--
-- child_urls holds the internal AEM page URLs (already expanded to full
-- *.model.json URLs) that were discovered from link fields (e.g. ctaLink)
-- in the content nodes extracted during a job run.  Callers can read this
-- field and submit each URL via POST /ingest to extract deeper content.

ALTER TABLE ingestion_jobs
    ADD COLUMN IF NOT EXISTS child_urls TEXT[] NOT NULL DEFAULT '{}';
