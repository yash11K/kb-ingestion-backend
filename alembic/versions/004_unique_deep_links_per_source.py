"""Add unique constraint on deep_links (source_id, url).

Prevents duplicate deep link rows when the same URL is discovered
from multiple pages within the same source.

Revision ID: 004
Revises: 003
Create Date: 2026-03-23 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove existing duplicates before adding the constraint:
    # keep the earliest row (min id) per (source_id, url) pair.
    op.execute(
        "DELETE FROM deep_links a "
        "USING deep_links b "
        "WHERE a.source_id = b.source_id "
        "AND a.url = b.url "
        "AND a.created_at > b.created_at"
    )
    op.create_unique_constraint(
        "uq_deep_links_source_url", "deep_links", ["source_id", "url"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_deep_links_source_url", "deep_links", type_="unique")
