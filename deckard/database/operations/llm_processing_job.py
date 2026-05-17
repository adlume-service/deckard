import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from deckard.database.models import LLMProcessingJob


async def create_for_request(
    session: AsyncSession,
    *,
    scraping_request_id: uuid.UUID,
    job_type: str = "default_extraction",
    provider: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
) -> LLMProcessingJob:
    job = LLMProcessingJob(
        scraping_request_id=scraping_request_id,
        job_type=job_type,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
    )
    session.add(job)
    await session.flush()
    return job


async def get_with_output(session: AsyncSession, job_id: uuid.UUID) -> LLMProcessingJob | None:
    stmt = (
        select(LLMProcessingJob).where(LLMProcessingJob.id == job_id).options(selectinload(LLMProcessingJob.llm_output))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
