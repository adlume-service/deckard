"""Tests for the /ui cookie-based auth flow."""

from httpx import AsyncClient

from deckard.config import get_settings
from tests.conftest import AuthedApiUser


class TestLoginGate:
    async def test_index_redirects_to_login_when_unauthenticated(self, http_client: AsyncClient) -> None:
        response = await http_client.get("/ui/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/ui/login"

    async def test_detail_redirects_to_login_when_unauthenticated(self, http_client: AsyncClient) -> None:
        response = await http_client.get(
            "/ui/requests/00000000-0000-0000-0000-000000000000",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/ui/login"

    async def test_login_page_renders(self, http_client: AsyncClient) -> None:
        response = await http_client.get("/ui/login")
        assert response.status_code == 200
        assert "API key" in response.text


class TestLoginSubmit:
    async def test_invalid_key_redirects_back_with_error(self, http_client: AsyncClient) -> None:
        response = await http_client.post(
            "/ui/login",
            data={"api_key": "deckard_live_not_a_real_key"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/ui/login?error=invalid"
        assert get_settings().ui_cookie_name not in response.cookies

    async def test_valid_key_sets_cookie_and_redirects_to_index(
        self,
        http_client: AsyncClient,
        authed_api_user: AuthedApiUser,
    ) -> None:
        cookie_name = get_settings().ui_cookie_name
        response = await http_client.post(
            "/ui/login",
            data={"api_key": authed_api_user.api_key},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/ui/"
        assert response.cookies.get(cookie_name) == authed_api_user.api_key

    async def test_logout_clears_cookie(
        self,
        http_client: AsyncClient,
        authed_api_user: AuthedApiUser,
    ) -> None:
        cookie_name = get_settings().ui_cookie_name
        # Log in.
        await http_client.post(
            "/ui/login",
            data={"api_key": authed_api_user.api_key},
            follow_redirects=False,
        )
        # Then log out.
        response = await http_client.post("/ui/logout", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/ui/login"
        # The Set-Cookie should clear the cookie (empty value or expired).
        set_cookie = response.headers.get("set-cookie", "")
        assert cookie_name in set_cookie
