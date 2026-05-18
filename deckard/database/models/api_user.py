import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from deckard.database.base import Base

if TYPE_CHECKING:
    from deckard.database.models.scraping_request import ScrapingRequest


class ApiUser(Base):
    """An external service authorized to call the Deckard API.

    Distinct from `Client` — an ApiUser is the credential-bearing caller (e.g.
    a partner integration server), while a Client is one of the tenants that
    caller submits work for. A single ApiUser submits scraping requests for
    many Clients; Clients are referenced per-request by `client_identifier`.
    """

    __tablename__ = "api_users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name: Mapped[str | None] = mapped_column(String, nullable=True)

    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    api_key_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    api_key_last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    scraping_requests: Mapped[list[ScrapingRequest]] = relationship(back_populates="api_user")
