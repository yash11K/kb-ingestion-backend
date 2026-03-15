-- Add full-text search support to kb_files for knowledge base querying.
-- Run with: python run_migration.py src/db/migrations/006_fulltext_search.sql

-- Generated tsvector column combining title and markdown content
ALTER TABLE kb_files ADD COLUMN IF NOT EXISTS search_vector tsvector;

-- Populate for existing rows
UPDATE kb_files
SET search_vector = to_tsvector('english', coalesce(title, '') || ' ' || coalesce(md_content, ''));

-- GIN index for fast full-text search
CREATE INDEX IF NOT EXISTS idx_kb_files_search_vector ON kb_files USING gin(search_vector);

-- Trigger to keep search_vector in sync on INSERT/UPDATE
CREATE OR REPLACE FUNCTION kb_files_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('english', coalesce(NEW.title, '') || ' ' || coalesce(NEW.md_content, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_kb_files_search_vector ON kb_files;
CREATE TRIGGER trg_kb_files_search_vector
    BEFORE INSERT OR UPDATE OF title, md_content ON kb_files
    FOR EACH ROW EXECUTE FUNCTION kb_files_search_vector_update();
