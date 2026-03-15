-- Migration 005: Make aem_node_id nullable on kb_files.
--
-- The deep-crawl refactor replaced aem_node_id with the 'key' column
-- (added in migration 004). Existing rows retain their aem_node_id values;
-- new rows will have NULL since the insert query no longer provides it.

ALTER TABLE kb_files
    ALTER COLUMN aem_node_id DROP NOT NULL,
    ALTER COLUMN aem_node_id SET DEFAULT NULL;
