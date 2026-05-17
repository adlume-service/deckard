from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentType = Literal[
    "local",
    "staging",
    "production",
]


LogLevelType = Literal[
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
]


class Settings(BaseSettings):
    app_name: str = "Deckard"

    environment: EnvironmentType = "local"

    log_level: LogLevelType = "INFO"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/deckard"
    database_echo: bool = False

    scrape_max_depth: int = 2
    scrape_max_pages: int = 50
    scrape_request_timeout_seconds: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
