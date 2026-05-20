import uuid
from datetime import datetime
from typing import Any

from pydantic import AliasPath, BaseModel, ConfigDict, Field, HttpUrl

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
    marketing_stack: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasPath("request_metadata", "marketing_stack"),
        description=(
            "Marketing-stack detection report. Populated once the scrape "
            "completes and detection succeeds. Null while status is "
            "'pending'/'scraping', or when detection failed (in which case "
            "`marketing_stack_error` is set). Shape: `detector_version` (str, currently '1'), "
            "`detected_at` (ISO 8601), `vendors` (mapping of vendor key to "
            "`{detected, ids, evidence, extras}`), and `server_side_hints` "
            "(list of `{signal, confidence, evidence}`). Note: `evidence[*].snippet` "
            "contains raw HTML/JS fragments capped at ~240 chars per snippet — "
            "be mindful of token budgets if feeding this into a prompt. The "
            "shape is still evolving; treat unknown keys as forward-compatible additions."
        ),
        json_schema_extra={
            "example": {
                "detector_version": "1",
                "detected_at": "2026-05-19T10:30:00+00:00",
                "vendors": {
                    "gtm": {
                        "detected": True,
                        "ids": ["GTM-ABC123"],
                        "evidence": [{"source": "html", "snippet": "googletagmanager.com/gtm.js?id=GTM-ABC123"}],
                        "extras": {
                            "id_count": 1,
                            "load_context": "direct",
                            "first_party_mode": True,
                            "source": "html",
                            "transport_url": "https://sgtm.brand.com",
                            "consent_mode_v2": "denied",
                            "container_fetch_status": "ok",
                            "container_tags": [{"type": "ga4", "id": "G-XYZ"}],
                        },
                    }
                },
                "server_side_hints": [
                    {
                        "signal": "gtm_transport_url_first_party",
                        "confidence": "high",
                        "evidence": "GTM transport_url=https://sgtm.brand.com — non-Google subdomain of requested host",
                    }
                ],
            }
        },
    )
    marketing_stack_error: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasPath("request_metadata", "marketing_stack_error"),
        description=(
            "Set instead of `marketing_stack` when detection raised or had no "
            "usable seed HTML to inspect. Null on success and while detection "
            "has not yet run. Shape: `error_code` (str, e.g. `'no_seed_html'` "
            "or an exception class name like `'RuntimeError'`), `error_message` "
            "(str, human-readable), `detector_version` (str)."
        ),
        json_schema_extra={
            "example": {
                "error_code": "no_seed_html",
                "error_message": "Seed page raw HTML is empty; detection skipped.",
                "detector_version": "1",
            }
        },
    )
    performance: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasPath("request_metadata", "performance"),
        description=(
            "Google PageSpeed Insights report for the seed URL. Populated once "
            "the scrape completes and the PSI call succeeds. Null when "
            "`PAGE_SPEED_INSIGHTS_API` is unset, while status is "
            "'pending'/'scraping', or when the call failed (in which case "
            "`performance_error` is set). Shape: `detector_version` (str), "
            "`strategy` ('mobile'|'desktop'), `score` (int 0-100 — overall "
            "Lighthouse performance score), `metrics` (lab metrics in ms; CLS "
            "is unitless), `field_data` (real-user CrUX data, may be null), "
            "`opportunities` (top 5 fixes ranked by potential savings), "
            "`diagnostics` (top 5 failing audits), `fetched_at` (ISO 8601)."
        ),
        json_schema_extra={
            "example": {
                "detector_version": "1",
                "strategy": "mobile",
                "score": 62,
                "metrics": {
                    "first_contentful_paint_ms": 1820,
                    "largest_contentful_paint_ms": 3140,
                    "cumulative_layout_shift": 0.08,
                    "total_blocking_time_ms": 290,
                    "speed_index_ms": 4100,
                    "time_to_interactive_ms": 5200,
                    "server_response_time_ms": 410,
                },
                "field_data": None,
                "opportunities": [
                    {
                        "id": "unused-javascript",
                        "title": "Reduce unused JavaScript",
                        "description": "Reduce unused JavaScript and defer loading of scripts...",
                        "savings_ms": 1200,
                        "savings_bytes": 84000,
                        "display_value": "Potential savings of 84 KiB",
                    }
                ],
                "diagnostics": [],
                "lighthouse_version": "11.0.0",
                "final_url": "https://www.brand.com/",
                "fetched_at": "2026-05-19T10:30:00+00:00",
            }
        },
    )
    performance_error: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasPath("request_metadata", "performance_error"),
        description=(
            "Set instead of `performance` when the PageSpeed Insights call "
            "raised. Null on success and when no key is configured. Shape: "
            "`error_code` (exception class name, e.g. `'HTTPStatusError'`), "
            "`error_message` (str, human-readable), `detector_version` (str)."
        ),
        json_schema_extra={
            "example": {
                "error_code": "HTTPStatusError",
                "error_message": "Client error '429 Too Many Requests' for url ...",
                "detector_version": "1",
            }
        },
    )
