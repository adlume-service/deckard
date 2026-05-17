"""Initial schema

Revision ID: ddd42aa478ea
Revises:
Create Date: 2026-05-16 22:26:26.468229+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ddd42aa478ea"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("identifier", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identifier"),
    )
    op.create_table(
        "websites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "url", name="uq_websites_client_url"),
    )
    op.create_table(
        "scraping_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("website_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("requested_url", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "error_details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'scraping', 'scraped', 'processing', 'completed', 'failed', 'cancelled')",
            name="ck_scraping_requests_status",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
        ),
        sa.ForeignKeyConstraint(
            ["website_id"],
            ["websites.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "idempotency_key", name="uq_scraping_requests_client_idempotency_key"),
    )
    op.create_index("ix_scraping_requests_client_id", "scraping_requests", ["client_id"], unique=False)
    op.create_index("ix_scraping_requests_next_attempt_at", "scraping_requests", ["next_attempt_at"], unique=False)
    op.create_index(
        "ix_scraping_requests_requested_at", "scraping_requests", [sa.literal_column("requested_at DESC")], unique=False
    )
    op.create_index("ix_scraping_requests_status", "scraping_requests", ["status"], unique=False)
    op.create_index("ix_scraping_requests_website_id", "scraping_requests", ["website_id"], unique=False)
    op.create_table(
        "llm_processing_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scraping_request_id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(), server_default=sa.text("'default_extraction'"), nullable=False),
        sa.Column("status", sa.String(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("prompt_version", sa.String(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "error_details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')",
            name="ck_llm_processing_jobs_status",
        ),
        sa.ForeignKeyConstraint(["scraping_request_id"], ["scraping_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_processing_jobs_job_type", "llm_processing_jobs", ["job_type"], unique=False)
    op.create_index("ix_llm_processing_jobs_next_attempt_at", "llm_processing_jobs", ["next_attempt_at"], unique=False)
    op.create_index(
        "ix_llm_processing_jobs_scraping_request_id", "llm_processing_jobs", ["scraping_request_id"], unique=False
    )
    op.create_index("ix_llm_processing_jobs_status", "llm_processing_jobs", ["status"], unique=False)
    op.create_table(
        "scraping_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scraping_request_id", sa.Uuid(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("markdown", sa.Text(), nullable=True),
        sa.Column("cleaned_html", sa.Text(), nullable=True),
        sa.Column("raw_html", sa.Text(), nullable=True),
        sa.Column("content_storage_type", sa.String(), server_default=sa.text("'database'"), nullable=False),
        sa.Column("markdown_storage_uri", sa.Text(), nullable=True),
        sa.Column("cleaned_html_storage_uri", sa.Text(), nullable=True),
        sa.Column("raw_html_storage_uri", sa.Text(), nullable=True),
        sa.Column("success", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["scraping_request_id"], ["scraping_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scraping_request_id"),
    )
    op.create_index(
        "ix_scraping_results_metadata_gin", "scraping_results", ["metadata"], unique=False, postgresql_using="gin"
    )
    op.create_index(
        "ix_scraping_results_scraping_request_id", "scraping_results", ["scraping_request_id"], unique=False
    )
    op.create_table(
        "llm_outputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("llm_processing_job_id", sa.Uuid(), nullable=False),
        sa.Column("output_schema", sa.String(), nullable=True),
        sa.Column("output_schema_version", sa.String(), nullable=True),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["llm_processing_job_id"], ["llm_processing_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("llm_processing_job_id"),
    )
    op.create_index("ix_llm_outputs_llm_processing_job_id", "llm_outputs", ["llm_processing_job_id"], unique=False)
    op.create_index("ix_llm_outputs_output_gin", "llm_outputs", ["output"], unique=False, postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_llm_outputs_output_gin", table_name="llm_outputs", postgresql_using="gin")
    op.drop_index("ix_llm_outputs_llm_processing_job_id", table_name="llm_outputs")
    op.drop_table("llm_outputs")
    op.drop_index("ix_scraping_results_scraping_request_id", table_name="scraping_results")
    op.drop_index("ix_scraping_results_metadata_gin", table_name="scraping_results", postgresql_using="gin")
    op.drop_table("scraping_results")
    op.drop_index("ix_llm_processing_jobs_status", table_name="llm_processing_jobs")
    op.drop_index("ix_llm_processing_jobs_scraping_request_id", table_name="llm_processing_jobs")
    op.drop_index("ix_llm_processing_jobs_next_attempt_at", table_name="llm_processing_jobs")
    op.drop_index("ix_llm_processing_jobs_job_type", table_name="llm_processing_jobs")
    op.drop_table("llm_processing_jobs")
    op.drop_index("ix_scraping_requests_website_id", table_name="scraping_requests")
    op.drop_index("ix_scraping_requests_status", table_name="scraping_requests")
    op.drop_index("ix_scraping_requests_requested_at", table_name="scraping_requests")
    op.drop_index("ix_scraping_requests_next_attempt_at", table_name="scraping_requests")
    op.drop_index("ix_scraping_requests_client_id", table_name="scraping_requests")
    op.drop_table("scraping_requests")
    op.drop_table("websites")
    op.drop_table("clients")
