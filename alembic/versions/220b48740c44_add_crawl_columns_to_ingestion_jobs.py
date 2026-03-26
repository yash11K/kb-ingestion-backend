"""add crawl columns to ingestion_jobs

Revision ID: 220b48740c44
Revises: 001
Create Date: 2026-03-23 14:39:28.782407

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '220b48740c44'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ingestion_jobs', sa.Column('child_urls', sa.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False))
    op.add_column('kb_files', sa.Column('search_vector', postgresql.TSVECTOR(), nullable=True))
    op.create_index('idx_kb_files_search_vector', 'kb_files', ['search_vector'], unique=False, postgresql_using='gin')


def downgrade() -> None:
    op.drop_index('idx_kb_files_search_vector', table_name='kb_files', postgresql_using='gin')
    op.drop_column('kb_files', 'search_vector')
    op.drop_column('ingestion_jobs', 'child_urls')
