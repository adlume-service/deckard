"""crawl4ai stage: turn a seed URL into a list of unsaved ``ScrapingResult`` rows.

Pure stage — no DB, no orchestration. The pipeline calls ``crawl`` and handles
persistence / status / chained detectors.
"""

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CrawlResult
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

from deckard.config import get_settings
from deckard.constants import TOKEN_ENCODING
from deckard.database.models import ScrapingResult


async def crawl(*, seed_url: str) -> tuple[list[ScrapingResult], dict[str, str] | None]:
    """Run a depth-limited BFS crawl from ``seed_url``.

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
