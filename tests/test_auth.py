import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from deckard.auth import API_KEY_PREFIX, generate_api_key, hash_api_key
from deckard.database.models import ScrapingRequest
from tests.conftest import AuthedApiUser, fresh_client_identifier


class TestApiKeyHelpers:
    def test_generated_key_uses_prefix(self) -> None:
        key = generate_api_key()
        assert key.startswith(API_KEY_PREFIX)
        assert len(key) > len(API_KEY_PREFIX) + 16

    def test_hash_is_deterministic_64_hex_chars(self) -> None:
        key = "deckard_live_example"
        h = hash_api_key(key)
        assert h == hash_api_key(key)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestAuthBoundary:
    async def test_missing_header_returns_401(self, http_client: AsyncClient) -> None:
        response = await http_client.post(
            "/scraping-requests",
            json={"client_identifier": fresh_client_identifier(), "url": "https://example.com"},
        )
        assert response.status_code == 401
        assert response.headers.get("www-authenticate", "").lower().startswith("bearer")

    async def test_non_bearer_scheme_returns_401(self, http_client: AsyncClient) -> None:
        response = await http_client.post(
            "/scraping-requests",
            json={"client_identifier": fresh_client_identifier(), "url": "https://example.com"},
            headers={"Authorization": "Basic Zm9vOmJhcg=="},
        )
        assert response.status_code == 401

    async def test_wrong_prefix_returns_401(self, http_client: AsyncClient) -> None:
        response = await http_client.post(
            "/scraping-requests",
            json={"client_identifier": fresh_client_identifier(), "url": "https://example.com"},
            headers={"Authorization": "Bearer not_a_deckard_key"},
        )
        assert response.status_code == 401

    async def test_unknown_key_returns_401(self, http_client: AsyncClient) -> None:
        response = await http_client.post(
            "/scraping-requests",
            json={"client_identifier": fresh_client_identifier(), "url": "https://example.com"},
            headers={"Authorization": f"Bearer {generate_api_key()}"},
        )
        assert response.status_code == 401

    async def test_status_endpoint_is_still_public(self, http_client: AsyncClient) -> None:
        response = await http_client.get("/status")
        assert response.status_code == 200


class TestCreateRequest:
    async def test_valid_key_creates_request_and_returns_client_id(
        self,
        http_client: AsyncClient,
        authed_api_user: AuthedApiUser,
    ) -> None:
        ident = fresh_client_identifier()
        response = await http_client.post(
            "/scraping-requests",
            json={"client_identifier": ident, "url": "https://example.com"},
            headers=authed_api_user.headers,
        )
        assert response.status_code == 201
        body = response.json()
        # client_id is freshly minted (auto-created on first use) and present in the response.
        assert uuid.UUID(body["client_id"])  # parses

    async def test_missing_client_identifier_returns_422(
        self,
        http_client: AsyncClient,
        authed_api_user: AuthedApiUser,
    ) -> None:
        response = await http_client.post(
            "/scraping-requests",
            json={"url": "https://example.com"},
            headers=authed_api_user.headers,
        )
        assert response.status_code == 422

    async def test_request_persisted_with_api_user_and_client(
        self,
        http_client: AsyncClient,
        db_session: AsyncSession,
        authed_api_user: AuthedApiUser,
    ) -> None:
        ident = fresh_client_identifier()
        response = await http_client.post(
            "/scraping-requests",
            json={"client_identifier": ident, "url": "https://example.com"},
            headers=authed_api_user.headers,
        )
        assert response.status_code == 201
        request_id = uuid.UUID(response.json()["id"])

        stored = await db_session.get(ScrapingRequest, request_id)
        assert stored is not None
        assert stored.api_user_id == authed_api_user.api_user.id
        assert stored.client_id == uuid.UUID(response.json()["client_id"])

    async def test_two_api_users_can_submit_for_same_client(
        self,
        http_client: AsyncClient,
        authed_api_user: AuthedApiUser,
        other_authed_api_user: AuthedApiUser,
    ) -> None:
        """Clients are global — distinct ApiUsers using the same identifier hit the same Client row."""
        ident = fresh_client_identifier("shared")

        r1 = await http_client.post(
            "/scraping-requests",
            json={"client_identifier": ident, "url": "https://example.com"},
            headers=authed_api_user.headers,
        )
        r2 = await http_client.post(
            "/scraping-requests",
            json={"client_identifier": ident, "url": "https://example.com"},
            headers=other_authed_api_user.headers,
        )
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["client_id"] == r2.json()["client_id"]
        assert r1.json()["id"] != r2.json()["id"]

    async def test_idempotency_scoped_per_api_user(
        self,
        http_client: AsyncClient,
        authed_api_user: AuthedApiUser,
        other_authed_api_user: AuthedApiUser,
    ) -> None:
        ident = fresh_client_identifier()
        body = {
            "client_identifier": ident,
            "url": "https://example.com",
            "idempotency_key": "shared-key-abc",
        }

        first = await http_client.post("/scraping-requests", json=body, headers=authed_api_user.headers)
        second_same_user = await http_client.post("/scraping-requests", json=body, headers=authed_api_user.headers)
        second_other_user = await http_client.post(
            "/scraping-requests", json=body, headers=other_authed_api_user.headers
        )

        assert first.status_code == 201
        # Same ApiUser, same key → same row.
        assert second_same_user.json()["id"] == first.json()["id"]
        # Different ApiUser, same key → distinct row.
        assert second_other_user.json()["id"] != first.json()["id"]


