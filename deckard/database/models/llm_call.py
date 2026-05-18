import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Literal, get_args

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from deckard.database.base import Base

if TYPE_CHECKING:
    from deckard.database.models.llm_processing_job import LLMProcessingJob


LLMCallType = Literal["extractor", "ranker"]
LLM_CALL_TYPES: tuple[str, ...] = get_args(LLMCallType)


class LLMCall(Base):
    """One LLM API call made in service of an LLMProcessingJob.

    A single job can produce multiple calls — at minimum an extractor, plus a
    ranker when budget shrinkage fires. Total cost per job is the SUM over
    this table for that job_id.
    """

    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint(
            "call_type IN (" + ", ".join(f"'{t}'" for t in LLM_CALL_TYPES) + ")",
            name="ck_llm_calls_call_type",
        ),
        Index("ix_llm_calls_llm_processing_job_id", "llm_processing_job_id"),
        Index("ix_llm_calls_call_type", "call_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    llm_processing_job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("llm_processing_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )

    call_type: Mapped[str] = mapped_column(String, nullable=False)

    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_response_id: Mapped[str | None] = mapped_column(String, nullable=True)

    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    llm_processing_job: Mapped[LLMProcessingJob] = relationship(back_populates="calls")
