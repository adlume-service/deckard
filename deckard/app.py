import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from deckard.config import get_settings
from deckard.endpoints.scraping_requests import router as scraping_requests_router
from deckard.endpoints.status import router as status_router
from deckard.logging import configure_logging
from deckard.ui.router import router as ui_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
    )

    # allow_credentials=True is incompatible with allow_origins=["*"]; list explicit origins in env.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    app.include_router(status_router)
    app.include_router(scraping_requests_router)
    app.include_router(ui_router)

    logger.info(
        "Application configured",
        extra={"environment": settings.environment},
    )

    return app


app = create_app()