class TestOwnership:
    async def test_get_returns_404_for_other_api_users_request(
        self,
        http_client: AsyncClient,
        db_session: AsyncSession,
        authed_api_user: AuthedApiUser,
        other_authed_api_user: AuthedApiUser,
    ) -> None:
        from deckard.database.operations import scraping_request as scraping_request_ops

        request = await scraping_request_ops.create(
            db_session,
            api_user=other_authed_api_user.api_user,
            client_identifier=fresh_client_identifier(),
            url="https://example.com",
        )
        await db_session.flush()

        response = await http_client.get(
            f"/scraping-requests/{request.id}",
            headers=authed_api_user.headers,
        )
        assert response.status_code == 404

    async def test_get_returns_404_for_unknown_uuid(
        self,
        http_client: AsyncClient,
        authed_api_user: AuthedApiUser,
    ) -> None:
        response = await http_client.get(
            f"/scraping-requests/{uuid.uuid4()}",
            headers=authed_api_user.headers,
        )
        assert response.status_code == 404

    async def test_owner_can_read_own_request(
        self,
        http_client: AsyncClient,
        db_session: AsyncSession,
        authed_api_user: AuthedApiUser,
    ) -> None:
        from deckard.database.operations import scraping_request as scraping_request_ops

        request = await scraping_request_ops.create(
            db_session,
            api_user=authed_api_user.api_user,
            client_identifier=fresh_client_identifier(),
            url="https://example.com",
        )
        await db_session.flush()

        response = await http_client.get(
            f"/scraping-requests/{request.id}",
            headers=authed_api_user.headers,
        )
        assert response.status_code == 200
        assert response.json()["id"] == str(request.id)

    async def test_other_api_user_sharing_client_still_cannot_read(
        self,
        http_client: AsyncClient,
        db_session: AsyncSession,
        authed_api_user: AuthedApiUser,
        other_authed_api_user: AuthedApiUser,
    ) -> None:
        """Sharing the same Client identifier does NOT grant cross-ApiUser visibility."""
        from deckard.database.operations import scraping_request as scraping_request_ops

        shared_ident = fresh_client_identifier("shared")
        request = await scraping_request_ops.create(
            db_session,
            api_user=authed_api_user.api_user,
            client_identifier=shared_ident,
            url="https://example.com",
        )
        await db_session.flush()

        # other_authed_api_user knows the shared client_identifier but did not submit this request.
        response = await http_client.get(
            f"/scraping-requests/{request.id}",
            headers=other_authed_api_user.headers,
        )
        assert response.status_code == 404
