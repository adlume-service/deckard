"""LLM extraction orchestration.

Runs one OpenAI extraction job per ScrapingRequest. Concatenates the
captured pages' markdown (fit into a configurable token budget — when
shrinkage is needed, a small LLM ranks pages by likely info density and
the boundary page is truncated), asks the configured OpenAI model for a
structured JSON object containing the mar-tech fields we care about, and
stores the parsed result on `LLMOutput`.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from deckard.config import Settings, get_settings
from deckard.constants import TOKEN_ENCODING
from deckard.database.models import (
    LLMCall,
    LLMOutput,
    LLMProcessingJob,
    ScrapingRequest,
    ScrapingResult,
)
from deckard.database.operations import llm_processing_job as job_ops
from deckard.database.session import get_sessionmaker

logger = logging.getLogger(__name__)

JOB_TYPE = "default_extraction"
OUTPUT_SCHEMA_NAME = "martech_v1"
PROVIDER = "openai"


class MarTechExtraction(BaseModel):
    """Structured fields we ask the LLM to extract from a website's markdown.

    Every field is nullable on purpose — the prompt instructs the model to
    return null rather than confabulate when information is not present on
    the site.
    """

    target_audience: str | None = Field(
        description="Who the product/service is aimed at. Demographics, segments, use cases."
    )
    tone_of_voice: str | None = Field(
        description="How the brand communicates. Formal/casual, playful/serious, technical/accessible."
    )
    pricing_and_offer: str | None = Field(
        description="What they sell and at what price. Null if not stated on the site."
    )
    location: str | None = Field(
        description=(
            "Geographic market the product/service targets, i.e. where the intended audience is. "
            "City, country, region. Not the company's HQ unless the site explicitly conflates the two."
        )
    )
    usp: str | None = Field(
        description=(
            "Unique selling proposition the company explicitly claims. "
            "Null if absent; many companies don't articulate one."
        )
    )
    event_dates: list[str] | None = Field(
        description="Dates of events the company hosts or attends, if the site is event-related. Null otherwise."
    )


_SYSTEM_PROMPT = (
    "You extract marketing/business information from a company's website. "
    "The user message contains the cleaned markdown of one or more pages from a single site, "
    "concatenated with page-URL separators.\n\n"
    "Write your output values in the same language as the source content.\n\n"
    "Extract the requested fields. Critically: return null for any field you cannot confidently "
    "ground in the provided content. Do NOT invent plausible-sounding values."
)


async def process_llm_job(scraping_request_id: uuid.UUID) -> None:
    """Run the LLM extraction for a scraping request.

    Opens its own DB session. Intended to be called either directly from
    BackgroundTasks or chained inline from the scraping service once a
    scrape completes successfully.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        request = await _load_request_with_results(session, scraping_request_id)
        if request is None:
            logger.warning("Scraping request %s not found; skipping extraction", scraping_request_id)
            return

        if request.status != "scraped":
            logger.info(
                "Scraping request %s is in status %r; skipping extraction (only 'scraped' is processed)",
                scraping_request_id,
                request.status,
            )
            return

        settings = get_settings()
        job = await job_ops.create_for_request(
            session,
            scraping_request_id=request.id,
            job_type=JOB_TYPE,
            prompt_version=settings.extraction_prompt_version,
        )
        await _mark_request_processing(session, request)
        await _mark_job_processing(session, job)

        # Budget-fit before the API call so we can persist the report even on failure.
        pages, budget_report = await _fit_to_budget(
            request.scraping_results,
            budget=settings.openai_context_token_budget,
        )
        if not budget_report.fit:
            logger.warning(
                "Extraction input exceeded budget; shrunk %d→%d tokens (budget=%d). "
                "Ranker used: %s. Truncated %d page; dropped %d pages.",
                budget_report.total_tokens_before,
                budget_report.total_tokens_after,
                settings.openai_context_token_budget,
                budget_report.ranker_used,
                0 if budget_report.truncated_url is None else 1,
                len(budget_report.dropped_urls),
            )
            if budget_report.truncated_url is not None:
                logger.debug("Truncated URL: %s", budget_report.truncated_url)
            if budget_report.dropped_urls:
                logger.debug("Dropped URLs: %s", budget_report.dropped_urls)
        # Null when nothing was shrunk; True/False otherwise so we can track ranker success rate.
        job.ranker_used = None if budget_report.fit else budget_report.ranker_used
        if budget_report.ranker_usage is not None:
            _record_llm_call(session, job, "ranker", budget_report.ranker_usage)
        await session.commit()

        try:
            parsed, response_meta = await _call_openai(pages, settings)
        except Exception as exc:
            logger.exception("LLM extraction failed for request %s", scraping_request_id)
            await _mark_job_failed(session, job, exc)
            await _mark_request_failed(session, request, exc)
            await session.commit()
            return

        _record_llm_call(session, job, "extractor", response_meta)
        await _persist_output(session, job, parsed)
        await _mark_job_completed(session, job)
        await _mark_request_completed(session, request)
        await session.commit()
        logger.info("LLM extraction completed for request %s", scraping_request_id)


