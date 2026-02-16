# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies first (layer caching)
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

# Copy application code
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY src/ ./src/

# Create data directory for SQLite DB and Garmin tokens
RUN mkdir -p /data /data/garmin_tokens

ENV MYCOACH_DB_URL=sqlite+aiosqlite:////data/mycoach.db \
    MYCOACH_GARMIN_TOKEN_DIR=/data/garmin_tokens

EXPOSE 8000

# Run Alembic migrations then start the app
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn src.mycoach.main:app --host 0.0.0.0 --port 8000"]
