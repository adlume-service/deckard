"""Scraping orchestration.

Picks up a `ScrapingRequest`, runs crawl4ai's `AsyncWebCrawler` with a
breadth-first deep-crawl strategy against the seed URL, persists one
`ScrapingResult` per captured page, and advances the request's status to
`scraped` or `failed`.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CrawlResult
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from sqlalchemy.ext.asyncio import AsyncSession

from deckard.config import get_settings
from deckard.constants import TOKEN_ENCODING
from deckard.database.models import ScrapingRequest, ScrapingResult
from deckard.database.session import get_sessionmaker
from deckard.services.marketing_stack import (
    MARKETING_STACK_DETECTOR_VERSION,
    detect_marketing_stack,
    enrich_with_gtm_container,
)
from deckard.services.performance import (
    PERFORMANCE_DETECTOR_VERSION,
    detect_performance,
)

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
            results, seed_headers = await _crawl(seed_url=request.requested_url)
        except Exception as exc:
            logger.exception("Scrape failed for request %s", request_id)
            await _mark_failed(session, request, exc)
            await session.commit()
            return

        for result in results:
            result.scraping_request_id = request.id
            session.add(result)

        await _mark_scraped(session, request)
        await _detect_and_persist_marketing_stack(
            session=session,
            request=request,
            seed_result=results[0] if results else None,
            seed_headers=seed_headers,
        )
        await _detect_and_persist_performance(session=session, request=request)
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


async def _crawl(*, seed_url: str) -> tuple[list[ScrapingResult], dict[str, str] | None]:
    """Run a depth-limited BFS crawl from `seed_url`.

    Returns the unsaved ScrapingResult rows and the seed page's response headers
    (held in memory, not persisted — used by the marketing-stack detector).
    """
    settings = get_settings()

    run_config = CrawlerRunConfig(
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=settings.scrape_max_depth,
            max_pages=settings.scrape_max_pages,
            include_external=True,
        ),
        page_timeout=settings.scrape_request_timeout_seconds * 1000,
        stream=False,
    )
    browser_config = BrowserConfig(headless=True)

    async with AsyncWebCrawler(config=browser_config) as crawler:
        crawl_results: list[CrawlResult] = await crawler.arun(url=seed_url, config=run_config)

    seed_headers: dict[str, str] | None = None
    if crawl_results:
        raw_headers = getattr(crawl_results[0], "response_headers", None)
        if isinstance(raw_headers, dict):
            seed_headers = {str(k): str(v) for k, v in raw_headers.items()}

    return [_to_scraping_result(result) for result in crawl_results], seed_headers


def _to_scraping_result(result: CrawlResult) -> ScrapingResult:
    markdown = str(result.markdown) if result.markdown is not None else None
    markdown = markdown or None
    return ScrapingResult(
        url=result.url,
        final_url=result.redirected_url or result.url,
        markdown=markdown,
        cleaned_html=result.cleaned_html,
        raw_html=result.html or None,
        success=result.success,
        status_code=result.status_code,
        tokens=len(TOKEN_ENCODING.encode(markdown)) if markdown else None,
    )


async def _detect_and_persist_marketing_stack(
    *,
    session: AsyncSession,
    request: ScrapingRequest,
    seed_result: ScrapingResult | None,
    seed_headers: dict[str, str] | None,
) -> None:
    """Run static marketing-stack detection against the seed page and persist
    the result on ``request.request_metadata``.

    Isolated try/except: detection failure is recorded under
    ``marketing_stack_error`` but never flips the request to ``failed``.
    """
    if seed_result is None or not seed_result.raw_html:
        request.request_metadata = {
            **request.request_metadata,
            "marketing_stack_error": {
                "error_code": "no_seed_html",
                "error_message": "No seed page HTML available for marketing-stack detection.",
                "detector_version": MARKETING_STACK_DETECTOR_VERSION,
            },
        }
        return

    try:
        stack = detect_marketing_stack(
            html=seed_result.raw_html,
            response_headers=seed_headers or {},
            seed_url=request.requested_url,
        )
        stack = await enrich_with_gtm_container(stack)
    except Exception as exc:
        logger.exception("Marketing-stack detection failed for request %s", request.id)
        request.request_metadata = {
            **request.request_metadata,
            "marketing_stack_error": {
                "error_code": type(exc).__name__,
                "error_message": str(exc)[:1000],
                "detector_version": MARKETING_STACK_DETECTOR_VERSION,
            },
        }
        return

    request.request_metadata = {**request.request_metadata, "marketing_stack": stack}


async def _detect_and_persist_performance(*, session: AsyncSession, request: ScrapingRequest) -> None:
    """Run Google PageSpeed Insights against the seed URL and persist per-strategy reports.

    Skipped silently when ``page_speed_insights_api`` is unset — not having a
    key is a configuration choice, not a failure. When configured for
    ``"both"``, mobile + desktop run concurrently; per-strategy failures are
    isolated under ``performance_error[strategy]`` and never flip the request
    to ``failed``.
    """
    settings = get_settings()
    api_key = settings.page_speed_insights_api
    if not api_key:
        return

    strategies = (
        ["mobile", "desktop"]
        if settings.page_speed_insights_strategy == "both"
        else [settings.page_speed_insights_strategy]
    )
    results = await asyncio.gather(
        *(detect_performance(request.requested_url, strategy=s) for s in strategies),
        return_exceptions=True,
    )

    reports: dict[str, dict] = {}
    errors: dict[str, dict] = {}
    for strategy, result in zip(strategies, results, strict=True):
        if isinstance(result, Exception):
            logger.exception(
                "PageSpeed Insights detection failed for request %s strategy=%s",
                request.id,
                strategy,
                exc_info=result,
            )
            # Belt-and-suspenders: scrub key from any persisted error message before
            # truncating — otherwise a key straddling the 1000-char boundary would
            # leak a partial prefix.
            errors[strategy] = {
                "error_code": type(result).__name__,
                "error_message": str(result).replace(api_key, "<redacted>")[:1000],
                "detector_version": PERFORMANCE_DETECTOR_VERSION,
            }
        else:
            reports[strategy] = result

    # Single rebind to preserve SQLAlchemy JSONB change detection.
    new_metadata = {**request.request_metadata}
    if reports:
        new_metadata["performance"] = reports
    if errors:
        new_metadata["performance_error"] = errors
    request.request_metadata = new_metadata
