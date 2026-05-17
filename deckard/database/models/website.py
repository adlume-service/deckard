import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from deckard.database.base import Base

if TYPE_CHECKING:
    from deckard.database.models.client import Client
    from deckard.database.models.scraping_request import ScrapingRequest


class Website(Base):
    """A website registered against a client; the unit being scraped."""

    __tablename__ = "websites"
    __table_args__ = (UniqueConstraint("client_id", "url", name="uq_websites_client_url"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )

    url: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    client: Mapped[Client] = relationship(back_populates="websites")
    scraping_requests: Mapped[list[ScrapingRequest]] = relationship(back_populates="website")
