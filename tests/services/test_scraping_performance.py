"""Orchestration-level tests for PageSpeed Insights detection.

Exercises ``run_scraping_request_pipeline`` with both ``crawl`` and the PSI
``detect_performance`` call patched, asserting that the trimmed report (or
``performance_error``) lands on ``request_metadata`` without affecting
status. Extraction is no-op'd by the ``patched_sessionmaker`` fixture.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deckard.config import get_settings
from deckard.database.models import ScrapingRequest, ScrapingResult
from deckard.database.operations import client as client_ops
from deckard.database.operations import website as website_ops
from deckard.services import scraping_request as pipeline_module
from deckard.services.performance import stage as performance_stage_module
from deckard.services.performance.parser import parse_pagespeed_response
from deckard.services.scraping_request import run_scraping_request_pipeline
from tests.conftest import AuthedApiUser, fresh_client_identifier

PSI_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "performance" / "pagespeed_response.json"
MARKETING_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "marketing_stack"


def _load_psi_payload() -> dict[str, Any]:
    return json.loads(PSI_FIXTURE.read_text(encoding="utf-8"))


def _load_marketing(name: str) -> str:
    return (MARKETING_FIXTURES / name).read_text(encoding="utf-8")


@pytest_asyncio.fixture
async def patched_sessionmaker(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
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


@pytest_asyncio.fixture
async def psi_key_configured(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Force the PSI key on for tests regardless of local .env state."""
    settings = get_settings()
    monkeypatch.setattr(settings, "page_speed_insights_api", "test-key")
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


async def _patch_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_crawl(*, seed_url: str) -> tuple[list[ScrapingResult], dict[str, str] | None]:
        return (
            [
                ScrapingResult(
                    url=seed_url,
                    final_url=seed_url,
                    markdown=None,
                    cleaned_html=None,
                    raw_html=_load_marketing("gtm.html"),
                    success=True,
                    status_code=200,
                    tokens=None,
                )
            ],
            None,
        )

    monkeypatch.setattr(pipeline_module, "crawl", _fake_crawl)


async def _patch_gtm_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(container_id: str, *, timeout_s: float | None = None) -> str | None:
        return None

    monkeypatch.setattr("deckard.services.marketing_stack.gtm.fetch_gtm_container", _fake)


async def _patch_detect_performance(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(url: str, *, strategy: str) -> dict[str, Any]:
        report = parse_pagespeed_response(_load_psi_payload(), strategy=strategy)
        report["detector_version"] = "1"
        report["fetched_at"] = "2026-05-19T10:30:00+00:00"
        return report

    monkeypatch.setattr(performance_stage_module, "detect_performance", _fake)


async def test_performance_report_persisted(
    db_session: AsyncSession,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
    psi_key_configured: None,
) -> None:
    await _patch_crawl(monkeypatch)
    await _patch_gtm_fetch(monkeypatch)
    await _patch_detect_performance(monkeypatch)

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)
    await run_scraping_request_pipeline(request.id)

    refreshed = (await db_session.execute(select(ScrapingRequest).where(ScrapingRequest.id == request.id))).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == "scraped"
    perf = refreshed.request_metadata["performance"]
    assert perf["mobile"]["detector_version"] == "1"
    assert perf["mobile"]["strategy"] == "mobile"
    assert perf["mobile"]["score"] == 62
    assert perf["mobile"]["metrics"]["largest_contentful_paint_ms"] == 3140
    assert "performance_error" not in refreshed.request_metadata


async def test_both_strategies_succeed(
    db_session: AsyncSession,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
    psi_key_configured: None,
) -> None:
    await _patch_crawl(monkeypatch)
    await _patch_gtm_fetch(monkeypatch)
    await _patch_detect_performance(monkeypatch)

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)
    await run_scraping_request_pipeline(request.id)

    refreshed = (await db_session.execute(select(ScrapingRequest).where(ScrapingRequest.id == request.id))).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == "scraped"
    perf = refreshed.request_metadata["performance"]
    assert perf["mobile"]["strategy"] == "mobile"
    assert perf["desktop"]["strategy"] == "desktop"
    assert "performance_error" not in refreshed.request_metadata


async def test_partial_failure_mobile_ok_desktop_raises(
    db_session: AsyncSession,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
    psi_key_configured: None,
) -> None:
    await _patch_crawl(monkeypatch)
    await _patch_gtm_fetch(monkeypatch)

    async def _fake(url: str, *, strategy: str) -> dict[str, Any]:
        if strategy == "desktop":
            raise RuntimeError("desktop exploded")
        report = parse_pagespeed_response(_load_psi_payload(), strategy=strategy)
        report["detector_version"] = "1"
        report["fetched_at"] = "2026-05-19T10:30:00+00:00"
        return report

    monkeypatch.setattr(performance_stage_module, "detect_performance", _fake)

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)
    await run_scraping_request_pipeline(request.id)

    refreshed = (await db_session.execute(select(ScrapingRequest).where(ScrapingRequest.id == request.id))).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == "scraped"
    perf = refreshed.request_metadata["performance"]
    assert "mobile" in perf
    assert "desktop" not in perf
    err = refreshed.request_metadata["performance_error"]
    assert "desktop" in err
    assert "mobile" not in err
    assert err["desktop"]["error_code"] == "RuntimeError"


