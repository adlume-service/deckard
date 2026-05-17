import logging

from fastapi import FastAPI

from deckard.config import get_settings
from deckard.endpoints.scraping_requests import router as scraping_requests_router
from deckard.endpoints.status import router as status_router
from deckard.logging import configure_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
    )

    app.include_router(status_router)
    app.include_router(scraping_requests_router)

    logger.info(
        "Application configured",
        extra={"environment": settings.environment},
    )

    return app


app = create_app()
