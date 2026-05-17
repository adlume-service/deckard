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
