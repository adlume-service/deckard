import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, get_args

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from deckard.database.base import Base

if TYPE_CHECKING:
    from deckard.database.models.client import Client
    from deckard.database.models.llm_processing_job import LLMProcessingJob
    from deckard.database.models.scraping_result import ScrapingResult
    from deckard.database.models.website import Website


ScrapingRequestStatus = Literal[
    "pending",
    "scraping",
    "scraped",
    "processing",
    "completed",
    "failed",
    "cancelled",
]
SCRAPING_REQUEST_STATUSES: tuple[str, ...] = get_args(ScrapingRequestStatus)


class ScrapingRequest(Base):
    """An external request from a client to scrape a website."""

    __tablename__ = "scraping_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in SCRAPING_REQUEST_STATUSES) + ")",
            name="ck_scraping_requests_status",
        ),
        UniqueConstraint(
            "client_id",
            "idempotency_key",
            name="uq_scraping_requests_client_idempotency_key",
        ),
        Index("ix_scraping_requests_client_id", "client_id"),
        Index("ix_scraping_requests_website_id", "website_id"),
        Index("ix_scraping_requests_status", "status"),
        Index(
            "ix_scraping_requests_requested_at",
            text("requested_at DESC"),
        ),
        Index("ix_scraping_requests_next_attempt_at", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    client_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    website_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("websites.id"), nullable=False)

    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'pending'"))

    requested_url: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )

    request_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
    )

    # Relationships
    client: Mapped[Client] = relationship(back_populates="scraping_requests")
    website: Mapped[Website] = relationship(back_populates="scraping_requests")
    scraping_results: Mapped[list[ScrapingResult]] = relationship(
        back_populates="scraping_request",
        passive_deletes=True,
    )
    llm_processing_jobs: Mapped[list[LLMProcessingJob]] = relationship(
        back_populates="scraping_request",
        passive_deletes=True,
    )
