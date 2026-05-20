"""End-to-end tests for the /ui table + detail pages."""

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deckard.config import get_settings
from deckard.database.models import ScrapingRequest
from deckard.database.operations import scraping_request as scraping_request_ops
from tests.conftest import AuthedApiUser, fresh_client_identifier


def _logged_in_headers(authed: AuthedApiUser) -> dict[str, str]:
    return {"Cookie": f"{get_settings().ui_cookie_name}={authed.api_key}"}


class TestIndexPage:
    async def test_index_renders_when_empty(self, http_client: AsyncClient, authed_api_user: AuthedApiUser) -> None:
        response = await http_client.get("/ui/", headers=_logged_in_headers(authed_api_user))
        assert response.status_code == 200
        assert "No requests yet" in response.text
        # The submit form is always visible.
        assert 'action="/ui/requests"' in response.text

    async def test_index_lists_callers_requests_only(
        self,
        http_client: AsyncClient,
        db_session: AsyncSession,
        authed_api_user: AuthedApiUser,
        other_authed_api_user: AuthedApiUser,
    ) -> None:
        my = await scraping_request_ops.create(
            db_session,
            api_user=authed_api_user.api_user,
            client_identifier=fresh_client_identifier(),
            url="https://my-site.example.com",
        )
        other = await scraping_request_ops.create(
            db_session,
            api_user=other_authed_api_user.api_user,
            client_identifier=fresh_client_identifier(),
            url="https://not-mine.example.com",
        )
        await db_session.flush()

        response = await http_client.get("/ui/", headers=_logged_in_headers(authed_api_user))
        assert response.status_code == 200
        assert str(my.id) in response.text
        assert str(other.id) not in response.text
        assert "not-mine.example.com" not in response.text


class TestDetailPage:
    async def test_owner_sees_detail(
        self,
        http_client: AsyncClient,
        db_session: AsyncSession,
        authed_api_user: AuthedApiUser,
    ) -> None:
        req = await scraping_request_ops.create(
            db_session,
            api_user=authed_api_user.api_user,
            client_identifier=fresh_client_identifier(),
            url="https://example.com",
        )
        await db_session.flush()

        response = await http_client.get(f"/ui/requests/{req.id}", headers=_logged_in_headers(authed_api_user))
        assert response.status_code == 200
        assert str(req.id) in response.text
        # Non-terminal status → polling wrapper is wired up.
        assert f'hx-get="/ui/requests/{req.id}/body"' in response.text

    async def test_other_user_cannot_see(
        self,
        http_client: AsyncClient,
        db_session: AsyncSession,
        authed_api_user: AuthedApiUser,
        other_authed_api_user: AuthedApiUser,
    ) -> None:
        req = await scraping_request_ops.create(
            db_session,
            api_user=other_authed_api_user.api_user,
            client_identifier=fresh_client_identifier(),
            url="https://example.com",
        )
        await db_session.flush()

        response = await http_client.get(f"/ui/requests/{req.id}", headers=_logged_in_headers(authed_api_user))
        assert response.status_code == 404

    async def test_body_fragment_drops_polling_when_terminal(
        self,
        http_client: AsyncClient,
        db_session: AsyncSession,
        authed_api_user: AuthedApiUser,
    ) -> None:
        req = await scraping_request_ops.create(
            db_session,
            api_user=authed_api_user.api_user,
            client_identifier=fresh_client_identifier(),
            url="https://example.com",
        )
        req.status = "completed"
        await db_session.flush()

        response = await http_client.get(f"/ui/requests/{req.id}/body", headers=_logged_in_headers(authed_api_user))
        assert response.status_code == 200
        assert "hx-get" not in response.text


class TestSubmit:
    async def test_form_creates_request_and_redirects_to_detail(
        self,
        http_client: AsyncClient,
        db_session: AsyncSession,
        authed_api_user: AuthedApiUser,
    ) -> None:
        ident = fresh_client_identifier()
        response = await http_client.post(
            "/ui/requests",
            data={
                "url": "https://example.com",
                "client_identifier": ident,
                "idempotency_key": "",
            },
            headers=_logged_in_headers(authed_api_user),
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers["location"]
        assert location.startswith("/ui/requests/")
        request_id = uuid.UUID(location.rsplit("/", 1)[-1])

        stored = (
            await db_session.execute(select(ScrapingRequest).where(ScrapingRequest.id == request_id))
        ).scalar_one()
        assert stored.api_user_id == authed_api_user.api_user.id

    async def test_form_rejects_garbage_url(
        self,
        http_client: AsyncClient,
        authed_api_user: AuthedApiUser,
    ) -> None:
        response = await http_client.post(
            "/ui/requests",
            data={"url": "not-a-url", "client_identifier": "x"},
            headers=_logged_in_headers(authed_api_user),
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/ui/?submit_error=invalid"
