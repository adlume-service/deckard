"""add provider_response_id to llm_processing_jobs

Revision ID: d5aa384ed9c7
Revises: 1de3f5da4a4c
Create Date: 2026-05-17 18:26:54.263102+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5aa384ed9c7"
down_revision: str | Sequence[str] | None = "1de3f5da4a4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("llm_processing_jobs", sa.Column("provider_response_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_processing_jobs", "provider_response_id")