async def test_both_strategies_fail(
    db_session: AsyncSession,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
    psi_key_configured: None,
) -> None:
    await _patch_crawl(monkeypatch)
    await _patch_gtm_fetch(monkeypatch)

    async def _boom(url: str, *, strategy: str) -> dict[str, Any]:
        raise RuntimeError(f"{strategy} exploded")

    monkeypatch.setattr(performance_stage_module, "detect_performance", _boom)

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)
    await run_scraping_request_pipeline(request.id)

    refreshed = (await db_session.execute(select(ScrapingRequest).where(ScrapingRequest.id == request.id))).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == "scraped"
    assert "performance" not in refreshed.request_metadata
    err = refreshed.request_metadata["performance_error"]
    assert err["mobile"]["error_code"] == "RuntimeError"
    assert err["desktop"]["error_code"] == "RuntimeError"
    assert "mobile exploded" in err["mobile"]["error_message"]
    assert "desktop exploded" in err["desktop"]["error_message"]


async def test_mobile_only_branching(
    db_session: AsyncSession,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
    psi_key_configured: None,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "page_speed_insights_strategy", "mobile")
    await _patch_crawl(monkeypatch)
    await _patch_gtm_fetch(monkeypatch)

    called_with: list[str] = []

    async def _fake(url: str, *, strategy: str) -> dict[str, Any]:
        called_with.append(strategy)
        report = parse_pagespeed_response(_load_psi_payload(), strategy=strategy)
        report["detector_version"] = "1"
        report["fetched_at"] = "2026-05-19T10:30:00+00:00"
        return report

    monkeypatch.setattr(performance_stage_module, "detect_performance", _fake)

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)
    await run_scraping_request_pipeline(request.id)

    refreshed = (await db_session.execute(select(ScrapingRequest).where(ScrapingRequest.id == request.id))).scalar_one()
    await db_session.refresh(refreshed)
    perf = refreshed.request_metadata["performance"]
    assert list(perf.keys()) == ["mobile"]
    assert "performance_error" not in refreshed.request_metadata
    assert called_with == ["mobile"]


async def test_psi_failure_isolated_from_request_status(
    db_session: AsyncSession,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
    psi_key_configured: None,
) -> None:
    await _patch_crawl(monkeypatch)
    await _patch_gtm_fetch(monkeypatch)

    async def _boom(url: str, *, strategy: str) -> dict[str, Any]:
        raise RuntimeError("psi exploded")

    monkeypatch.setattr(performance_stage_module, "detect_performance", _boom)

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)
    await run_scraping_request_pipeline(request.id)

    refreshed = (await db_session.execute(select(ScrapingRequest).where(ScrapingRequest.id == request.id))).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == "scraped"
    assert "performance" not in refreshed.request_metadata
    err = refreshed.request_metadata["performance_error"]
    assert err["mobile"]["error_code"] == "RuntimeError"
    assert "psi exploded" in err["mobile"]["error_message"]
    assert err["desktop"]["error_code"] == "RuntimeError"


async def test_psi_skipped_silently_when_key_unset(
    db_session: AsyncSession,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "page_speed_insights_api", None)
    await _patch_crawl(monkeypatch)
    await _patch_gtm_fetch(monkeypatch)

    called = False

    async def _should_not_be_called(url: str, *, strategy: str) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(performance_stage_module, "detect_performance", _should_not_be_called)

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)
    await run_scraping_request_pipeline(request.id)

    refreshed = (await db_session.execute(select(ScrapingRequest).where(ScrapingRequest.id == request.id))).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == "scraped"
    assert "performance" not in refreshed.request_metadata
    assert "performance_error" not in refreshed.request_metadata
    assert called is False


async def test_psi_error_message_redacts_api_key(
    db_session: AsyncSession,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
    psi_key_configured: None,
) -> None:
    """When PSI raises an HTTPStatusError whose string includes the URL (and
    therefore could include the key if it were ever passed as a query param),
    the persisted ``error_message`` must not contain the key.
    """
    await _patch_crawl(monkeypatch)
    await _patch_gtm_fetch(monkeypatch)

    api_key = get_settings().page_speed_insights_api
    assert api_key is not None

    async def _boom(url: str, *, strategy: str) -> dict[str, Any]:
        # Fabricate a leaky URL that contains the API key, as old PSI requests did.
        leaky_url = (
            f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
            f"?url={url}&key={api_key}&strategy={strategy}&category=performance"
        )
        request = httpx.Request("GET", leaky_url)
        response = httpx.Response(400, request=request, text="bad request")
        raise httpx.HTTPStatusError(
            f"Client error '400 Bad Request' for url '{leaky_url}'",
            request=request,
            response=response,
        )

    monkeypatch.setattr(performance_stage_module, "detect_performance", _boom)

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)
    await run_scraping_request_pipeline(request.id)

    refreshed = (await db_session.execute(select(ScrapingRequest).where(ScrapingRequest.id == request.id))).scalar_one()
    await db_session.refresh(refreshed)
    err = refreshed.request_metadata["performance_error"]["mobile"]
    assert err["error_code"] == "HTTPStatusError"
    assert api_key not in err["error_message"]
    assert "<redacted>" in err["error_message"]


async def test_get_endpoint_exposes_performance(
    db_session: AsyncSession,
    http_client: AsyncClient,
    authed_api_user: AuthedApiUser,
    monkeypatch: pytest.MonkeyPatch,
    patched_sessionmaker: None,
    psi_key_configured: None,
) -> None:
    await _patch_crawl(monkeypatch)
    await _patch_gtm_fetch(monkeypatch)
    await _patch_detect_performance(monkeypatch)

    request = await _make_pending_request(db_session, authed_api_user.api_user.id)
    await run_scraping_request_pipeline(request.id)
    await db_session.refresh(request)

    response = await http_client.get(
        f"/scraping-requests/{request.id}",
        headers=authed_api_user.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["performance_error"] is None
    perf = body["performance"]
    assert perf is not None
    assert perf["mobile"]["score"] == 62
    assert perf["mobile"]["strategy"] == "mobile"
    assert perf["desktop"]["strategy"] == "desktop"
