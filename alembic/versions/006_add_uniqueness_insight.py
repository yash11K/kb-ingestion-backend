"""Add uniqueness_insight column to kb_files.

Revision ID: 006
Revises: 005_add_file_type_to_kb_files
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kb_files", sa.Column("uniqueness_insight", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("kb_files", "uniqueness_insight")
