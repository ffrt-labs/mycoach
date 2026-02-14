# MyCoach

AI-powered personal fitness coaching app. Ingests data from Garmin (biometrics, activities) and Hevy CSV exports (gym workouts), analyzes it with the Claude API, and delivers personalized training plans and coaching feedback via a PWA and email.

Built for a single technically-proficient user training across multiple sports (gym, swimming, padel).

## Features

- **Multi-source data ingestion** — Garmin Connect for health metrics (HRV, sleep, Body Battery, training readiness, VO2 max) and activities (swimming, padel); Hevy CSV for detailed gym data (exercises, weights, reps, RPE)
- **Weekly workout plans** — AI-generated training plans across gym, swimming, and padel based on availability, biometrics, performance history, and mesocycle position
- **Daily coaching feedback** — Morning briefing with sleep assessment, recovery status, training readiness verdict, and workout adjustments
- **Post-workout analysis** — Actual vs. planned comparison, HR zone analysis, training effect, and recommendations
- **Sleep coaching** — 14-day trend analysis, bedtime recommendations, and personalized sleep hygiene tips
- **Progressive programming** — Mesocycle tracking (4-6 week blocks), auto-progression, and fatigue management
- **PWA interface** — Mobile-first dashboard with HTMX for real-time interactivity
- **Email delivery** — Weekly plans, daily briefings, post-workout reports, and weekly recaps

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Web Framework | FastAPI + Uvicorn |
| Database | SQLite (async via aiosqlite) + SQLAlchemy 2.0 + Alembic |
| Garmin Integration | `garminconnect` + `garth` |
| Gym Data | Hevy CSV import |
| LLM | Anthropic Claude API (Sonnet for daily tasks, Opus for weekly plans) |
| Frontend | Jinja2 + HTMX + Tailwind CSS |
| Testing | pytest + pytest-asyncio + httpx |
| Quality | ruff (lint + format), mypy |
| Python | 3.11+ |

## Project Structure

```
src/mycoach/
  main.py             # FastAPI app factory with lifespan
  config.py           # Pydantic Settings (env-based config)
  database.py         # Async SQLAlchemy engine + session
  models/             # SQLAlchemy ORM models
  schemas/            # Pydantic DTOs
  sources/            # Data source plugin system
    base.py           #   Abstract DataSource interface
    garmin/           #   Garmin Connect integration
    hevy/             #   Hevy CSV import
  coaching/           # AI coaching engine
    engine.py         #   LLM call orchestration
    prompt_builder.py #   Context assembly for prompts
    response_parser.py#   JSON -> Pydantic validation
    sports/           #   Sport-specific logic
  api/
    routes/           #   REST API endpoints
    pages/            #   PWA page routes (Jinja2)
  prompts/            # Versioned prompt templates (v1/, v2/, ...)
  templates/          # Jinja2 HTML templates
  static/             # CSS, JS, PWA manifest
  email/              # Email sender + templates
  scheduler/          # APScheduler jobs
tests/                # pytest test suite
alembic/              # Database migrations
```

## Getting Started

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- A Garmin Connect account (for biometric data)
- An Anthropic API key (for AI coaching)

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/mycoach.git
cd mycoach

# Install dependencies
uv sync --dev

# Configure environment
cp .env.example .env
# Edit .env with your credentials (Garmin, Claude API key, etc.)

# Run database migrations
uv run alembic upgrade head
```

### Running

```bash
# Start the development server
uv run uvicorn src.mycoach.main:app --reload
```

The app will be available at `http://localhost:8000`.

### Importing Gym Data

Export your workouts from the Hevy app (Profile > Settings > Export Workouts), then upload the CSV via the PWA or the API:

```bash
curl -X POST http://localhost:8000/api/sources/hevy/import \
  -F "file=@hevy_export.csv"
```

## Development

```bash
# Run all tests
uv run pytest

# Run a specific test file
uv run pytest tests/test_api/test_system.py

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Type check
uv run mypy src/mycoach/

# Create a new database migration
uv run alembic revision --autogenerate -m "description"
```

## Configuration

All configuration is via environment variables (or a `.env` file). See `.env.example` for the full list. Key settings:

| Variable | Description |
|----------|-------------|
| `MYCOACH_DB_URL` | Database connection string |
| `MYCOACH_GARMIN_EMAIL` | Garmin Connect email |
| `MYCOACH_GARMIN_PASSWORD` | Garmin Connect password |
| `MYCOACH_CLAUDE_API_KEY` | Anthropic API key |
| `MYCOACH_CLAUDE_MODEL_DAILY` | Model for daily coaching (default: Sonnet) |
| `MYCOACH_CLAUDE_MODEL_WEEKLY` | Model for weekly plans (default: Opus) |
| `MYCOACH_CLAUDE_MONTHLY_COST_CEILING` | Monthly API spend limit (default: $30) |
| `MYCOACH_EMAIL_ENABLED` | Enable email delivery |

## Architecture Decisions

- **Hevy CSV over Hevy API** — CSV export is free; the API requires a Pro subscription
- **HTMX over React/Vue** — No separate frontend build; serves directly from FastAPI
- **SQLite for MVP** — Sufficient for single-user; SQLAlchemy ORM makes PostgreSQL migration a config change
- **APScheduler over Celery** — No broker needed for a few daily jobs in a single-user app
- **Dual LLM strategy** — Sonnet for cost-efficient daily tasks, Opus for high-quality weekly plan generation
- **Prompt versioning** — Templates in `v1/`, `v2/` directories; `PromptLog` records which version produced each output

## License

Private project. All rights reserved.
