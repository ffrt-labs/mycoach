# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MyCoach is an AI-powered personal fitness coaching app. Single-user MVP that ingests data from Garmin (biometrics/activities) and Hevy CSV exports (gym workouts), analyzes with Claude API, and delivers personalized training plans and coaching feedback via a PWA and email.

## Commands

```bash
# Install dependencies
uv sync --dev

# Run the app
uv run uvicorn src.mycoach.main:app --reload

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_api/test_system.py

# Run a single test
uv run pytest tests/test_api/test_system.py::test_health_check -v

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Type check
uv run mypy src/mycoach/

# Alembic migrations
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"
```

## Architecture

- **Stack:** FastAPI + SQLAlchemy 2.0 (async) + SQLite (aiosqlite) + Pydantic v2 + Jinja2/HTMX frontend
- **Package layout:** `src/mycoach/` (PEP 621 src layout, managed by uv)
- **Async throughout:** async SQLAlchemy engine, async FastAPI endpoints
- **Data source plugin system:** `sources/base.py` defines abstract `DataSource` interface; each source (garmin/, hevy/) implements `authenticate()`, `fetch()`, `map_to_domain()`
- **Coaching engine:** `coaching/engine.py` orchestrates LLM calls. Uses `prompt_builder.py` for context assembly, `response_parser.py` for JSON→Pydantic validation. Sport-specific logic in `coaching/sports/`
- **Dual LLM strategy:** Sonnet for daily tasks (cost), Opus for weekly plan generation (quality)
- **Prompt versioning:** Templates in `prompts/v1/`, `v2/`, etc. `PromptLog` model tracks which version produced each output

## Key Conventions

- Python 3.11+, line length 100
- All database models in `models/`, Pydantic DTOs in `schemas/`
- API routes in `api/routes/`, PWA page routes in `api/pages/`
- Config via pydantic-settings, loaded from environment / `.env` file
- Gym data: Hevy CSV is source of truth for exercises/weights/reps; Garmin provides HR/calorie overlay
- Swimming/padel: Garmin is sole data source

## Reference Documents

- `PRD.md` — full product requirements, data model, API endpoints, LLM strategy
- `TODO.md` — granular task breakdown across all 9 phases
- `PROGRESS.md` — tracks completed work and architectural decisions

## Plan Mode

- Make the plan extremely concise. Sacrifice grammar for the sake of concision.
- At the end of each plan, give me a list of unresolved questions to answer, if any.
