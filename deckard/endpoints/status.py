from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from deckard.config import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]

router = APIRouter(tags=["Status"])


class HealthCheckResponse(BaseModel):
    status: str
    service: str
    environment: str
    timestamp: datetime


@router.get(
    "/status",
    response_model=HealthCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Application health status",
    description="Returns the current operational status of the application.",
)
async def status(settings: SettingsDep) -> HealthCheckResponse:
    return HealthCheckResponse(
        status="healthy",
        service=settings.app_name,
        environment=settings.environment,
        timestamp=datetime.now(UTC),
    )
