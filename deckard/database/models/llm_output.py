import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from deckard.database.base import Base

if TYPE_CHECKING:
    from deckard.database.models.llm_processing_job import LLMProcessingJob


class LLMOutput(Base):
    """The structured extraction produced by a single LLMProcessingJob."""

    __tablename__ = "llm_outputs"
    __table_args__ = (
        Index("ix_llm_outputs_llm_processing_job_id", "llm_processing_job_id"),
        Index(
            "ix_llm_outputs_output_gin",
            "output",
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    llm_processing_job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("llm_processing_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    output_schema: Mapped[str | None] = mapped_column(String, nullable=True)
    output_schema_version: Mapped[str | None] = mapped_column(String, nullable=True)

    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    llm_processing_job: Mapped[LLMProcessingJob] = relationship(back_populates="llm_output")
