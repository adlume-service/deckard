import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from deckard.database.models.llm_processing_job import LLMProcessingJobStatus
from deckard.database.models.scraping_request import ScrapingRequestStatus


class ScrapingRequestCreate(BaseModel):
    """Payload for creating a new scraping request."""

    client_identifier: str = Field(
        ...,
        description=(
            "Stable identifier of the client submitting the request. "
            "If no client with this identifier exists, one is created on first use."
        ),
    )
    url: HttpUrl = Field(
        ...,
        description=(
            "URL of the page to scrape. If this client has not scraped this URL "
            "before, a Website entry is created for them on first use."
        ),
    )
    idempotency_key: str | None = Field(
        default=None,
        description=(
            "Optional client-supplied key for safe retries. Resubmitting with "
            "the same (client_identifier, idempotency_key) returns the original "
            "request unchanged instead of creating a duplicate. Recommended: "
            "generate a fresh UUID per submit-button-press on the frontend."
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form JSON metadata attached to the request.",
    )


class ScrapingRequestCreated(BaseModel):
    """Response returned immediately after creating a scraping request."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    website_id: uuid.UUID
    status: ScrapingRequestStatus = Field(
        ...,
        description=(
            "Lifecycle state. For a freshly created request this is always "
            "'pending'. See ScrapingRequestRead.status for the full state machine."
        ),
    )
    requested_url: str
    requested_at: datetime


class ScrapingResultRead(BaseModel):
    """Output of the scraping phase. Present once status reaches 'scraped'."""

    model_config = ConfigDict(from_attributes=True)

    final_url: str | None = Field(
        default=None,
        description=(
            "URL the scraper actually landed on after following redirects. May "
            "differ from `requested_url`. Null if the fetch never completed."
        ),
    )
    success: bool = Field(
        ...,
        description=(
            "Whether the scraper was able to fetch a usable response. False "
            "indicates a transport-level failure (DNS, TLS, timeout, etc.); "
            "non-2xx responses still count as success — check `status_code`."
        ),
    )
    status_code: int | None = Field(
        default=None,
        description="HTTP status code returned by the target site.",
    )
    markdown: str | None = Field(
        default=None,
        description=(
            "Cleaned plain-text/markdown rendering of the page. Suitable for display or downstream LLM input."
        ),
    )
    created_at: datetime


class LLMOutputRead(BaseModel):
    """Structured extraction produced by an LLM processing job."""

    model_config = ConfigDict(from_attributes=True)

    output_schema: str | None = Field(
        default=None,
        description=(
            "Name of the extraction schema used to shape `output` (e.g. "
            "'default_extraction'). Null if the job did not declare a schema."
        ),
    )
    output_schema_version: str | None = Field(
        default=None,
        description="Version of the named schema, if applicable.",
    )
    output: dict[str, Any] = Field(
        ...,
        description=(
            "Structured extraction. The shape of this object depends on "
            "`output_schema` — consult that schema's documentation to know "
            "what fields to expect."
        ),
    )
    summary: str | None = Field(
        default=None,
        description="Optional human-readable summary of the extracted content.",
    )
    created_at: datetime


class LLMProcessingJobRead(BaseModel):
    """A single LLM extraction job for a scraping request."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_type: str = Field(
        ...,
        description=(
            "Kind of LLM job. 'default_extraction' is the standard pipeline; "
            "additional types may be introduced over time."
        ),
    )
    status: LLMProcessingJobStatus = Field(
        ...,
        description=(
            "Lifecycle of this job. 'pending' = queued; 'processing' = LLM "
            "call in flight; 'completed' = `llm_output` is populated; "
            "'failed' = terminal failure (see `error_code`/`error_message`); "
            "'cancelled' = manually cancelled."
        ),
    )
    provider: str | None = Field(
        default=None,
        description="LLM provider that ran the job (e.g. 'anthropic', 'openai').",
    )
    model: str | None = Field(
        default=None,
        description="Provider-specific model identifier.",
    )
    started_at: datetime | None = Field(
        default=None,
        description="Set when the LLM call begins; null while status is 'pending'.",
    )
    finished_at: datetime | None = Field(
        default=None,
        description="Set when the LLM call ends, on success or failure.",
    )
    error_code: str | None = Field(
        default=None,
        description="Machine-readable error code; populated only when status is 'failed'.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error description; populated only when status is 'failed'.",
    )
    llm_output: LLMOutputRead | None = Field(
        default=None,
        description="The extraction output. Populated only when status is 'completed'.",
    )


class ScrapingRequestRead(BaseModel):
    """Full view of a scraping request, including downstream results when present."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    website_id: uuid.UUID
    status: ScrapingRequestStatus = Field(
        ...,
        description=(
            "Lifecycle state of the request. Typical happy-path transition is "
            "pending → scraping → scraped → processing → completed. "
            "'pending' = submitted, not yet picked up. "
            "'scraping' = scraper is fetching the page. "
            "'scraped' = page captured, `scraping_result` is populated, awaiting LLM. "
            "'processing' = at least one LLM job is in flight. "
            "'completed' = all work finished; `scraping_result` and "
            "`llm_processing_jobs` are populated. "
            "'failed' = terminal failure at some stage (see `error_code`/`error_message`). "
            "'cancelled' = manually cancelled."
        ),
    )
    requested_url: str = Field(
        ...,
        description="URL submitted by the client. See `scraping_result.final_url` for the post-redirect URL.",
    )
    idempotency_key: str | None = Field(
        default=None,
        description="The idempotency key the client supplied at creation, if any.",
    )
    attempt_count: int = Field(
        ...,
        description="Number of times the scraper has attempted to fetch this URL.",
    )
    requested_at: datetime
    started_at: datetime | None = Field(
        default=None,
        description="Set when the scraper first picks the request up. Null while 'pending'.",
    )
    finished_at: datetime | None = Field(
        default=None,
        description="Set when the request reaches a terminal status ('completed', 'failed', or 'cancelled').",
    )
    error_code: str | None = Field(
        default=None,
        description="Machine-readable error code. Populated only when status is 'failed'.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error description. Populated only when status is 'failed'.",
    )

    scraping_result: ScrapingResultRead | None = Field(
        default=None,
        description="Scraping output. Null until status reaches 'scraped'.",
    )
    llm_processing_jobs: list[LLMProcessingJobRead] = Field(
        default_factory=list,
        description=(
            "LLM jobs spawned for this request. Empty array until status "
            "reaches 'processing'. Each job has its own status; check "
            "`llm_output` on each to know which have finished."
        ),
    )
