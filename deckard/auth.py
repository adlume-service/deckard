import hashlib
import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from deckard.database.models import ApiUser
from deckard.database.session import get_session

API_KEY_PREFIX = "deckard_live_"

_bearer_scheme = HTTPBearer(auto_error=False, description="Per-ApiUser API key issued via `cli.create_api_user`.")


def generate_api_key() -> str:
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(16)}"


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_api_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApiUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()

    token = credentials.credentials
    if not token.startswith(API_KEY_PREFIX):
        raise _unauthorized()

    token_hash = hash_api_key(token)
    result = await session.execute(select(ApiUser).where(ApiUser.api_key_hash == token_hash))
    api_user = result.scalar_one_or_none()
    if api_user is None:
        raise _unauthorized()

    await session.execute(
        update(ApiUser).where(ApiUser.id == api_user.id).values(api_key_last_used_at=datetime.now(UTC))
    )

    return api_user


CurrentApiUser = Annotated[ApiUser, Depends(get_current_api_user)]
