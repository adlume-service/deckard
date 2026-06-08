"""External-API client factories shared across services.

Cached so callers (extractor, URL ranker, future detectors) reuse the same
underlying HTTP connection pool rather than constructing a new client per
call.
"""

from functools import lru_cache

from openai import AsyncOpenAI

from deckard.config import get_settings


@lru_cache
def get_openai_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )
