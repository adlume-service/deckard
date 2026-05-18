from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deckard.auth import generate_api_key, hash_api_key
from deckard.database.models import ApiUser


async def create_with_api_key(session: AsyncSession, *, name: str | None = None) -> tuple[ApiUser, str]:
    """Create a new ApiUser and issue an API key.

    Returns (api_user, plaintext_key). The plaintext is returned once and never
    persisted — only its SHA-256 hash is stored.
    """
    plaintext_key = generate_api_key()
    api_user = ApiUser(
        name=name,
        api_key_hash=hash_api_key(plaintext_key),
        api_key_created_at=datetime.now(UTC),
    )
    session.add(api_user)
    await session.flush()
    return api_user, plaintext_key


async def get_by_api_key_hash(session: AsyncSession, api_key_hash: str) -> ApiUser | None:
    result = await session.execute(select(ApiUser).where(ApiUser.api_key_hash == api_key_hash))
    return result.scalar_one_or_none()
