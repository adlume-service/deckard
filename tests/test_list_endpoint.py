"""Tests for `GET /scraping-requests` (paginated, ApiUser-scoped)."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from deckard.database.operations import scraping_request as scraping_request_ops
from tests.conftest import AuthedApiUser, fresh_client_identifier


async def _seed_requests(session: AsyncSession, *, api_user, count: int) -> None:
    for _ in range(count):
        await scraping_request_ops.create(
            session,
            api_user=api_user,
            client_identifier=fresh_client_identifier(),
            url="https://example.com",
        )
    await session.flush()


class TestListEndpoint:
    async def test_requires_auth(self, http_client: AsyncClient) -> None:
        response = await http_client.get("/scraping-requests")
        assert response.status_code == 401

    async def test_empty_response_shape(self, http_client: AsyncClient, authed_api_user: AuthedApiUser) -> None:
        response = await http_client.get("/scraping-requests", headers=authed_api_user.headers)
        assert response.status_code == 200
        body = response.json()
        assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}

    async def test_returns_only_callers_requests(
        self,
        http_client: AsyncClient,
        db_session: AsyncSession,
        authed_api_user: AuthedApiUser,
        other_authed_api_user: AuthedApiUser,
    ) -> None:
        await _seed_requests(db_session, api_user=authed_api_user.api_user, count=3)
        await _seed_requests(db_session, api_user=other_authed_api_user.api_user, count=2)

        response = await http_client.get("/scraping-requests", headers=authed_api_user.headers)
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert len(body["items"]) == 3

        other = await http_client.get("/scraping-requests", headers=other_authed_api_user.headers)
        assert other.json()["total"] == 2

    async def test_pagination(
        self,
        http_client: AsyncClient,
        db_session: AsyncSession,
        authed_api_user: AuthedApiUser,
    ) -> None:
        await _seed_requests(db_session, api_user=authed_api_user.api_user, count=5)

        page = await http_client.get("/scraping-requests?limit=2&offset=0", headers=authed_api_user.headers)
        body = page.json()
        assert body["total"] == 5
        assert body["limit"] == 2
        assert body["offset"] == 0
        assert len(body["items"]) == 2

        page2 = await http_client.get("/scraping-requests?limit=2&offset=2", headers=authed_api_user.headers)
        assert len(page2.json()["items"]) == 2
        assert page2.json()["items"][0]["id"] != body["items"][0]["id"]

    async def test_rejects_bad_pagination(self, http_client: AsyncClient, authed_api_user: AuthedApiUser) -> None:
        response = await http_client.get("/scraping-requests?limit=0", headers=authed_api_user.headers)
        assert response.status_code == 422