async def _load_request_with_results(session: AsyncSession, request_id: uuid.UUID) -> ScrapingRequest | None:
    stmt = (
        select(ScrapingRequest)
        .where(ScrapingRequest.id == request_id)
        .options(selectinload(ScrapingRequest.scraping_results))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _call_openai(
    pages: list[tuple[str, str]],
    settings: Settings,
) -> tuple[MarTechExtraction | None, dict]:
    """Call OpenAI with the budget-fitted page content.

    Returns the parsed extraction (or None if the model produced no parsed
    output) and a small metadata dict — just the bits we save on
    `LLMProcessingJob` (`id`, `model`, token counts). The full API envelope
    is intentionally discarded.
    """
    user_message = _build_user_message(pages)

    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )
    response = await client.responses.parse(
        model=settings.openai_model,
        input=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        text_format=MarTechExtraction,
        reasoning={"effort": settings.openai_reasoning_effort},
    )

    usage = response.usage
    meta = {
        "response_id": response.id,
        "model": response.model,
        "input_tokens": usage.input_tokens if usage else None,
        "output_tokens": usage.output_tokens if usage else None,
    }
    return response.output_parsed, meta


def _build_user_message(pages: list[tuple[str, str]]) -> str:
    if not pages:
        return "(no page content was captured)"
    return "\n\n".join(_page_block(url, md) for url, md in pages)


def _page_block(url: str, markdown: str) -> str:
    return _page_header(url) + markdown


# ---------------------------------------------------------------------------
# Token budget fitting
# ---------------------------------------------------------------------------


@dataclass
class _BudgetReport:
    fit: bool
    total_tokens_before: int
    total_tokens_after: int
    dropped_urls: list[str]
    truncated_url: str | None
    ranker_used: bool
    # Non-None when the ranker API call returned a response (even if we rejected the
    # parsed content). Used to persist the ranker's token usage on `llm_calls`.
    ranker_usage: dict | None = None


async def _fit_to_budget(
    results: list[ScrapingResult],
    budget: int,
) -> tuple[list[tuple[str, str]], _BudgetReport]:
    """Pack page markdown into the token budget.

    Happy path: total tokens already fit — return all pages in original
    order, ``fit=True``, no extra LLM call.

    Over budget: ask the small URL-ranker to order pages by likely info
    density, greedily pack from the top, truncate the boundary page to
    fit the remainder, drop the rest.
    """
    pages = [r for r in results if r.markdown]
    page_tokens = {r.url: _page_block_tokens(r.url, r.tokens or 0) for r in pages}
    pages_with_content: list[tuple[str, str]] = [(r.url, r.markdown) for r in pages]
    total_before = sum(page_tokens.values())

    if total_before <= budget:
        return pages_with_content, _BudgetReport(
            fit=True,
            total_tokens_before=total_before,
            total_tokens_after=total_before,
            dropped_urls=[],
            truncated_url=None,
            ranker_used=False,
        )

    by_url = dict(pages_with_content)
    ranked, ranker_used, ranker_usage = await _rank_urls_with_llm(list(by_url.keys()))

    fitted: list[tuple[str, str]] = []
    dropped: list[str] = []
    truncated_url: str | None = None
    consumed = 0

    for url in ranked:
        markdown = by_url[url]
        page_tok = page_tokens[url]
        remaining = budget - consumed
        # Once a boundary page has been truncated, the budget is conceptually exhausted —
        # every remaining URL goes to `dropped` even if a tiny round-trip slack would let it fit.
        if remaining <= 0 or truncated_url is not None:
            dropped.append(url)
            continue
        if page_tok <= remaining:
            fitted.append((url, markdown))
            consumed += page_tok
            continue
        # Boundary page: truncate the body so the full page block fits remaining budget.
        header_tokens = _count_tokens(_page_header(url))
        body_budget = max(0, remaining - header_tokens)
        truncated_md = TOKEN_ENCODING.decode(TOKEN_ENCODING.encode(markdown)[:body_budget])
        fitted.append((url, truncated_md))
        consumed += header_tokens + _count_tokens(truncated_md)
        truncated_url = url

    return fitted, _BudgetReport(
        fit=False,
        total_tokens_before=total_before,
        total_tokens_after=consumed,
        dropped_urls=dropped,
        truncated_url=truncated_url,
        ranker_used=ranker_used,
        ranker_usage=ranker_usage,
    )


def _page_block_tokens(url: str, body_tokens: int) -> int:
    """Total token count for the page block (header + body)."""
    return _count_tokens(_page_header(url)) + body_tokens


def _page_header(url: str) -> str:
    return f"--- PAGE: {url} ---\n"


def _count_tokens(text: str) -> int:
    return len(TOKEN_ENCODING.encode(text))


# ---------------------------------------------------------------------------
# URL ranker — small-model call, fires only on over-budget runs
# ---------------------------------------------------------------------------


