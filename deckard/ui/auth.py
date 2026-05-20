"""Cookie-based ApiUser resolution for the tester UI.

The login form stores the raw `deckard_live_*` key in an httpOnly cookie.
Every UI request resolves the ApiUser by re-validating that key through the
same hash + lookup the JSON API uses. No session table; if stronger isolation
is needed later, swap the cookie value for a session id and keep this
dependency surface unchanged.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from deckard.auth import API_KEY_PREFIX, hash_api_key
from deckard.config import get_settings
from deckard.database.models import ApiUser
from deckard.database.session import get_session

LOGIN_PATH = "/ui/login"


async def lookup_api_user_by_key(session: AsyncSession, key: str) -> ApiUser | None:
    """Return the ApiUser owning this raw key, or None if it doesn't match a row."""
    if not key.startswith(API_KEY_PREFIX):
        return None
    result = await session.execute(select(ApiUser).where(ApiUser.api_key_hash == hash_api_key(key)))
    return result.scalar_one_or_none()


async def get_current_ui_api_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApiUser:
    settings = get_settings()
    key = request.cookies.get(settings.ui_cookie_name)
    if not key:
        raise _redirect_to_login()

    api_user = await lookup_api_user_by_key(session, key)
    if api_user is None:
        raise _redirect_to_login()

    await session.execute(
        update(ApiUser).where(ApiUser.id == api_user.id).values(api_key_last_used_at=datetime.now(UTC))
    )
    return api_user


CurrentUiApiUser = Annotated[ApiUser, Depends(get_current_ui_api_user)]


def set_session_cookie(response: Response, key: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.ui_cookie_name,
        value=key,
        max_age=settings.ui_cookie_max_age_seconds,
        httponly=True,
        secure=settings.environment != "local",
        samesite="strict",
        path="/ui",
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(key=settings.ui_cookie_name, path="/ui")


def _redirect_to_login() -> HTTPException:
    # 303 forces the browser to GET the login page even if the original request was a POST.
    return HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": LOGIN_PATH})
