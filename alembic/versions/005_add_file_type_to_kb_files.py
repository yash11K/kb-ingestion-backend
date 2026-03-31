"""Add file_type column to kb_files and make markdown-specific columns nullable.

Adds a file_type discriminator column so kb_files can store both markdown
and PDF records. PDF records have no markdown content, so md_content,
title, content_type, and component_type are made nullable.

Revision ID: 005
Revises: 004
Create Date: 2026-03-24 10:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add file_type column — existing rows default to 'markdown'
    op.add_column(
        "kb_files",
        sa.Column(
            "file_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'markdown'"),
        ),
    )

    # Make markdown-specific columns nullable for PDF records
    op.alter_column("kb_files", "md_content", existing_type=sa.Text(), nullable=True)
    op.alter_column("kb_files", "title", existing_type=sa.Text(), nullable=True)
    op.alter_column("kb_files", "content_type", existing_type=sa.Text(), nullable=True)
    op.alter_column("kb_files", "component_type", existing_type=sa.Text(), nullable=True)

    # Index for filtered queries by file_type
    op.create_index("idx_kb_files_file_type", "kb_files", ["file_type"])


def downgrade() -> None:
    op.drop_index("idx_kb_files_file_type", table_name="kb_files")

    # Restore NOT NULL constraints (backfill NULLs first to avoid errors)
    op.execute("UPDATE kb_files SET component_type = '' WHERE component_type IS NULL")
    op.execute("UPDATE kb_files SET content_type = '' WHERE content_type IS NULL")
    op.execute("UPDATE kb_files SET title = '' WHERE title IS NULL")
    op.execute("UPDATE kb_files SET md_content = '' WHERE md_content IS NULL")

    op.alter_column("kb_files", "component_type", existing_type=sa.Text(), nullable=False)
    op.alter_column("kb_files", "content_type", existing_type=sa.Text(), nullable=False)
    op.alter_column("kb_files", "title", existing_type=sa.Text(), nullable=False)
    op.alter_column("kb_files", "md_content", existing_type=sa.Text(), nullable=False)

    op.drop_column("kb_files", "file_type")
