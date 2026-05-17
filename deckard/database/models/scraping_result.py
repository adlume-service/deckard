import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from deckard.database.base import Base

if TYPE_CHECKING:
    from deckard.database.models.scraping_request import ScrapingRequest


class ScrapingResult(Base):
    """A single page scraped as part of a ScrapingRequest. One request can produce many results."""

    __tablename__ = "scraping_results"
    __table_args__ = (
        Index("ix_scraping_results_scraping_request_id", "scraping_request_id"),
        Index("ix_scraping_results_url", "url"),
        Index(
            "ix_scraping_results_metadata_gin",
            "metadata",
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    scraping_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("scraping_requests.id", ondelete="CASCADE"),
        nullable=False,
    )

    url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    cleaned_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_html: Mapped[str | None] = mapped_column(Text, nullable=True)

    content_storage_type: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'database'"))
    markdown_storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    cleaned_html_storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_html_storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    success: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    result_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    scraping_request: Mapped[ScrapingRequest] = relationship(back_populates="scraping_results")
