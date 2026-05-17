"""scraping_results 1:1 -> 1:N + url column

Revision ID: 1de3f5da4a4c
Revises: ddd42aa478ea
Create Date: 2026-05-17 16:21:17.368804+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1de3f5da4a4c"
down_revision: str | Sequence[str] | None = "ddd42aa478ea"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scraping_results", sa.Column("url", sa.Text(), nullable=False))
    op.drop_constraint(op.f("scraping_results_scraping_request_id_key"), "scraping_results", type_="unique")
    op.create_index("ix_scraping_results_url", "scraping_results", ["url"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_scraping_results_url", table_name="scraping_results")
    op.create_unique_constraint(
        op.f("scraping_results_scraping_request_id_key"),
        "scraping_results",
        ["scraping_request_id"],
        postgresql_nulls_not_distinct=False,
    )
    op.drop_column("scraping_results", "url")
