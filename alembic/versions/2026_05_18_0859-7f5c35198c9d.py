"""add llm_calls table and drop per-call columns from llm_processing_jobs

Revision ID: 7f5c35198c9d
Revises: 7ab8152ba815
Create Date: 2026-05-18 08:59:22.222862+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f5c35198c9d"
down_revision: str | Sequence[str] | None = "7ab8152ba815"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("llm_processing_job_id", sa.Uuid(), nullable=False),
        sa.Column("call_type", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("provider_response_id", sa.String(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("call_type IN ('extractor', 'ranker')", name="ck_llm_calls_call_type"),
        sa.ForeignKeyConstraint(["llm_processing_job_id"], ["llm_processing_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_calls_call_type", "llm_calls", ["call_type"], unique=False)
    op.create_index("ix_llm_calls_llm_processing_job_id", "llm_calls", ["llm_processing_job_id"], unique=False)
    op.drop_column("llm_processing_jobs", "provider")
    op.drop_column("llm_processing_jobs", "output_tokens")
    op.drop_column("llm_processing_jobs", "provider_response_id")
    op.drop_column("llm_processing_jobs", "input_tokens")
    op.drop_column("llm_processing_jobs", "model")


def downgrade() -> None:
    op.add_column("llm_processing_jobs", sa.Column("model", sa.VARCHAR(), autoincrement=False, nullable=True))
    op.add_column("llm_processing_jobs", sa.Column("input_tokens", sa.INTEGER(), autoincrement=False, nullable=True))
    op.add_column(
        "llm_processing_jobs", sa.Column("provider_response_id", sa.VARCHAR(), autoincrement=False, nullable=True)
    )
    op.add_column("llm_processing_jobs", sa.Column("output_tokens", sa.INTEGER(), autoincrement=False, nullable=True))
    op.add_column("llm_processing_jobs", sa.Column("provider", sa.VARCHAR(), autoincrement=False, nullable=True))
    op.drop_index("ix_llm_calls_llm_processing_job_id", table_name="llm_calls")
    op.drop_index("ix_llm_calls_call_type", table_name="llm_calls")
    op.drop_table("llm_calls")
