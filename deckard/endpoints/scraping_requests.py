import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from deckard.database.operations import scraping_request as scraping_request_ops
from deckard.database.session import get_session
from deckard.schemas.scraping_request import (
    ScrapingRequestCreate,
    ScrapingRequestCreated,
    ScrapingRequestRead,
)
from deckard.services.extraction import process_llm_job
from deckard.services.scraping import process_scraping_request

SessionDep = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/scraping-requests", tags=["Scraping Requests"])


@router.post(
    "",
    response_model=ScrapingRequestCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new scraping request",
    description=(
        "Creates a scraping request for the given client and URL. The client "
        "(identified by `client_identifier`) and the website (identified by "
        "`url`) are created on first use. Supplying an `idempotency_key` makes "
        "the call safe to retry — subsequent requests with the same key for "
        "the same client return the original request unchanged."
    ),
)
async def create_scraping_request(
    payload: ScrapingRequestCreate,
    session: SessionDep,
    background_tasks: BackgroundTasks,
) -> ScrapingRequestCreated:
    request = await scraping_request_ops.create(
        session,
        client_identifier=payload.client_identifier,
        url=str(payload.url),
        idempotency_key=payload.idempotency_key,
        metadata=payload.metadata,
    )
    await session.commit()

    if request.status == "pending":
        background_tasks.add_task(process_scraping_request, request.id)
        background_tasks.add_task(process_llm_job, request.id)

    return ScrapingRequestCreated.model_validate(request)


@router.get(
    "/{request_id}",
    response_model=ScrapingRequestRead,
    status_code=status.HTTP_200_OK,
    summary="Fetch a scraping request and its results",
    description=(
        "Returns the current status of a scraping request. When the scrape "
        "has completed, the response includes the `scraping_result` payload. "
        "When LLM processing has produced any outputs, they are included "
        "under `llm_processing_jobs`."
    ),
    responses={status.HTTP_404_NOT_FOUND: {"description": "Scraping request not found."}},
)
async def get_scraping_request(
    request_id: uuid.UUID,
    session: SessionDep,
) -> ScrapingRequestRead:
    request = await scraping_request_ops.get_with_details(session, request_id)
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scraping request {request_id} not found.",
        )
    return ScrapingRequestRead.model_validate(request)
