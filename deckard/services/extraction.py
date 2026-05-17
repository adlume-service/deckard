"""LLM extraction orchestration.

Runs one OpenAI extraction job per ScrapingRequest. Concatenates all
captured pages' markdown, asks the configured OpenAI model for a structured
JSON object containing the mar-tech fields we care about, and stores the full
API response on `LLMOutput.output`.
"""

import logging
import uuid
from datetime import UTC, datetime

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from deckard.config import get_settings
from deckard.database.models import (
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
        description="Grupa docelowa — who the product/service is aimed at. Demographics, segments, use cases."
    )
    tone_of_voice: str | None = Field(
        description="How the brand communicates. Formal/casual, playful/serious, technical/accessible."
    )
    pricing_and_offer: str | None = Field(
        description="Pricing i oferta — what they sell and at what price. Null if not stated on the site."
    )
    location: str | None = Field(description="Lokalizacja — where the company is based. City, country, region.")
    usp: str | None = Field(
        description=(
            "Unique selling proposition the company explicitly claims. "
            "Null if absent — many companies don't articulate one."
        )
    )
    event_dates: list[str] | None = Field(
        description=(
            "Daty eventów — dates of events the company hosts or attends, if the site is event-related. Null otherwise."
        )
    )


_SYSTEM_PROMPT = (
    "You extract marketing/business information from a company's website. "
    "The user message contains the cleaned markdown of one or more pages from a single site, "
    "concatenated with page-URL separators. The site is most often in Polish; the field labels "
    "in parentheses below are the Polish names the business uses internally.\n\n"
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
            provider=PROVIDER,
            model=settings.openai_model,
            prompt_version=settings.extraction_prompt_version,
        )
        await _mark_request_processing(session, request)
        await _mark_job_processing(session, job)

        try:
            parsed, response_meta = await _call_openai(request.scraping_results)
        except Exception as exc:
            logger.exception("LLM extraction failed for request %s", scraping_request_id)
            await _mark_job_failed(session, job, exc)
            await _mark_request_failed(session, request, exc)
            await session.commit()
            return

        await _persist_output(session, job, parsed)
        await _mark_job_completed(session, job, response_meta)
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
    results: list[ScrapingResult],
) -> tuple[MarTechExtraction | None, dict]:
    """Call OpenAI with the concatenated markdown.

    Returns the parsed extraction (or None if the model produced no parsed
    output) and a small metadata dict — just the bits we save on
    `LLMProcessingJob` (`id`, `model`, token counts). The full API envelope
    is intentionally discarded.
    """
    settings = get_settings()
    user_message = _build_user_message(results)

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


def _build_user_message(results: list[ScrapingResult]) -> str:
    parts: list[str] = []
    for r in results:
        if not r.markdown:
            continue
        parts.append(f"--- PAGE: {r.url} ---\n{r.markdown}")
    if not parts:
        return "(no page content was captured)"
    return "\n\n".join(parts)


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


async def _mark_job_completed(session: AsyncSession, job: LLMProcessingJob, meta: dict) -> None:
    job.status = "completed"
    job.finished_at = datetime.now(UTC)
    job.provider_response_id = meta.get("response_id")
    if meta.get("model"):
        job.model = meta["model"]
    job.input_tokens = meta.get("input_tokens")
    job.output_tokens = meta.get("output_tokens")


async def _mark_job_failed(session: AsyncSession, job: LLMProcessingJob, exc: BaseException) -> None:
    job.status = "failed"
    job.finished_at = datetime.now(UTC)
    job.error_code = type(exc).__name__
    job.error_message = str(exc)[:1000]


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
