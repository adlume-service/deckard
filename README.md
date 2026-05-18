# Deckard

FastAPI backend for website scraping and LLM extraction.

## Requirements

- Python 3.14+
- PostgreSQL 14+
- [uv](https://docs.astral.sh/uv/) for dependency management

## Setup

```bash
uv sync
```

Create a `.env` file (or copy from below):

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/deckard
LOG_LEVEL=DEBUG
DEBUG=true
```

## Database migrations

Migrations are managed with [Alembic](https://alembic.sqlalchemy.org/) and run
against an async SQLAlchemy engine (`asyncpg`). The migration environment reads
`DATABASE_URL` from settings, so make sure `.env` is configured before running
any migration command.

### Apply all migrations

```bash
uv run alembic upgrade head
```

### Roll everything back

```bash
uv run alembic downgrade base
```

### Step up/down by one revision

```bash
uv run alembic upgrade +1
uv run alembic downgrade -1
```

### Create a new migration

After editing models in `deckard/database/models/`, autogenerate a revision:

```bash
uv run alembic revision --autogenerate -m "describe the change"
```

Always review the generated file under `alembic/versions/` before committing —
autogeneration does not detect every kind of change (e.g. CHECK constraint
edits, index ordering, server-side defaults) and may need manual adjustment.

### Inspect the migration history

```bash
uv run alembic history
uv run alembic current
```

## Running the API

```bash
uv run uvicorn deckard.app:app --reload
```

## Authentication

All `/scraping-requests` endpoints require an API key sent as a bearer token:

```
Authorization: Bearer deckard_live_<random>
```

`/status` is intentionally public for health checks.

### Identity model

- **ApiUser** — the calling server. Holds the API key. Standalone — no link
  to a Client.
- **Client** — a tenant whose data is being scraped. Tracked for billing.
  Identified by a stable string `identifier` that the caller supplies
  per-request. Created on first sight.

One ApiUser submits requests for many Clients. Clients are referenced
per-request via `client_identifier` in the POST body — they are NOT bound to
the API key. Ownership of a `ScrapingRequest` is scoped to the *ApiUser* that
submitted it: two ApiUsers using the same `client_identifier` can each see
their own requests but not each other's.

### Provisioning an ApiUser

Administrative scripts live in the top-level `cli/` package — one script per
command:

```bash
uv run python -m cli.create_api_user --name "acme-prod-integration"
```

The command prints the plaintext key once — only its SHA-256 hash is stored.
Capture the key at creation time; there is no way to recover it later.

### Calling the API

```bash
curl -X POST http://localhost:8000/scraping-requests \
  -H "Authorization: Bearer deckard_live_<your-key>" \
  -H "Content-Type: application/json" \
  -d '{"client_identifier": "acme-12345", "url": "https://example.com"}'
```

### Rotating a key

Run `create_api_user` again to mint a fresh ApiUser + key, switch the caller
over, then delete the old ApiUser row. Multi-active-key rotation on a single
ApiUser will be added when needed.

### Calling the API

```bash
curl -X POST http://localhost:8000/scraping-requests \
  -H "Authorization: Bearer deckard_live_<your-key>" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

## Tests

```bash
uv run pytest
```

Tests run against the database configured in `.env`. Each test executes inside
an outer transaction that is rolled back at teardown, so the tests are
isolated but assume a live Postgres reachable at `DATABASE_URL`.
