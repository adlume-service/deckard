import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from deckard.auth import CurrentApiUser
from deckard.database.models import ApiUser, ScrapingRequest
from deckard.database.operations import scraping_request as scraping_request_ops
from deckard.database.session import get_session
from deckard.schemas.scraping_request import (
    ScrapingRequestCreate,
    ScrapingRequestCreated,
    ScrapingRequestList,
    ScrapingRequestRead,
    ScrapingRequestSummary,
)
from deckard.services.extraction import process_llm_job
from deckard.services.scraping import process_scraping_request

SessionDep = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/scraping-requests", tags=["Scraping Requests"])


async def create_request_and_schedule(
    *,
    session: AsyncSession,
    background_tasks: BackgroundTasks,
    api_user: ApiUser,
    payload: ScrapingRequestCreate,
) -> ScrapingRequest:
    """Persist a ScrapingRequest and schedule the scrape + extraction pipeline.

    Shared between the JSON endpoint and the Jinja UI; both go through the same
    ops layer so the pipeline is identical.
    """
    request = await scraping_request_ops.create(
        session,
        api_user=api_user,
        client_identifier=payload.client_identifier,
        url=str(payload.url),
        idempotency_key=payload.idempotency_key,
        metadata=payload.metadata,
    )
    await session.commit()

    if request.status == "pending":
        background_tasks.add_task(process_scraping_request, request.id)
        background_tasks.add_task(process_llm_job, request.id)

    return request


@router.post(
    "",
    response_model=ScrapingRequestCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new scraping request",
    description=(
        "Creates a scraping request submitted by the authenticated ApiUser on "
        "behalf of the Client identified by `client_identifier`. The Client "
        "and Website are created on first use. Supplying an `idempotency_key` "
        "makes the call safe to retry — subsequent requests with the same key "
        "from the same ApiUser return the original request unchanged."
    ),
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid API key."}},
)
async def create_scraping_request(
    payload: ScrapingRequestCreate,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    api_user: CurrentApiUser,
) -> ScrapingRequestCreated:
    request = await create_request_and_schedule(
        session=session,
        background_tasks=background_tasks,
        api_user=api_user,
        payload=payload,
    )
    return ScrapingRequestCreated.model_validate(request)


@router.get(
    "",
    response_model=ScrapingRequestList,
    status_code=status.HTTP_200_OK,
    summary="List scraping requests submitted by the authenticated ApiUser",
    description=(
        "Returns a paginated, newest-first list of scraping requests owned by "
        "the calling ApiUser. The summary projection omits per-page results, "
        "LLM jobs, and `request_metadata` — fetch a specific id to get the "
        "full payload."
    ),
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid API key."}},
)
async def list_scraping_requests(
    session: SessionDep,
    api_user: CurrentApiUser,
    limit: int = Query(50, ge=1, le=200, description="Page size (max 200)."),
    offset: int = Query(0, ge=0, description="Number of rows to skip."),
) -> ScrapingRequestList:
    items, total = await scraping_request_ops.list_for_api_user(
        session,
        api_user_id=api_user.id,
        limit=limit,
        offset=offset,
    )
    return ScrapingRequestList(
        items=[ScrapingRequestSummary.model_validate(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


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
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid API key."},
        status.HTTP_404_NOT_FOUND: {"description": "Scraping request not found."},
    },
)
async def get_scraping_request(
    request_id: uuid.UUID,
    session: SessionDep,
    api_user: CurrentApiUser,
) -> ScrapingRequestRead:
    request = await scraping_request_ops.get_with_details(session, request_id)
    if request is None or request.api_user_id != api_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scraping request {request_id} not found.",
        )
    return ScrapingRequestRead.model_validate(request)
