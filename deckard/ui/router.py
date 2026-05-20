"""Server-rendered tester UI for Deckard.

Three views — login, table of historical requests, and a detail page that
auto-refreshes (via HTMX polling) while the request is still in flight.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import HttpUrl, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from deckard.database.operations import scraping_request as scraping_request_ops
from deckard.database.session import get_session
from deckard.endpoints.scraping_requests import create_request_and_schedule
from deckard.schemas.scraping_request import (
    ScrapingRequestCreate,
    ScrapingRequestRead,
    ScrapingRequestSummary,
)
from deckard.ui.auth import (
    LOGIN_PATH,
    CurrentUiApiUser,
    clear_session_cookie,
    lookup_api_user_by_key,
    set_session_cookie,
)
from deckard.ui.templates import templates

SessionDep = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/ui", tags=["UI"], include_in_schema=False)

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
DEFAULT_PAGE_SIZE = 25


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: str | None = Query(default=None)) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": error})


@router.post("/login")
async def login_submit(
    session: SessionDep,
    api_key: Annotated[str, Form()],
) -> RedirectResponse:
    api_user = await lookup_api_user_by_key(session, api_key.strip())
    if api_user is None:
        return RedirectResponse(f"{LOGIN_PATH}?error=invalid", status_code=status.HTTP_303_SEE_OTHER)
    response = RedirectResponse("/ui/", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, api_key.strip())
    return response


@router.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse(LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response)
    return response


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    session: SessionDep,
    api_user: CurrentUiApiUser,
    offset: int = Query(default=0, ge=0),
    submit_error: str | None = Query(default=None),
) -> HTMLResponse:
    items, total = await scraping_request_ops.list_for_api_user(
        session,
        api_user_id=api_user.id,
        limit=DEFAULT_PAGE_SIZE,
        offset=offset,
    )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "api_user": api_user,
            "items": [ScrapingRequestSummary.model_validate(r) for r in items],
            "total": total,
            "limit": DEFAULT_PAGE_SIZE,
            "offset": offset,
            "terminal_statuses": TERMINAL_STATUSES,
            "submit_error": submit_error,
        },
    )


@router.post("/requests")
async def submit_request(
    session: SessionDep,
    background_tasks: BackgroundTasks,
    api_user: CurrentUiApiUser,
    url: Annotated[str, Form()],
    client_identifier: Annotated[str, Form()],
    idempotency_key: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    try:
        payload = ScrapingRequestCreate(
            client_identifier=client_identifier.strip(),
            url=HttpUrl(url.strip()),
            idempotency_key=(idempotency_key or "").strip() or None,
        )
    except ValidationError:
        return RedirectResponse("/ui/?submit_error=invalid", status_code=status.HTTP_303_SEE_OTHER)

    new_request = await create_request_and_schedule(
        session=session,
        background_tasks=background_tasks,
        api_user=api_user,
        payload=payload,
    )
    return RedirectResponse(f"/ui/requests/{new_request.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/requests/{request_id}", response_class=HTMLResponse)
async def request_detail(
    request_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    api_user: CurrentUiApiUser,
) -> HTMLResponse:
    read_model = await _load_request_for_user(session, request_id, api_user_id=api_user.id)
    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "api_user": api_user,
            "req": read_model,
            "terminal_statuses": TERMINAL_STATUSES,
        },
    )


@router.get("/requests/{request_id}/body", response_class=HTMLResponse)
async def request_detail_body(
    request_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    api_user: CurrentUiApiUser,
) -> HTMLResponse:
    """HTMX-polled body fragment. Re-renders the detail panel until status is terminal."""
    read_model = await _load_request_for_user(session, request_id, api_user_id=api_user.id)
    return templates.TemplateResponse(
        request,
        "_detail_body.html",
        {
            "req": read_model,
            "terminal_statuses": TERMINAL_STATUSES,
        },
    )


async def _load_request_for_user(
    session: AsyncSession, request_id: uuid.UUID, *, api_user_id: uuid.UUID
) -> ScrapingRequestRead:
    db_request = await scraping_request_ops.get_with_details(session, request_id)
    if db_request is None or db_request.api_user_id != api_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    return ScrapingRequestRead.model_validate(db_request)
