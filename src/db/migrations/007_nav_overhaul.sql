-- Migration 007: Navigation-driven ingestion overhaul
--
-- Adds nav_tree_cache for parsed navigation trees, deep_links for
-- embedded link discovery, and enriches sources with nav context.

-- 1. Cache parsed navigation trees to avoid re-fetching/re-parsing
CREATE TABLE IF NOT EXISTS nav_tree_cache (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    root_url   TEXT UNIQUE NOT NULL,
    brand      TEXT NOT NULL,
    region     TEXT NOT NULL,
    tree_data  JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- 2. Discovered deep links pending user confirmation
CREATE TABLE IF NOT EXISTS deep_links (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id      UUID REFERENCES sources(id),
    job_id         UUID REFERENCES ingestion_jobs(id),
    url            TEXT NOT NULL,
    model_json_url TEXT NOT NULL,
    anchor_text    TEXT,
    found_in_node  TEXT,
    found_in_page  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deep_links_source ON deep_links(source_id);
CREATE INDEX IF NOT EXISTS idx_deep_links_status ON deep_links(status);
CREATE INDEX IF NOT EXISTS idx_deep_links_job    ON deep_links(job_id);

-- 3. Enrich sources with navigation context
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sources' AND column_name = 'nav_root_url'
    ) THEN
        ALTER TABLE sources ADD COLUMN nav_root_url TEXT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sources' AND column_name = 'nav_label'
    ) THEN
        ALTER TABLE sources ADD COLUMN nav_label TEXT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sources' AND column_name = 'nav_section'
    ) THEN
        ALTER TABLE sources ADD COLUMN nav_section TEXT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sources' AND column_name = 'page_path'
    ) THEN
        ALTER TABLE sources ADD COLUMN page_path TEXT;
    END IF;
END
$$;