class _RankedURLs(BaseModel):
    ordered: list[str] = Field(
        description=(
            "All input URLs returned in priority order, highest likely info density first. "
            "Every input URL must appear exactly once."
        )
    )


_RANKER_SYSTEM_PROMPT = (
    "You rank URLs from a single website by how likely each page contains the marketing/business "
    "information we want to extract: target audience, tone of voice, pricing and offer, "
    "geographic target market, unique selling proposition, and event dates. "
    "Return every input URL exactly once, ordered from most promising to least."
)


async def _rank_urls_with_llm(urls: list[str]) -> tuple[list[str], bool, dict | None]:
    """Return ``(ranked_urls, ranker_succeeded, usage_meta)``.

    ``usage_meta`` is non-None whenever the API call returned a response (even when
    we later reject the parsed content) — OpenAI bills for the call regardless, so
    we record it for cost attribution. It's None only when the call itself failed
    before returning (network error, 4xx/5xx).
    """
    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )
    usage_meta: dict | None = None
    try:
        response = await client.responses.parse(
            model=settings.openai_url_ranker_model,
            input=[
                {"role": "system", "content": _RANKER_SYSTEM_PROMPT},
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

        input_set = set(urls)
        returned_set = set(parsed.ordered)
        extra = returned_set - input_set
        if extra:
            # Model hallucinated/normalized URLs not present in input — can't trust the ordering.
            logger.debug(
                "Ranker returned URLs not in input (rejecting). extra=%d: %s",
                len(extra),
                sorted(extra),
            )
            raise ValueError("ranker returned URLs not in the input set")

        # Returned ⊆ input. Dedup preserving first occurrence; append any missing in deterministic order.
        seen: set[str] = set()
        deduped: list[str] = []
        for u in parsed.ordered:
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
        return deduped, True, usage_meta
    except Exception:  # noqa: BLE001 — any ranker failure must degrade gracefully to fallback
        logger.exception("URL ranker call failed; falling back to deterministic order")
        return _deterministic_url_order(urls), False, usage_meta


def _deterministic_url_order(urls: list[str]) -> list[str]:
    """Homepage (root path) first, then by URL path depth ascending, then alphabetical."""

    def sort_key(u: str) -> tuple[int, int, str]:
        path = urlparse(u).path.rstrip("/")
        is_root = 0 if path in ("", "/") else 1
        depth = path.count("/")
        return is_root, depth, u

    return sorted(urls, key=sort_key)


async def _mark_request_processing(session: AsyncSession, request: ScrapingRequest) -> None:
    request.status = "processing"
    await session.commit()


async def _mark_request_completed(session: AsyncSession, request: ScrapingRequest) -> None:
    request.status = "completed"
    request.finished_at = datetime.now(UTC)


async def _mark_request_failed(session: AsyncSession, request: ScrapingRequest, exc: BaseException) -> None:
    request.status = "failed"
    request.finished_at = datetime.now(UTC)
    request.error_code = type(exc).__name__
    request.error_message = str(exc)[:1000]


async def _mark_job_processing(session: AsyncSession, job: LLMProcessingJob) -> None:
    job.status = "processing"
    job.started_at = datetime.now(UTC)
    job.attempt_count = (job.attempt_count or 0) + 1
    await session.commit()


async def _mark_job_completed(session: AsyncSession, job: LLMProcessingJob) -> None:
    job.status = "completed"
    job.finished_at = datetime.now(UTC)


async def _mark_job_failed(session: AsyncSession, job: LLMProcessingJob, exc: BaseException) -> None:
    job.status = "failed"
    job.finished_at = datetime.now(UTC)
    job.error_code = type(exc).__name__
    job.error_message = str(exc)[:1000]


def _record_llm_call(session: AsyncSession, job: LLMProcessingJob, call_type: str, meta: dict) -> None:
    """Persist one LLM API call for cost attribution."""
    session.add(
        LLMCall(
            llm_processing_job_id=job.id,
            call_type=call_type,
            provider=PROVIDER,
            model=meta.get("model"),
            provider_response_id=meta.get("response_id"),
            input_tokens=meta.get("input_tokens"),
            output_tokens=meta.get("output_tokens"),
        )
    )


async def _persist_output(
    session: AsyncSession,
    job: LLMProcessingJob,
    parsed: MarTechExtraction | None,
) -> None:
    """Save just the parsed extraction fields. API metadata lives on the job row."""
    output_dict = parsed.model_dump(mode="json") if parsed is not None else {}
    session.add(
        LLMOutput(
            llm_processing_job_id=job.id,
            output_schema=OUTPUT_SCHEMA_NAME,
            output_schema_version="1",
            output=output_dict,
            summary=_short_summary(parsed),
        )
    )


def _short_summary(parsed: MarTechExtraction | None) -> str | None:
    if parsed is None:
        return None
    bits = []
    if parsed.location:
        bits.append(f"location={parsed.location}")
    if parsed.target_audience:
        bits.append(f"audience={parsed.target_audience[:60]}")
    return "; ".join(bits) or None
