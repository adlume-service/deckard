import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, get_args

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
    from deckard.database.models.llm_call import LLMCall
    from deckard.database.models.llm_output import LLMOutput
    from deckard.database.models.scraping_request import ScrapingRequest


LLMProcessingJobStatus = Literal[
    "pending",
    "processing",
    "completed",
    "failed",
    "cancelled",
]
LLM_PROCESSING_JOB_STATUSES: tuple[str, ...] = get_args(LLMProcessingJobStatus)


class LLMProcessingJob(Base):
    """An internal LLM extraction job derived from a scraped page."""

    __tablename__ = "llm_processing_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in LLM_PROCESSING_JOB_STATUSES) + ")",
            name="ck_llm_processing_jobs_status",
        ),
        Index("ix_llm_processing_jobs_scraping_request_id", "scraping_request_id"),
        Index("ix_llm_processing_jobs_status", "status"),
        Index("ix_llm_processing_jobs_job_type", "job_type"),
        Index("ix_llm_processing_jobs_next_attempt_at", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    scraping_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("scraping_requests.id", ondelete="CASCADE"),
        nullable=False,
    )

    job_type: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'default_extraction'"))
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'pending'"))

    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    ranker_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )

    job_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
    )

    # Relationships
    scraping_request: Mapped[ScrapingRequest] = relationship(back_populates="llm_processing_jobs")
    llm_output: Mapped[LLMOutput | None] = relationship(
        back_populates="llm_processing_job",
        uselist=False,
        passive_deletes=True,
    )
    calls: Mapped[list[LLMCall]] = relationship(
        back_populates="llm_processing_job",
        passive_deletes=True,
    )
