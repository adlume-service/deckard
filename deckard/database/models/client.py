import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from deckard.database.base import Base

if TYPE_CHECKING:
    from deckard.database.models.scraping_request import ScrapingRequest
    from deckard.database.models.website import Website


class Client(Base):
    """A tenant whose websites are scraped and whose UUID is tracked for billing.

    Distinct from `ApiUser`: an ApiUser is the calling server. One ApiUser
    submits scraping requests for many Clients; the Client is referenced
    per-request by `client_identifier`.
    """

    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    identifier: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    websites: Mapped[list[Website]] = relationship(back_populates="client", passive_deletes=True)
    scraping_requests: Mapped[list[ScrapingRequest]] = relationship(back_populates="client")
