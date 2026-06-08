"""URL ranker: small-model call to order pages by likely info density.

Only fires when the page corpus exceeds the OpenAI context budget. The
hallucination-rejection / dedup / fallback logic lives in a pure function
so it can be unit-tested without the API.
"""

import logging
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from deckard.config import get_settings
from deckard.services.clients import get_openai_client

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You rank URLs from a single website by how likely each page contains the marketing/business "
    "information we want to extract: target audience, tone of voice, pricing and offer, "
    "geographic target market, unique selling proposition, and event dates. "
    "Return every input URL exactly once, ordered from most promising to least."
)


class _RankedURLs(BaseModel):
    ordered: list[str] = Field(
        description=(
            "All input URLs returned in priority order, highest likely info density first. "
            "Every input URL must appear exactly once."
        )
    )


def _deterministic_url_order(urls: list[str]) -> list[str]:
    """Homepage (root path) first, then by URL path depth ascending, then alphabetical."""

    def sort_key(u: str) -> tuple[int, int, str]:
        path = urlparse(u).path.rstrip("/")
        is_root = 0 if path in ("", "/") else 1
        depth = path.count("/")
        return is_root, depth, u

    return sorted(urls, key=sort_key)


def reconcile_ranker_output(input_urls: list[str], parsed_ordered: list[str]) -> list[str]:
    """Dedup the model's ordering, reject hallucinations, append any missing URLs.

    Raises ``ValueError`` if the model returned URLs not in the input set —
    callers fall back to the deterministic order.
    """
    input_set = set(input_urls)
    returned_set = set(parsed_ordered)
    extra = returned_set - input_set
    if extra:
        logger.debug(
            "Ranker returned URLs not in input (rejecting). extra=%d: %s",
            len(extra),
            sorted(extra),
        )
        raise ValueError("ranker returned URLs not in the input set")

    seen: set[str] = set()
    deduped: list[str] = []
    for u in parsed_ordered:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    missing = input_set - seen
    if missing:
        logger.info(
            "Ranker omitted %d URL(s); appending at end in deterministic order.",
            len(missing),
        )
        logger.debug("Missing URLs: %s", sorted(missing))
        deduped.extend(_deterministic_url_order(list(missing)))
    return deduped


async def rank_urls_with_llm(urls: list[str]) -> tuple[list[str], bool, dict | None]:
    """Return ``(ranked_urls, ranker_succeeded, usage_meta)``.

    ``usage_meta`` is non-None whenever the API call returned a response (even
    when we later reject the parsed content) — OpenAI bills for the call
    regardless, so we record it for cost attribution. It's None only when the
    call itself failed before returning (network error, 4xx/5xx).
    """
    settings = get_settings()
    client = get_openai_client()
    usage_meta: dict | None = None
    try:
        response = await client.responses.parse(
            model=settings.openai_url_ranker_model,
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(urls)},
            ],
            text_format=_RankedURLs,
        )
        usage = response.usage
        usage_meta = {
            "response_id": response.id,
            "model": response.model,
            "input_tokens": usage.input_tokens if usage else None,
            "output_tokens": usage.output_tokens if usage else None,
        }
        parsed = response.output_parsed
        if parsed is None:
            raise ValueError("ranker returned no parsed output")
        logger.debug("Ranker returned %d URLs: %s", len(parsed.ordered), parsed.ordered)
        return reconcile_ranker_output(urls, parsed.ordered), True, usage_meta
    except Exception:  # noqa: BLE001 — any ranker failure must degrade gracefully to fallback
        logger.exception("URL ranker call failed; falling back to deterministic order")
        return _deterministic_url_order(urls), False, usage_meta
