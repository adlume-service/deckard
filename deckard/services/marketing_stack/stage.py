"""Pipeline stage wrapper for marketing-stack detection.

Runs the static detector against the seed page, enriches with the GTM
container fetch when applicable, and writes the result onto
``request.request_metadata``. Detector failures are isolated under
``marketing_stack_error`` and never flip the request status.
"""

import logging

from deckard.database.models import ScrapingRequest, ScrapingResult
from deckard.services.marketing_stack.detector import (
    MARKETING_STACK_DETECTOR_VERSION,
    detect_marketing_stack,
)
from deckard.services.marketing_stack.gtm import enrich_with_gtm_container

logger = logging.getLogger(__name__)


async def run_marketing_stack_stage(
    request: ScrapingRequest,
    *,
    seed: ScrapingResult | None,
    headers: dict[str, str] | None,
) -> None:
    """Mutates ``request.request_metadata`` in place. The caller's session
    flushes the change."""
    if seed is None or not seed.raw_html:
        # JSONB rebind required: SQLAlchemy doesn't detect in-place dict mutation.
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
            html=seed.raw_html,
            response_headers=headers or {},
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
