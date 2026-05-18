# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Builder: resolve Python dependencies into a venv with uv
# -----------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Build deps for C extensions without Py3.14 wheels yet (e.g. lxml).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev


# -----------------------------------------------------------------------------
# Runtime: slim Python + Chromium (Playwright) + non-root user
# -----------------------------------------------------------------------------
FROM python:3.14-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY --from=builder /app /app

# tini for PID 1 (reaps Chromium subprocesses); Chromium system libs via playwright --with-deps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && playwright install --with-deps chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /home/app --create-home app \
    && chown -R app:app /app /ms-playwright

USER app

STOPSIGNAL SIGINT
ENTRYPOINT ["/usr/bin/tini", "--"]
