"""PageSpeed Insights v5 client + orchestrator entrypoint.

``fetch_pagespeed_insights`` performs the I/O; ``detect_performance`` is the
high-level call the scraper invokes — it composes fetch + parse and stamps
the detector version + timestamp.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from deckard.config import get_settings
from deckard.services.performance.parser import parse_pagespeed_response

logger = logging.getLogger(__name__)

PERFORMANCE_DETECTOR_VERSION = "1"

PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


async def fetch_pagespeed_insights(
    url: str,
    *,
    api_key: str,
    strategy: str = "mobile",
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Call PSI v5 and return the raw response body.

    The API key is sent via the ``X-goog-api-key`` header (not a query param)
    so it can't leak into ``httpx.HTTPStatusError`` strings, which include the
    request URL.

    Performs at most one retry on transport failures and on retryable status
    codes (429, 5xx). 4xx other than 429 raises immediately. Raises
    ``httpx.HTTPError`` / ``httpx.HTTPStatusError`` on final failure so the
    caller's isolated try/except can record the failure type.
    """
    params = {
        "url": url,
        "strategy": strategy,
        "category": "performance",
    }
    headers = {"X-goog-api-key": api_key}

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        last_exc: httpx.HTTPError | None = None
        last_response: httpx.Response | None = None
        for attempt in (1, 2):
            try:
                response = await client.get(PAGESPEED_URL, params=params, headers=headers)
            except httpx.HTTPError as exc:
                logger.info("PSI fetch attempt %d failed: %s", attempt, exc)
                last_exc = exc
                last_response = None
                continue

            if response.status_code in _RETRYABLE_STATUS:
                logger.info(
                    "PSI fetch attempt %d returned retryable status %d",
                    attempt,
                    response.status_code,
                )
                last_exc = None
                last_response = response
                continue

            # Non-retryable: either success or a 4xx we shouldn't retry.
            response.raise_for_status()
            return response.json()

        # Exhausted retries — raise the last failure.
        if last_response is not None:
            last_response.raise_for_status()
        assert last_exc is not None
        raise last_exc


async def detect_performance(url: str, *, strategy: str) -> dict[str, Any]:
    """Run a PageSpeed Insights audit against ``url`` and return the trimmed report.

    Returns the dict the scraper writes to ``request_metadata.performance[strategy]``.
    Callers must check ``settings.page_speed_insights_api`` and skip when
    unset — this function assumes the key is configured. The orchestrator owns
    the mobile-vs-desktop decision; this function audits a single strategy.
    """
    settings = get_settings()
    payload = await fetch_pagespeed_insights(
        url,
        api_key=settings.page_speed_insights_api or "",
        strategy=strategy,
        timeout_s=settings.page_speed_insights_timeout_seconds,
    )
    report = parse_pagespeed_response(payload, strategy=strategy)
    report["detector_version"] = PERFORMANCE_DETECTOR_VERSION
    report["fetched_at"] = datetime.now(UTC).isoformat()
    return report
