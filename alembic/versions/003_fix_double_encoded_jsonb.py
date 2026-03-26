"""Fix double-encoded JSONB values in kb_files.

When TEXT columns containing JSON strings were cast to JSONB, the values
became JSONB string scalars (e.g. '"{\\"key\\": \\"val\\"}"') instead of
JSONB objects. This migration unwraps them by casting the inner string.

Revision ID: 003
Revises: 002
Create Date: 2026-03-23 15:10:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Where the JSONB value is a string (jsonb_typeof = 'string'),
    # extract the text and re-cast it to proper JSONB.
    op.execute(
        "UPDATE kb_files "
        "SET validation_breakdown = (validation_breakdown #>> '{}')::jsonb "
        "WHERE validation_breakdown IS NOT NULL "
        "AND jsonb_typeof(validation_breakdown) = 'string'"
    )
    op.execute(
        "UPDATE kb_files "
        "SET validation_issues = (validation_issues #>> '{}')::jsonb "
        "WHERE validation_issues IS NOT NULL "
        "AND jsonb_typeof(validation_issues) = 'string'"
    )
    op.execute(
        "UPDATE nav_tree_cache "
        "SET tree_data = (tree_data #>> '{}')::jsonb "
        "WHERE tree_data IS NOT NULL "
        "AND jsonb_typeof(tree_data) = 'string'"
    )


def downgrade() -> None:
    # Re-wrap as JSONB string scalars (reverse the unwrap)
    op.execute(
        "UPDATE kb_files "
        "SET validation_breakdown = to_jsonb(validation_breakdown::text) "
        "WHERE validation_breakdown IS NOT NULL "
        "AND jsonb_typeof(validation_breakdown) = 'object'"
    )
    op.execute(
        "UPDATE kb_files "
        "SET validation_issues = to_jsonb(validation_issues::text) "
        "WHERE validation_issues IS NOT NULL "
        "AND jsonb_typeof(validation_issues) IN ('object', 'array')"
    )
