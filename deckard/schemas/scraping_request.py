import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from deckard.database.models.llm_processing_job import LLMProcessingJobStatus
from deckard.database.models.scraping_request import ScrapingRequestStatus


class ScrapingRequestCreate(BaseModel):
    """Payload for creating a new scraping request.

    The submitting ApiUser is identified by the bearer token; the Client this
    request belongs to (for billing and grouping) is supplied per-request by
    `client_identifier`.
    """

    client_identifier: str = Field(
        ...,
        description=(
            "Stable identifier of the Client this request belongs to. Used for "
            "billing and grouping. If no Client with this identifier exists, one "
            "is created on first use. Identifiers are global — two ApiUsers "
            "using the same identifier refer to the same Client."
        ),
    )
    url: HttpUrl = Field(
        ...,
        description=(
            "URL of the page to scrape. If this Client has not scraped this URL "
            "before, a Website entry is created on first use."
        ),
    )
    idempotency_key: str | None = Field(
        default=None,
        description=(
            "Optional caller-supplied key for safe retries. Resubmitting with "
            "the same idempotency_key from the same authenticated ApiUser "
            "returns the original request unchanged instead of creating a "
            "duplicate. Recommended: generate a fresh UUID per submit-button-press."
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
    """A single page scraped as part of a ScrapingRequest. One request can produce many of these."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str = Field(
        ...,
        description=(
            "URL of this specific page. For the seed page this matches the "
            "request's `requested_url`; for pages discovered via deep crawl it "
            "is the discovered link."
        ),
    )
    final_url: str | None = Field(
        default=None,
        description=(
            "URL the scraper actually landed on after following redirects. May "
            "differ from `url`. Null if the fetch never completed."
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
    tokens: int | None = Field(
        default=None,
        description=(
            "Token count of `markdown` per the canonical `o200k_base` encoding, "
            "computed once at scrape time. Null if the markdown is null or the row "
            "predates this field."
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
    ranker_used: bool | None = Field(
        default=None,
        description=(
            "Null when no shrinkage was needed. True if the small-model URL ranker "
            "ordered the pages; false if the ranker call failed and we fell back to a "
            "deterministic ordering."
        ),
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
            "'scraped' = page captured, `scraping_results` is populated, awaiting LLM. "
            "'processing' = at least one LLM job is in flight. "
            "'completed' = all work finished; `scraping_results` and "
            "`llm_processing_jobs` are populated. "
            "'failed' = terminal failure at some stage (see `error_code`/`error_message`). "
            "'cancelled' = manually cancelled."
        ),
    )
    requested_url: str = Field(
        ...,
        description="Seed URL submitted by the client. See each `scraping_results[].final_url` for post-redirect URLs.",
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

    scraping_results: list[ScrapingResultRead] = Field(
        default_factory=list,
        description=(
            "Pages scraped for this request. Empty until status reaches "
            "'scraping'; populated progressively as the crawler captures each "
            "page. A single request may produce many results when the backend "
            "follows nested links."
        ),
    )
    llm_processing_jobs: list[LLMProcessingJobRead] = Field(
        default_factory=list,
        description=(
            "LLM jobs spawned for this request. Empty array until status "
            "reaches 'processing'. Each job has its own status; check "
            "`llm_output` on each to know which have finished."
        ),
    )
