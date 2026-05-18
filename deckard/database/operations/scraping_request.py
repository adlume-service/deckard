import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from deckard.database.models import ApiUser, LLMProcessingJob, ScrapingRequest
from deckard.database.operations import client as client_ops
from deckard.database.operations import website as website_ops


async def create(
    session: AsyncSession,
    *,
    api_user: ApiUser,
    client_identifier: str,
    url: str,
    idempotency_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ScrapingRequest:
    client = await client_ops.get_or_create_by_identifier(session, client_identifier)
    website = await website_ops.get_or_create_for_client(session, client.id, url)

    if idempotency_key is not None:
        existing = await session.execute(
            select(ScrapingRequest).where(
                ScrapingRequest.api_user_id == api_user.id,
                ScrapingRequest.idempotency_key == idempotency_key,
            )
        )
        existing_request = existing.scalar_one_or_none()
        if existing_request is not None:
            return existing_request

    request = ScrapingRequest(
        client_id=client.id,
        api_user_id=api_user.id,
        website_id=website.id,
        requested_url=url,
        idempotency_key=idempotency_key,
        request_metadata=metadata or {},
    )
    session.add(request)
    await session.flush()
    return request


async def get_with_details(session: AsyncSession, request_id: uuid.UUID) -> ScrapingRequest | None:
    stmt = (
        select(ScrapingRequest)
        .where(ScrapingRequest.id == request_id)
        .options(
            selectinload(ScrapingRequest.scraping_results),
            selectinload(ScrapingRequest.llm_processing_jobs).selectinload(LLMProcessingJob.llm_output),
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
