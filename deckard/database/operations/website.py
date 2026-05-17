import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deckard.database.models import Website


async def get_by_client_and_url(session: AsyncSession, client_id: uuid.UUID, url: str) -> Website | None:
    result = await session.execute(
        select(Website).where(
            Website.client_id == client_id,
            Website.url == url,
        )
    )
    return result.scalar_one_or_none()


async def get_or_create_for_client(session: AsyncSession, client_id: uuid.UUID, url: str) -> Website:
    website = await get_by_client_and_url(session, client_id, url)
    if website is not None:
        return website

    website = Website(client_id=client_id, url=url)
    session.add(website)
    await session.flush()
    return website
