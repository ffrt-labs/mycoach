# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output.
# Put the project venv on PATH so we invoke alembic/uvicorn directly (no `uv run`
# at runtime — keeps things simple under a non-root user).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_FROZEN=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 1. Install dependencies only (best layer caching). README is required because
#    pyproject sets `readme = "README.md"`, but --no-install-project skips
#    building the mycoach package itself here.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen --no-install-project

# 2. Copy application source, then install the project itself.
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY src/ ./src/
RUN uv sync --no-dev --frozen

# Persistent data (SQLite DB + Garmin tokens) lives under /data on a volume.
RUN mkdir -p /data /data/garmin_tokens

ENV MYCOACH_DB_URL=sqlite+aiosqlite:////data/mycoach.db \
    MYCOACH_GARMIN_TOKEN_DIR=/data/garmin_tokens

# Run as a non-root user that owns the app and data directories.
RUN useradd --create-home --uid 1000 app \
    && chown -R app:app /app /data
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/system/status', timeout=4).status == 200 else 1)"

# Apply migrations, then start the app.
CMD ["sh", "-c", "alembic upgrade head && uvicorn src.mycoach.main:app --host 0.0.0.0 --port 8000"]
