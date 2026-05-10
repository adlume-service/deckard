import logging

from fastapi import FastAPI

from deckard.config import get_settings
from deckard.logging import configure_logging
from deckard.endpoints.status import router as status_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
    )

    app.include_router(status_router)

    logger.info(
        "Application configured",
        extra={"environment": settings.environment},
    )

    return app


app = create_app()
