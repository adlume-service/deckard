"""End-to-end scraping-request pipeline.

One async function — ``run_scraping_request_pipeline`` — owns the full
lifecycle: crawl → persist pages → marketing-stack detector → performance
detector → LLM extraction. Each stage is a plain function imported from its
own module; this file is intentionally linear so a reader can see the whole
workflow top to bottom.

Note on ``request_metadata``: SQLAlchemy JSONB doesn't detect in-place dict
mutation. Anywhere the detector stages update this attribute, they rebind
it via ``request.request_metadata = {**request.request_metadata, ...}`` —
the rebind is what triggers the change.
"""

import logging
import uuid

from deckard.database.models import ScrapingRequest
from deckard.database.session import get_sessionmaker
from deckard.services.extraction import run_extraction
from deckard.services.marketing_stack import run_marketing_stack_stage
from deckard.services.performance import run_performance_stage
from deckard.services.scraping import crawl
from deckard.services.status import transition_status

logger = logging.getLogger(__name__)


async def run_scraping_request_pipeline(request_id: uuid.UUID) -> None:
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

        transition_status(request, "scraping")
        await session.commit()

        try:
            results, seed_headers = await crawl(seed_url=request.requested_url)
        except Exception as exc:
            logger.exception("Scrape failed for request %s", request_id)
            transition_status(request, "failed", exc=exc)
            await session.commit()
            return

        for result in results:
            result.scraping_request_id = request.id
            session.add(result)
        transition_status(request, "scraped")

        # Detector stages: isolated failures, never flip status.
        seed_result = results[0] if results else None
        await run_marketing_stack_stage(request, seed=seed_result, headers=seed_headers)
        await run_performance_stage(request)
        await session.commit()
        logger.info("Scrape completed for %s — %d pages captured", request_id, len(results))

    # LLM extraction opens its own session: separate workflow on LLMProcessingJob,
    # and the crawl/detect transaction is already committed.
    await run_extraction(request_id)
