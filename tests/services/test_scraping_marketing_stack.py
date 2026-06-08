"""Orchestration-level tests for marketing-stack detection.

Exercises ``run_scraping_request_pipeline`` with ``crawl`` patched to return
canned HTML + headers, asserting that the request reaches ``scraped`` status
and the marketing-stack report lands in ``request_metadata``. Extraction is
no-op'd by the ``patched_sessionmaker`` fixture.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deckard.database.models import ScrapingRequest, ScrapingResult
from deckard.database.operations import client as client_ops
from deckard.database.operations import website as website_ops
from deckard.services import scraping_request as pipeline_module
from deckard.services.marketing_stack import stage as marketing_stack_stage_module
from deckard.services.scraping_request import run_scraping_request_pipeline
from tests.conftest import AuthedApiUser, fresh_client_identifier

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "marketing_stack"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest_asyncio.fixture
async def patched_sessionmaker(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Make the pipeline reuse the per-test connection so its commits land on the
    SAVEPOINT controlled by the outer fixture. Extraction is no-op'd — these tests
    cover scraping + marketing-stack detection only.
    """
    connection = await db_session.connection()
    sessionmaker = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(pipeline_module, "get_sessionmaker", lambda: sessionmaker)

    async def _noop_extraction(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(pipeline_module, "run_extraction", _noop_extraction)
    yield


async def _make_pending_request(
    session: AsyncSession, api_user_id: uuid.UUID, url: str = "https://www.brand.com/"
) -> ScrapingRequest:
    client = await client_ops.get_or_create_by_identifier(session, fresh_client_identifier())
    website = await website_ops.get_or_create_for_client(session, client.id, url)
    request = ScrapingRequest(
        client_id=client.id,
        api_user_id=api_user_id,
        website_id=website.id,
        requested_url=url,
        request_metadata={},
    )
    session.add(request)
    await session.flush()
    await session.commit()
    return request


async def _patch_crawl(monkeypatch: pytest.MonkeyPatch, *, html: str, headers: dict[str, str] | None = None) -> None:
    async def _fake_crawl(*, seed_url: str) -> tuple[list[ScrapingResult], dict[str, str] | None]:
        return (
            [
                ScrapingResult(
                    url=seed_url,
                    final_url=seed_url,
                    markdown=None,
                    cleaned_html=None,
                    raw_html=html,
                    success=True,
                    status_code=200,
                    tokens=None,
                )
            ],
            headers,
        )

    monkeypatch.setattr(pipeline_module, "crawl", _fake_crawl)


async def _patch_gtm_fetch(monkeypatch: pytest.MonkeyPatch, body: str | None) -> None:
    async def _fake(container_id: str, *, timeout_s: float | None = None) -> str | None:
        return body

    monkeypatch.setattr("deckard.services.marketing_stack.gtm.fetch_gtm_container", _fake)


async def test_detection_persisted_under_marketing_stack(
    db_session: AsyncSession,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
) -> None:
    await _patch_crawl(monkeypatch, html=_load("gtm.html"))
    await _patch_gtm_fetch(monkeypatch, _load("gtm_container_body.js"))

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)

    await run_scraping_request_pipeline(request.id)

    refreshed = (await db_session.execute(select(ScrapingRequest).where(ScrapingRequest.id == request.id))).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == "scraped"
    stack = refreshed.request_metadata["marketing_stack"]
    assert stack["detector_version"] == "1"
    assert "gtm" in stack["vendors"]
    assert stack["vendors"]["gtm"]["extras"]["container_fetch_status"] == "ok"
    assert "marketing_stack_error" not in refreshed.request_metadata


async def test_no_seed_html_writes_marketing_stack_error(
    db_session: AsyncSession,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
) -> None:
    async def _empty_crawl(*, seed_url: str) -> tuple[list[ScrapingResult], dict[str, str] | None]:
        return ([], None)

    monkeypatch.setattr(pipeline_module, "crawl", _empty_crawl)

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)
    await run_scraping_request_pipeline(request.id)

    refreshed = (await db_session.execute(select(ScrapingRequest).where(ScrapingRequest.id == request.id))).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == "scraped"
    assert "marketing_stack" not in refreshed.request_metadata
    err = refreshed.request_metadata["marketing_stack_error"]
    assert err["error_code"] == "no_seed_html"


async def test_detector_failure_isolated_from_request_status(
    db_session: AsyncSession,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
) -> None:
    await _patch_crawl(monkeypatch, html=_load("gtm.html"))

    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(marketing_stack_stage_module, "detect_marketing_stack", _boom)

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)
    await run_scraping_request_pipeline(request.id)

    refreshed = (await db_session.execute(select(ScrapingRequest).where(ScrapingRequest.id == request.id))).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == "scraped"
    err = refreshed.request_metadata["marketing_stack_error"]
    assert err["error_code"] == "RuntimeError"
    assert "detector exploded" in err["error_message"]


async def test_get_endpoint_exposes_marketing_stack(
    db_session: AsyncSession,
    http_client: AsyncClient,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
) -> None:
    await _patch_crawl(monkeypatch, html=_load("gtm.html"))
    await _patch_gtm_fetch(monkeypatch, _load("gtm_container_body.js"))

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)
    await run_scraping_request_pipeline(request.id)
    # run_scraping_request_pipeline wrote via a different session — refresh our
    # session's view of the row so the endpoint sees the post-detection state.
    await db_session.refresh(request)

    response = await http_client.get(
        f"/scraping-requests/{request.id}",
        headers=authed_api_user.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["marketing_stack_error"] is None
    stack = body["marketing_stack"]
    assert stack is not None
    assert stack["detector_version"] == "1"
    assert "gtm" in stack["vendors"]
    assert stack["vendors"]


async def test_get_endpoint_exposes_marketing_stack_error(
    db_session: AsyncSession,
    http_client: AsyncClient,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
) -> None:
    async def _empty_crawl(*, seed_url: str) -> tuple[list[ScrapingResult], dict[str, str] | None]:
        return ([], None)

    monkeypatch.setattr(pipeline_module, "crawl", _empty_crawl)

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)
    await run_scraping_request_pipeline(request.id)
    await db_session.refresh(request)

    response = await http_client.get(
        f"/scraping-requests/{request.id}",
        headers=authed_api_user.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["marketing_stack"] is None
    err = body["marketing_stack_error"]
    assert err is not None
    assert err["error_code"] == "no_seed_html"


async def test_existing_metadata_preserved_alongside_marketing_stack(
    db_session: AsyncSession,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
) -> None:
    await _patch_crawl(monkeypatch, html=_load("gtm.html"))
    await _patch_gtm_fetch(monkeypatch, _load("gtm_container_body.js"))

    client = await client_ops.get_or_create_by_identifier(db_session, fresh_client_identifier())
    website = await website_ops.get_or_create_for_client(db_session, client.id, "https://www.brand.com/")
    request = ScrapingRequest(
        client_id=client.id,
        api_user_id=authed_api_user.api_user.id,
        website_id=website.id,
        requested_url="https://www.brand.com/",
        request_metadata={"caller_tag": "preserve-me"},
    )
    db_session.add(request)
    await db_session.flush()
    await db_session.commit()

    await run_scraping_request_pipeline(request.id)

    refreshed = (await db_session.execute(select(ScrapingRequest).where(ScrapingRequest.id == request.id))).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.request_metadata["caller_tag"] == "preserve-me"
    assert "marketing_stack" in refreshed.request_metadata
