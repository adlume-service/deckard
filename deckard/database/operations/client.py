from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deckard.database.models import Client


async def get_by_identifier(session: AsyncSession, identifier: str) -> Client | None:
    result = await session.execute(select(Client).where(Client.identifier == identifier))
    return result.scalar_one_or_none()


async def get_or_create_by_identifier(session: AsyncSession, identifier: str) -> Client:
    client = await get_by_identifier(session, identifier)
    if client is not None:
        return client

    client = Client(identifier=identifier)
    session.add(client)
    await session.flush()
    return client
