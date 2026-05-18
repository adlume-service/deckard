"""Shared fixtures for the test suite.

Each test gets its own AsyncSession bound to a connection inside an outer
transaction. The app's `session.commit()` calls land on a savepoint, so the
outer transaction can be rolled back at the end of the test to give full
isolation without touching schema or truncating tables.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from deckard.app import create_app
from deckard.config import get_settings
from deckard.database.models import ApiUser
from deckard.database.operations import api_user as api_user_ops
from deckard.database.session import get_session
from deckard.endpoints import scraping_requests as scraping_requests_endpoint


@dataclass
class AuthedApiUser:
    api_user: ApiUser
    api_key: str
    headers: dict[str, str]


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    # Fresh engine per test with NullPool — sidesteps cross-event-loop pool reuse
    # that pytest-asyncio function-scope tests would otherwise trigger.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            sessionmaker = async_sessionmaker(
                bind=connection,
                class_=AsyncSession,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            async with sessionmaker() as session:
                yield session
            await outer.rollback()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def http_client(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    # Background tasks would otherwise call out to the live scraper/LLM.
    async def _noop(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(scraping_requests_endpoint, "process_scraping_request", _noop)
    monkeypatch.setattr(scraping_requests_endpoint, "process_llm_job", _noop)

    app = create_app()

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _make_authed_api_user(session: AsyncSession, label: str) -> AuthedApiUser:
    api_user, key = await api_user_ops.create_with_api_key(session, name=f"{label}-{uuid.uuid4().hex[:8]}")
    await session.flush()
    return AuthedApiUser(api_user=api_user, api_key=key, headers={"Authorization": f"Bearer {key}"})


@pytest_asyncio.fixture
async def authed_api_user(db_session: AsyncSession) -> AuthedApiUser:
    return await _make_authed_api_user(db_session, "test-api-user")


@pytest_asyncio.fixture
async def other_authed_api_user(db_session: AsyncSession) -> AuthedApiUser:
    return await _make_authed_api_user(db_session, "other-api-user")


def fresh_client_identifier(prefix: str = "client") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
