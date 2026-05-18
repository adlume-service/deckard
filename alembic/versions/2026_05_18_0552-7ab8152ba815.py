"""add tokens to scraping_results and ranker_used to llm_processing_jobs

Revision ID: 7ab8152ba815
Revises: d5aa384ed9c7
Create Date: 2026-05-18 05:52:16.279745+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7ab8152ba815"
down_revision: str | Sequence[str] | None = "d5aa384ed9c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("llm_processing_jobs", sa.Column("ranker_used", sa.Boolean(), nullable=True))
    op.add_column("scraping_results", sa.Column("tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("scraping_results", "tokens")
    op.drop_column("llm_processing_jobs", "ranker_used")
