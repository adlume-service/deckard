from functools import lru_cache
from typing import Literal

from pydantic import field_validator
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


ReasoningEffort = Literal[
    "minimal",
    "low",
    "medium",
    "high",
]


class Settings(BaseSettings):
    app_name: str = "Deckard"

    environment: EnvironmentType = "local"

    log_level: LogLevelType = "INFO"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/deckard"
    database_echo: bool = False

    scrape_max_depth: int = 2
    scrape_max_pages: int = 20
    scrape_request_timeout_seconds: int = 30

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    openai_reasoning_effort: ReasoningEffort = "medium"
    # Sized against the account's TPM quota (200K/min for our tier), not the model's 400K context window,
    # TPM is the tighter ceiling. 85% leaves headroom for reasoning + output, which also count against TPM.
    openai_context_token_budget: int = int(200_000 * 0.85)
    openai_request_timeout_seconds: int = 120
    extraction_prompt_version: str = "v1"

    openai_url_ranker_model: str = "gpt-5.4-nano"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    @field_validator("database_url", mode="after")
    @classmethod
    def _force_asyncpg_driver(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
