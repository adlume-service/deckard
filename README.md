# Deckard

FastAPI service that scrapes a website, packs the captured markdown into an
LLM context budget, and extracts structured mar-tech fields from the result.

One submitted URL turns into:

1. a deep BFS crawl of the site (via [crawl4ai](https://github.com/unclecode/crawl4ai)),
2. a static marketing-stack scan of the seed page (detected vendors,
   GTM container parse, server-side tracking hints),
3. a Google PageSpeed Insights audit of the seed URL (Lighthouse
   performance score, Core Web Vitals, top opportunities — best-effort,
   skipped silently when no API key is configured),
4. an OpenAI extraction call that returns a typed JSON object
   (`target_audience`, `tone_of_voice`, `pricing_and_offer`, `location`,
   `usp`, `event_dates`),
5. a row per API call so spend can be attributed back to the originating
   `ApiUser` and `Client`.

---

## How it works

### High-level architecture

```mermaid
flowchart LR
    Caller([API caller])

    subgraph FastAPI["FastAPI app (deckard.app)"]
        direction TB
        Auth[ApiUser bearer-token auth]
        EP_POST["POST /scraping-requests"]
        EP_GET["GET /scraping-requests/&#123;id&#125;"]
        EP_STATUS["GET /status (public)"]
    end

    subgraph BG["Background tasks"]
        direction TB
        Scraper[Scraping service<br/>crawl4ai BFS deep-crawl]
        Detector[Marketing-stack detector<br/>static scan + GTM container parse]
        PerfAudit[Performance auditor<br/>Google PageSpeed Insights v5]
        Extractor[Extraction service<br/>OpenAI Responses API]
        Ranker[(Small URL-ranker LLM<br/>only when over budget)]
    end

    DB[(PostgreSQL<br/>SQLAlchemy / asyncpg)]
    Web[(Target websites)]
    GTM[(googletagmanager.com<br/>gtm.js container)]
    PSI[(googleapis.com<br/>PageSpeed Insights API)]
    OAI[(OpenAI API)]

    Caller -->|Bearer deckard_live_*| Auth
    Auth --> EP_POST
    Auth --> EP_GET
    Caller --> EP_STATUS

    EP_POST -->|persist request| DB
    EP_POST -.->|enqueue| Scraper
    EP_POST -.->|enqueue| Extractor

    Scraper --> Web
    Scraper -->|ScrapingResult rows| DB
    Scraper -->|seed HTML + headers| Detector
    Detector -.->|optional gtm.js fetch| GTM
    Detector -->|marketing_stack on request_metadata| DB
    Scraper -->|seed URL| PerfAudit
    PerfAudit --> PSI
    PerfAudit -->|performance on request_metadata| DB

    Extractor -->|load pages| DB
    Extractor --> Ranker
    Ranker --> OAI
    Extractor --> OAI
    Extractor -->|LLMOutput + LLMCall rows| DB

    EP_GET --> DB
```

The HTTP layer is intentionally thin: it authenticates, validates, persists
the request, and immediately schedules the scrape and extraction as
`BackgroundTasks`. All long-running work happens on its own DB session.

### Request lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant C as Caller
    participant API as FastAPI
    participant DB as Postgres
    participant S as Scraper (crawl4ai)
    participant P as PageSpeed Insights
    participant E as Extractor
    participant O as OpenAI

    C->>API: POST /scraping-requests<br/>{client_identifier, url, idempotency_key?}
    API->>API: Authenticate ApiUser (SHA-256 hash)
    API->>DB: upsert Client + Website, insert ScrapingRequest (status=pending)
    API-->>C: 201 Created (request id, status=pending)

    Note over API,S: BackgroundTasks fire after the response is sent

    API->>S: process_scraping_request(id)
    S->>DB: status = scraping
    S->>S: BFS deep-crawl from seed URL
    S->>DB: persist ScrapingResult per page
    S->>S: detect marketing stack on seed HTML
    S->>S: (optional) fetch + parse GTM container
    S->>P: runPagespeed (if API key configured)
    P-->>S: Lighthouse score + Core Web Vitals
    S->>DB: marketing_stack + performance on request_metadata<br/>status = scraped (or failed)

    API->>E: process_llm_job(id)
    E->>DB: load ScrapingRequest + results
    E->>E: token-budget fit (optional URL ranker call)
    E->>O: responses.parse(MarTechExtraction)
    O-->>E: parsed JSON + usage
    E->>DB: LLMOutput, LLMCall(s)<br/>status = completed (or failed)

    C->>API: GET /scraping-requests/{id}
    API->>DB: fetch request + results + jobs
    API-->>C: 200 OK with structured payload
```

### Status state machine

A single `status` column on `scraping_requests` is advanced by the two
services. The terminal states are `completed`, `failed`, and `cancelled`.

```mermaid
stateDiagram-v2
    [*] --> pending: POST /scraping-requests
    pending --> scraping: scraper picks it up
    scraping --> scraped: BFS crawl finishes
    scraping --> failed: crawl error
    scraped --> processing: extractor picks it up
    processing --> completed: OpenAI returns parsed output
    processing --> failed: extractor / API error
    pending --> cancelled: (admin)
    completed --> [*]
    failed --> [*]
    cancelled --> [*]
```

### Marketing-stack detection

At the tail of each scrape — after the per-page `ScrapingResult` rows are
written and before the request flips to `scraped` — a static, pattern-based
detector scans the seed page's raw HTML and response headers and writes the
result to `ScrapingRequest.request_metadata.marketing_stack` in the same
transaction that flips the status. There is no separate table.

The catalogue covers ~30 vendors across analytics (GTM, GA4, UA),
client-side pixels (Meta, LinkedIn, TikTok, Pinterest, Reddit, Snap, Google
Ads, Microsoft UET), product analytics (Hotjar, Segment, Mixpanel,
Amplitude), MAP/CRM (HubSpot, Klaviyo, Marketo, Pardot, Braze, Customer.io,
Intercom), consent management (OneTrust, Cookiebot, Didomi, Usercentrics,
Termly), A/B testing (Optimizely, VWO), reviews (Yotpo, Trustpilot, Okendo),
and ecom platform fingerprints (Shopify, Magento, BigCommerce, WooCommerce).

When GTM is found, the orchestrator makes a single best-effort HTTPS request
to `googletagmanager.com/gtm.js?id=<container_id>`, parses the container
body, and enriches the GTM entry with `container_tags`, `transport_url`,
and `consent_mode_v2`. Vendors found *only* inside the container (e.g. a
Meta Pixel fired via GTM with no client-side `fbq()` on the page) are
synthesised with `extras.load_context = "gtm"` and `extras.source = "gtm_container"`,
so downstream consumers can tell "loaded directly" from "configured in GTM".

Server-side tracking is reported as `server_side_hints` with calibrated
confidence (`low | medium | high`) and visible evidence — no DNS lookups in
v1, so `high` requires either a direct script reference to a known sGTM
vendor domain (`stape.io`, `addingwell.com`) or a GTM `transport_url`
pointing to a non-Google subdomain of the requested host.

Detection runs in an isolated try/except: on any failure the request still
completes as `scraped`, and `marketing_stack_error` is written under
`request_metadata` instead of `marketing_stack`. Both keys are surfaced as
top-level fields on the GET response via Pydantic `AliasPath` — the SA
model stays free of presentation-layer concerns.

### Performance audit (PageSpeed Insights)

Right after the marketing-stack write — still inside the same transaction
that flips status to `scraped` — Deckard makes a single call to Google's
PageSpeed Insights v5 API against the seed URL and persists a trimmed
report to `ScrapingRequest.request_metadata.performance`. As with the
marketing-stack detector, there is no separate table.

The free Google quota is 25k requests/day with a free API key, and
typical PSI latency is 10–30s per call (occasionally 60s+). Authentication
goes via the `X-goog-api-key` header, **not** the `key` query parameter,
so the key never appears in URLs that might be echoed back in error
messages. The persisted `performance_error.error_message` is also
defensively scrubbed of the key as belt-and-suspenders.

The raw PSI response is 1–2 MB; we trim it down to ~5–15 KB by keeping
only what a marketing audit actually consumes:

- the Lighthouse `performance` category score (0–100),
- the canonical lab metrics — FCP, LCP, Speed Index, TTI, TBT, CLS, and
  server response time (TTFB),
- real-user CrUX data under `field_data` when available (page-level
  preferred over origin-level; `source` reflects which one was used),
- the top 5 opportunities ranked by `savings_ms` then `savings_bytes`,
- the top 5 failing diagnostics (`score < 0.9`).

The HTTP client retries once on transient failures (`httpx` transport
errors, 429, and 5xx). Non-retryable 4xx (400/401/403) raises
immediately. Default timeout is 30s per attempt.

The audit is **best-effort and entirely optional**. When
`PAGE_SPEED_INSIGHTS_API` is unset, the call is skipped silently and
neither `performance` nor `performance_error` is written — this is a
configuration choice, not a failure. On any other failure (timeout,
exhausted retries, malformed response), `performance_error` is written
with the exception class, scrubbed message, and detector version, and
the request still completes as `scraped`.

Both `performance` and `performance_error` are surfaced as top-level
fields on the GET response via the same `AliasPath` pattern used for
marketing-stack.

### Token-budget fitting

When the concatenated page markdown fits the configured budget, the
extractor sends the pages in crawl order — no extra LLM call.

When it doesn't fit, a smaller "ranker" model orders the URLs by likely
info density; the extractor packs greedily from the top, truncates the
boundary page, and drops the rest. The ranker is best-effort: any failure
or hallucinated URL falls back to a deterministic order (root first, then
path-depth ascending). Both calls — ranker and extractor — are persisted
as `LLMCall` rows for cost attribution.

```mermaid
flowchart TD
    Start[ScrapingResults with markdown] --> Sum[Sum page-block tokens]
    Sum --> Fit{Total &le; budget?}
    Fit -- yes --> Pass[Send all pages in crawl order<br/>no extra LLM call]
    Fit -- no --> Rank[Call URL-ranker LLM]
    Rank --> Valid{Valid response?<br/>all URLs &isin; input}
    Valid -- no --> Fallback[Deterministic order:<br/>root first, then depth, then alpha]
    Valid -- yes --> Order[Use ranked order]
    Fallback --> Pack
    Order --> Pack[Greedy pack until budget hit]
    Pack --> Boundary[Truncate boundary page to fit remainder]
    Boundary --> Drop[Drop remaining pages]
    Pass --> Call[OpenAI responses.parse<br/>text_format = MarTechExtraction]
    Drop --> Call
    Call --> Out[Persist LLMOutput + LLMCall rows]
```

### Data model

```mermaid
erDiagram
    ApiUser ||--o{ ScrapingRequest : "submits"
    Client ||--o{ ScrapingRequest : "billed for"
    Website ||--o{ ScrapingRequest : "target of"
    ScrapingRequest ||--o{ ScrapingResult : "captured pages"
    ScrapingRequest ||--o{ LLMProcessingJob : "extraction jobs"
    LLMProcessingJob ||--|| LLMOutput : "parsed output"
    LLMProcessingJob ||--o{ LLMCall : "API calls (ranker + extractor)"

    ApiUser {
        uuid id PK
        string name
        string key_hash "sha256(deckard_live_*)"
    }
    Client {
        uuid id PK
        string identifier "stable per-tenant string"
    }
    Website {
        uuid id PK
        string host
    }
    ScrapingRequest {
        uuid id PK
        uuid api_user_id FK
        uuid client_id FK
        uuid website_id FK
        string status "pending|scraping|scraped|processing|completed|failed|cancelled"
        text requested_url
        string idempotency_key "unique per api_user"
        int attempt_count
    }
    ScrapingResult {
        uuid id PK
        uuid scraping_request_id FK
        text url
        text markdown
        int tokens
        bool success
    }
    LLMProcessingJob {
        uuid id PK
        uuid scraping_request_id FK
        string status
        bool ranker_used "null when budget already fit"
    }
    LLMOutput {
        uuid id PK
        uuid llm_processing_job_id FK
        jsonb output "MarTechExtraction"
    }
    LLMCall {
        uuid id PK
        uuid llm_processing_job_id FK
        string call_type "ranker | extractor"
        string model
        int input_tokens
        int output_tokens
    }
```

`ApiUser` (the caller) and `Client` (the billed tenant) are deliberately
orthogonal — there is no foreign key between them. One ApiUser submits
requests for many Clients; ownership of a `ScrapingRequest` is scoped to
the `api_user_id`, so two ApiUsers using the same `client_identifier`
each see only their own requests.

---

## Requirements

- Python 3.14+
- PostgreSQL 14+
- [uv](https://docs.astral.sh/uv/) for dependency management
- An OpenAI API key (for the extraction and ranker calls)

## Setup

```bash
uv sync
```

Create a `.env` file:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/deckard
OPENAI_API_KEY=sk-...
PAGE_SPEED_INSIGHTS_API=AIza...  # optional; omit to skip the perf audit
LOG_LEVEL=DEBUG
DEBUG=true
```

## Database migrations

Migrations are managed with [Alembic](https://alembic.sqlalchemy.org/) and
run against an async SQLAlchemy engine (`asyncpg`). The migration
environment reads `DATABASE_URL` from settings, so make sure `.env` is
configured before running any migration command.

```bash
# Apply
uv run alembic upgrade head

# Roll back
uv run alembic downgrade base

# Step by one
uv run alembic upgrade +1
uv run alembic downgrade -1

# Inspect
uv run alembic history
uv run alembic current
```

After editing models in `deckard/database/models/`, autogenerate a
revision:

```bash
uv run alembic revision --autogenerate -m "describe the change"
```

Always review the generated file under `alembic/versions/` before
committing — autogeneration does not detect every kind of change
(CHECK constraint edits, index ordering, server-side defaults) and may
need manual adjustment.

## Running the API

```bash
uv run uvicorn deckard.app:app --reload
```

OpenAPI docs are served at `http://localhost:8000/docs`.

## Authentication

All `/scraping-requests` endpoints require an API key sent as a bearer
token:

```
Authorization: Bearer deckard_live_<random>
```

`/status` is intentionally public for health checks.

### Provisioning an ApiUser

Administrative scripts live in the top-level `cli/` package — one script
per command:

```bash
uv run python -m cli.create_api_user --name "acme-prod-integration"
```

The command prints the plaintext key once — only its SHA-256 hash is
stored. Capture the key at creation time; there is no way to recover it
later.

### Rotating a key

Run `create_api_user` again to mint a fresh ApiUser + key, switch the
caller over, then delete the old ApiUser row. Multi-active-key rotation
on a single ApiUser will be added when needed.

## Calling the API

Submit a request:

```bash
curl -X POST http://localhost:8000/scraping-requests \
  -H "Authorization: Bearer deckard_live_<your-key>" \
  -H "Content-Type: application/json" \
  -d '{
        "client_identifier": "acme-12345",
        "url": "https://example.com",
        "idempotency_key": "run-2026-05-18-001"
      }'
```

Response (201):

```json
{
  "id": "5f1d…",
  "status": "pending",
  "requested_at": "2026-05-18T09:30:00Z"
}
```

Poll the result:

```bash
curl http://localhost:8000/scraping-requests/<id> \
  -H "Authorization: Bearer deckard_live_<your-key>"
```

When the request reaches `completed`, the GET response contains the
per-page `scraping_result` payloads, the parsed extraction under
`llm_processing_jobs[].output`, the marketing-stack detection report
under `marketing_stack` (or `marketing_stack_error` if detection failed),
and the PageSpeed Insights report under `performance` (or
`performance_error`, or null when no API key is configured).

Supplying the same `idempotency_key` from the same ApiUser returns the
original request unchanged — safe to retry.

## Tests

```bash
uv run pytest
```

Tests run against the database configured in `.env`. Each test executes
inside an outer transaction that is rolled back at teardown, so the
tests are isolated but assume a live Postgres reachable at
`DATABASE_URL`.
