"""Convert validation_breakdown and validation_issues from TEXT to JSONB.

Existing rows contain JSON stored as plain text strings. This migration
casts them to JSONB so PostgreSQL returns parsed objects and enables
native JSON indexing/querying.

Revision ID: 002
Revises: 220b48740c44
Create Date: 2026-03-23 15:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "220b48740c44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE kb_files "
        "ALTER COLUMN validation_breakdown TYPE JSONB USING validation_breakdown::jsonb"
    )
    op.execute(
        "ALTER TABLE kb_files "
        "ALTER COLUMN validation_issues TYPE JSONB USING validation_issues::jsonb"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE kb_files "
        "ALTER COLUMN validation_breakdown TYPE TEXT USING validation_breakdown::text"
    )
    op.execute(
        "ALTER TABLE kb_files "
        "ALTER COLUMN validation_issues TYPE TEXT USING validation_issues::text"
    )
