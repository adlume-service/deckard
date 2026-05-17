"""Scraping orchestration.

Picks up a `ScrapingRequest`, runs crawl4ai's `AsyncWebCrawler` with a
breadth-first deep-crawl strategy against the seed URL, persists one
`ScrapingResult` per captured page, and advances the request's status to
`scraped` or `failed`.
"""

import logging
import uuid
from datetime import UTC, datetime

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CrawlResult
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from sqlalchemy.ext.asyncio import AsyncSession

from deckard.config import get_settings
from deckard.database.models import ScrapingRequest, ScrapingResult
from deckard.database.session import get_sessionmaker

logger = logging.getLogger(__name__)


async def process_scraping_request(request_id: uuid.UUID) -> None:
    """Run the scrape for a request. Opens its own session — safe to call from BackgroundTasks."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        request = await session.get(ScrapingRequest, request_id)
        if request is None:
            logger.warning("Scraping request %s not found; skipping", request_id)
            return

        if request.status != "pending":
            logger.info(
                "Scraping request %s is in status %r; skipping (only 'pending' is processed)",
                request_id,
                request.status,
            )
            return

        await _mark_scraping(session, request)

        try:
            results = await _crawl(seed_url=request.requested_url)
        except Exception as exc:
            logger.exception("Scrape failed for request %s", request_id)
            await _mark_failed(session, request, exc)
            await session.commit()
            return

        for result in results:
            result.scraping_request_id = request.id
            session.add(result)

        await _mark_scraped(session, request)
        await session.commit()
        logger.info("Scrape completed for %s — %d pages captured", request_id, len(results))


async def _mark_scraping(session: AsyncSession, request: ScrapingRequest) -> None:
    request.status = "scraping"
    request.started_at = datetime.now(UTC)
    request.attempt_count = (request.attempt_count or 0) + 1
    await session.commit()


async def _mark_scraped(session: AsyncSession, request: ScrapingRequest) -> None:
    request.status = "scraped"
    request.finished_at = datetime.now(UTC)


async def _mark_failed(session: AsyncSession, request: ScrapingRequest, exc: BaseException) -> None:
    request.status = "failed"
    request.finished_at = datetime.now(UTC)
    request.error_code = type(exc).__name__
    request.error_message = str(exc)[:1000]


async def _crawl(*, seed_url: str) -> list[ScrapingResult]:
    """Run a depth-limited BFS crawl from `seed_url`, returning unsaved ScrapingResult rows."""
    settings = get_settings()

    run_config = CrawlerRunConfig(
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=settings.scrape_max_depth,
            max_pages=settings.scrape_max_pages,
            include_external=False,
        ),
        page_timeout=settings.scrape_request_timeout_seconds * 1000,
        stream=False,
    )
    browser_config = BrowserConfig(headless=True)

    async with AsyncWebCrawler(config=browser_config) as crawler:
        crawl_results: list[CrawlResult] = await crawler.arun(url=seed_url, config=run_config)

    return [_to_scraping_result(result) for result in crawl_results]


def _to_scraping_result(result: CrawlResult) -> ScrapingResult:
    markdown = str(result.markdown) if result.markdown is not None else None
    return ScrapingResult(
        url=result.url,
        final_url=result.redirected_url or result.url,
        markdown=markdown or None,
        cleaned_html=result.cleaned_html,
        raw_html=result.html or None,
        success=result.success,
        status_code=result.status_code,
    )
