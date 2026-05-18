"""add api_users and link scraping_requests

Revision ID: cd4f676f46a2
Revises: 7f5c35198c9d
Create Date: 2026-05-18 11:27:31.802858+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cd4f676f46a2"
down_revision: str | Sequence[str] | None = "7f5c35198c9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("api_key_hash", sa.String(length=64), nullable=False),
        sa.Column("api_key_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("api_key_last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_users_api_key_hash"), "api_users", ["api_key_hash"], unique=True)

    op.add_column("scraping_requests", sa.Column("api_user_id", sa.Uuid(), nullable=True))
    op.drop_constraint(
        op.f("uq_scraping_requests_client_idempotency_key"),
        "scraping_requests",
        type_="unique",
    )
    op.create_index("ix_scraping_requests_api_user_id", "scraping_requests", ["api_user_id"], unique=False)
    op.create_unique_constraint(
        "uq_scraping_requests_api_user_idempotency_key",
        "scraping_requests",
        ["api_user_id", "idempotency_key"],
    )
    op.create_foreign_key(
        "fk_scraping_requests_api_user_id_api_users",
        "scraping_requests",
        "api_users",
        ["api_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_scraping_requests_api_user_id_api_users",
        "scraping_requests",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_scraping_requests_api_user_idempotency_key",
        "scraping_requests",
        type_="unique",
    )
    op.drop_index("ix_scraping_requests_api_user_id", table_name="scraping_requests")
    op.create_unique_constraint(
        op.f("uq_scraping_requests_client_idempotency_key"),
        "scraping_requests",
        ["client_id", "idempotency_key"],
    )
    op.drop_column("scraping_requests", "api_user_id")
    op.drop_index(op.f("ix_api_users_api_key_hash"), table_name="api_users")
    op.drop_table("api_users")
