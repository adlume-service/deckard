"""Pipeline stage wrapper for Google PageSpeed Insights.

Skipped silently when ``page_speed_insights_api`` is unset — no key is a
configuration choice, not a failure. For ``"both"`` mode, mobile + desktop
run concurrently; per-strategy failures are isolated under
``performance_error[strategy]`` and never flip the request status.
"""

import asyncio
import logging

from deckard.config import get_settings
from deckard.database.models import ScrapingRequest
from deckard.services.performance.client import (
    PERFORMANCE_DETECTOR_VERSION,
    detect_performance,
)

logger = logging.getLogger(__name__)


async def run_performance_stage(request: ScrapingRequest) -> None:
    """Mutates ``request.request_metadata`` in place. The caller's session
    flushes the change."""
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

    if not reports and not errors:
        return
    # JSONB rebind required: SQLAlchemy doesn't detect in-place dict mutation.
    new_metadata = {**request.request_metadata}
    if reports:
        new_metadata["performance"] = reports
    if errors:
        new_metadata["performance_error"] = errors
    request.request_metadata = new_metadata
