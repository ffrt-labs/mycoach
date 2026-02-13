# MyCoach — Progress Tracker

> This file tracks what has been completed, when, and any notes/decisions made along the way.
> PRD.md defines the end state. TODO.md lists all tasks. This file records actual progress.

---

## Current Phase: 0 — Foundation

**Status:** In progress

---

## Completed Work

- **Phase 0, Task 1:** Initialize project with pyproject.toml using uv

---

## Progress Log

### 2026-02-13 — Phase 0: Project Scaffolding (Task 1)

- [x] Installed `uv` package manager
- [x] Initialized project with `uv init --lib` (src layout, Python 3.11)
- [x] Defined `pyproject.toml` with all MVP dependencies (fastapi, uvicorn, sqlalchemy, aiosqlite, alembic, pydantic, pydantic-settings, jinja2, python-multipart)
- [x] Defined dev dependencies (pytest, pytest-asyncio, httpx, ruff, mypy)
- [x] Added commented future dependencies (garminconnect, garth, anthropic, apscheduler, resend)
- [x] Created full directory structure per PRD Section 4 (`src/mycoach/` with models, schemas, sources, coaching, api, email, scheduler, prompts, templates, static + tests/ + scripts/ + alembic/)
- [x] Configured ruff, pytest, and mypy in `pyproject.toml`
- [x] Created `.env.example` with all env var placeholders
- [x] Created `.gitignore`
- [x] Verified: `uv run python -c "import mycoach"` succeeds

### 2026-02-13 — Project Planning

- [x] Defined PRD with full requirements, architecture, and phased plan
- [x] Created TODO.md with granular task breakdown across all 9 phases
- [x] Created PROGRESS.md for tracking

**Key decisions made during planning:**
- **Tech stack:** Python-only MVP (FastAPI + SQLAlchemy + SQLite)
- **Garmin data:** `garminconnect` unofficial library (official API as post-MVP)
- **Gym data:** Hevy CSV import (free, no subscription). Built-in gym logger deferred to post-MVP.
- **LLM:** Claude API via Anthropic SDK. Sonnet for daily tasks, Opus for weekly plan generation.
- **Frontend:** Jinja2 + HTMX + Tailwind CSS (no JS framework). PWA with service worker.
- **Database:** SQLite for MVP, SQLAlchemy ORM allows migration to PostgreSQL later.
- **Email:** Resend or SMTP for plan/briefing delivery.
- **No paid subscriptions** required beyond Claude API (~$20-30/month).

---

## Phase Summary

| Phase | Name | Status | Started | Completed |
|-------|------|--------|---------|-----------|
| 0 | Foundation | Not started | — | — |
| 1 | Data Sources | Not started | — | — |
| 2 | Coaching Core | Not started | — | — |
| 3 | Weekly Plans | Not started | — | — |
| 4 | Post-Workout | Not started | — | — |
| 5 | Automation | Not started | — | — |
| 6 | PWA Frontend | Not started | — | — |
| 7 | Email | Not started | — | — |
| 8 | Polish | Not started | — | — |

---

## Blockers & Open Questions

_None currently._

---

## Architecture Decisions Record

| # | Decision | Rationale | Date |
|---|----------|-----------|------|
| 1 | Python-only MVP | Best Garmin library support, rich data science ecosystem, fastest path to working product | 2026-02-13 |
| 2 | Hevy CSV import over Hevy API | Hevy API requires Pro subscription ($). CSV export is free and covers all needed data. | 2026-02-13 |
| 3 | HTMX over React/Vue | Eliminates separate frontend build, serves directly from FastAPI, simpler for single-developer MVP | 2026-02-13 |
| 4 | SQLite → PostgreSQL via SQLAlchemy | SQLite sufficient for single-user; SQLAlchemy ORM makes migration a config change, not a rewrite | 2026-02-13 |
| 5 | APScheduler over Celery | No need for Redis/RabbitMQ broker for a few daily jobs in a single-user app | 2026-02-13 |
| 6 | Dual LLM model strategy | Sonnet for routine daily tasks (cost), Opus for weekly plan generation (quality) | 2026-02-13 |
| 7 | Prompt versioning via filesystem | Templates in v1/, v2/ dirs — easy to edit, diff, track in git. PromptLog records version used. | 2026-02-13 |
