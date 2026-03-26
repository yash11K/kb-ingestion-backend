"""Baseline migration: complete schema from SQL migrations 001-007.

Creates all 6 tables (sources, ingestion_jobs, kb_files, revalidation_jobs,
nav_tree_cache, deep_links) with columns, indexes, constraints, foreign keys,
the uuid-ossp extension, and the full-text search trigger on kb_files.

Revision ID: 001
Revises: None
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID, TSVECTOR

# revision identifiers, used by Alembic.
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable uuid-ossp extension for uuid_generate_v4()
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # -----------------------------------------------------------------
    # sources
    # -----------------------------------------------------------------
    op.create_table(
        "sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("nav_root_url", sa.Text(), nullable=True),
        sa.Column("nav_label", sa.Text(), nullable=True),
        sa.Column("nav_section", sa.Text(), nullable=True),
        sa.Column("page_path", sa.Text(), nullable=True),
        sa.Column("last_ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_sources_region", "sources", ["region"])
    op.create_index("idx_sources_brand", "sources", ["brand"])
    op.create_index("idx_sources_url", "sources", ["url"])

    # -----------------------------------------------------------------
    # ingestion_jobs
    # -----------------------------------------------------------------
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'in_progress'")),
        sa.Column("total_nodes_found", sa.Integer(), nullable=True),
        sa.Column("files_created", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("files_auto_approved", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("files_pending_review", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("files_auto_rejected", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("duplicates_skipped", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("child_urls", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")),
        sa.Column("max_depth", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pages_crawled", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("current_depth", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_ingestion_jobs_status", "ingestion_jobs", ["status"])
    op.create_index("idx_ingestion_jobs_source_id", "ingestion_jobs", ["source_id"])

    # -----------------------------------------------------------------
    # kb_files
    # -----------------------------------------------------------------
    op.create_table(
        "kb_files",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("component_type", sa.Text(), nullable=False),
        sa.Column("aem_node_id", sa.Text(), nullable=True),
        sa.Column("md_content", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=True),
        sa.Column("modify_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parent_context", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=True),
        sa.Column("namespace", sa.Text(), nullable=True),
        sa.Column("validation_score", sa.Float(), nullable=True),
        sa.Column("validation_breakdown", JSONB(), nullable=True),
        sa.Column("validation_issues", JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending_review'")),
        sa.Column("s3_bucket", sa.Text(), nullable=True),
        sa.Column("s3_key", sa.Text(), nullable=True),
        sa.Column("s3_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("ingestion_jobs.id"), nullable=True),
        sa.Column("search_vector", TSVECTOR(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_kb_files_content_hash", "kb_files", ["content_hash"])
    op.create_index("idx_kb_files_status", "kb_files", ["status"])
    op.create_index("idx_kb_files_region", "kb_files", ["region"])
    op.create_index("idx_kb_files_brand", "kb_files", ["brand"])
    op.create_index("idx_kb_files_source_url", "kb_files", ["source_url"])
    op.create_index("idx_kb_files_content_type", "kb_files", ["content_type"])
    op.create_index("idx_kb_files_doc_type", "kb_files", ["doc_type"])
    op.create_index("idx_kb_files_created_at", "kb_files", ["created_at"])
    op.create_index("idx_kb_files_source_id", "kb_files", ["source_id"])
    op.create_index("idx_kb_files_job_id", "kb_files", ["job_id"])
    op.create_index("idx_kb_files_search_vector", "kb_files", ["search_vector"], postgresql_using="gin")

    # -----------------------------------------------------------------
    # revalidation_jobs
    # -----------------------------------------------------------------
    op.create_table(
        "revalidation_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'in_progress'")),
        sa.Column("total_files", sa.Integer(), nullable=False),
        sa.Column("completed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("not_found", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_revalidation_jobs_status", "revalidation_jobs", ["status"])

    # -----------------------------------------------------------------
    # nav_tree_cache
    # -----------------------------------------------------------------
    op.create_table(
        "nav_tree_cache",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("root_url", sa.Text(), nullable=False, unique=True),
        sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("tree_data", JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    # -----------------------------------------------------------------
    # deep_links
    # -----------------------------------------------------------------
    op.create_table(
        "deep_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("ingestion_jobs.id"), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("model_json_url", sa.Text(), nullable=False),
        sa.Column("anchor_text", sa.Text(), nullable=True),
        sa.Column("found_in_node", sa.Text(), nullable=True),
        sa.Column("found_in_page", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_deep_links_source", "deep_links", ["source_id"])
    op.create_index("idx_deep_links_status", "deep_links", ["status"])
    op.create_index("idx_deep_links_job", "deep_links", ["job_id"])

    # -----------------------------------------------------------------
    # Full-text search trigger function and trigger on kb_files
    # -----------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION kb_files_search_vector_update() RETURNS trigger AS $$
        BEGIN
            NEW.search_vector := to_tsvector('english', coalesce(NEW.title, '') || ' ' || coalesce(NEW.md_content, ''));
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_kb_files_search_vector
            BEFORE INSERT OR UPDATE OF title, md_content ON kb_files
            FOR EACH ROW EXECUTE FUNCTION kb_files_search_vector_update();
        """
    )


def downgrade() -> None:
    # Drop trigger and function first (depends on kb_files table)
    op.execute("DROP TRIGGER IF EXISTS trg_kb_files_search_vector ON kb_files")
    op.execute("DROP FUNCTION IF EXISTS kb_files_search_vector_update()")

    # Drop tables in reverse dependency order
    op.drop_table("deep_links")
    op.drop_table("nav_tree_cache")
    op.drop_table("revalidation_jobs")
    op.drop_table("kb_files")
    op.drop_table("ingestion_jobs")
    op.drop_table("sources")

    # Drop extension
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
