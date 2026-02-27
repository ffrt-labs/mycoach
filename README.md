# MyCoach

AI-powered personal fitness coaching app. Ingests data from Garmin (biometrics, activities) and Hevy CSV exports (gym workouts), analyzes it with the Claude API, and delivers personalized training plans and coaching feedback via a PWA and email.

Built for a single technically-proficient user training across multiple sports (gym, swimming, padel).

## Features

- **Multi-source data ingestion** — Garmin Connect for health metrics (HRV, sleep, Body Battery, training readiness, VO2 max) and activities (swimming, padel); Hevy CSV for detailed gym data (exercises, weights, reps, RPE)
- **Weekly workout plans** — AI-generated training plans across gym, swimming, and padel based on availability, biometrics, performance history, and mesocycle position. Gym and cardio days are automatically interleaved for optimal recovery spacing
- **Daily coaching feedback** — Morning briefing with sleep assessment, recovery status, training readiness verdict, and workout adjustments
- **Post-workout analysis** — Actual vs. planned comparison, HR zone analysis, training effect, and recommendations
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

## Scripts

### Fetch raw Garmin data

`scripts/fetch_garmin.py` lets you manually fetch the raw response from each Garmin API endpoint. Useful for debugging, inspecting data shape, or verifying that a metric is available for a given date.

```bash
# Fetch all endpoints for today
uv run python scripts/fetch_garmin.py

# Fetch for a specific date
uv run python scripts/fetch_garmin.py --date 2026-02-20

# Fetch only specific endpoints
uv run python scripts/fetch_garmin.py --only sleep hrv stats

# Fetch activities over a date range
uv run python scripts/fetch_garmin.py --date 2026-02-01 --end-date 2026-02-26 --only activities

# Save raw JSON output to a file
uv run python scripts/fetch_garmin.py --date 2026-02-20 --out garmin_raw.json
```

Available endpoints: `stats`, `heart_rates`, `hrv`, `sleep`, `stress`, `body_battery`, `training_readiness`, `training_status`, `max_metrics`, `respiration`, `spo2`, `activities`.

Requires saved Garmin tokens in `.garmin_tokens/` (created automatically on first app run).

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

## Data Pipeline

MyCoach collects data from two sources, stores it in a normalized database, then assembles relevant subsets into LLM prompts for each coaching feature.

```
Garmin API / Hevy CSV → Mappers → DB Models → Context Queries → Serialization → Formatting → Prompt Template → LLM
```

### Data Sources

**Garmin Connect** (daily sync via `garminconnect` + `garth`):
Docs: https://github.com/matin/garth?tab=readme-ov-file

| API Call | Metrics Collected |
|----------|-------------------|
| `get_stats()` | Resting HR, max HR, avg HR, steps, intensity minutes |
| `get_hrv_data()` | HRV status, 7-day HRV average |
| `get_sleep_data()` | Sleep duration, score, deep/light/REM/awake minutes |
| `get_body_battery()` | Body Battery high/low |
| `get_stress_data()` | Average stress level |
| `get_training_readiness()` | Training readiness score |
| `get_training_status()` | Training load, training status classification |
| `get_max_metrics()` | VO2 max |
| `get_respiration_data()` | Average respiration rate |
| `get_spo2_data()` | Average SpO2 |
| `get_activities_by_date()` | Activities with HR zones, training effects, duration, calories |

**Hevy CSV Export** (manual upload, free tier):

| CSV Column | Stored As |
|------------|-----------|
| `title`, `start_time`, `end_time` | Activity metadata |
| `exercise_title`, `set_index`, `set_type` | GymWorkoutDetail (one row per set) |
| `weight_lbs` / `weight_kg` | `weight_kg` (auto-converted) |
| `reps`, `rpe` | Per-set metrics |
| `distance_miles` / `distance_km` | `distance_meters` (auto-converted) |
| `duration_seconds` | Set duration |
| `superset_id`, `exercise_notes` | Grouping and annotations |

### Database Models

**DailyHealthSnapshot** — one record per day, 23 fields:
- Heart: `resting_hr`, `max_hr`, `avg_hr`, `hrv_status`, `hrv_7day_avg`
- Sleep: `sleep_duration_minutes`, `sleep_score`, `sleep_deep_minutes`, `sleep_light_minutes`, `sleep_rem_minutes`, `sleep_awake_minutes`
- Recovery: `body_battery_high`, `body_battery_low`, `avg_stress`
- Training: `training_readiness`, `training_load`, `training_status`, `vo2_max`
- Other: `steps`, `respiration_avg`, `spo2_avg`, `intensity_minutes`
- Debug: `raw_data` (full JSON blob, never sent to LLM)

**Activity** — one record per workout, 12 fields:
- Metadata: `sport`, `title`, `start_time`, `end_time`, `duration_minutes`
- HR: `avg_hr`, `max_hr`
- Effort: `calories`, `training_effect_aerobic`, `training_effect_anaerobic`
- Detail: `hr_zones` (JSON), `data_source` (garmin/hevy/merged)

**GymWorkoutDetail** — one record per set, linked to Activity:
- `exercise_title`, `set_index`, `set_type` (normal/warmup/dropset/failure)
- `weight_kg`, `reps`, `rpe`, `distance_meters`, `duration_seconds`
- `superset_id`, `exercise_notes`

**WeeklyPlan / PlannedSession** — AI-generated training plans:
- Plan: `week_start`, `mesocycle_week`, `mesocycle_phase`, `summary`
- Session: `day_of_week`, `sport`, `title`, `duration_minutes`, `details`, `track`, `notes`, `completed`

**SportProfile** — per-sport configuration: `sport`, `skill_level`, `goals`, `preferences`, `benchmarks`

**WorkoutRoutine / RoutineDay / RoutineExercise** — gym routine templates:
- Routine: `name`, `is_active`
- Day: `name`, `day_of_week` (optional — when null, the engine dynamically assigns days via interleaving), `order_index`
- Exercise: `exercise_name`, `sets`, `rep_range`, `notes`

**CardioGoal** — per-sport targets: `sport`, `weekly_target`, `fitness_goal`

> **SportProfile vs CardioGoal:** SportProfile is a broad capability/aptitude profile for all 4 sports (gym, swimming, padel, cardio) — storing skill level, preferences, and benchmarks that give the coaching LLM context about the user's ability. CardioGoal is a narrow, goal-focused target for cardio sports only (swimming, running) — storing concrete weekly training volume and fitness objectives. They are independent tables; a user can have both a swimming sport profile and a swimming cardio goal managing different aspects of coaching context.

### LLM-Powered Features Overview

5 features make LLM calls across 7 prompt types. All use v2 prompt templates, include 1 automatic retry on parse failure, and skip if already generated for the date/week (idempotency).

| Feature | Prompt Type(s) | Model | Trigger | Email |
|---------|---------------|-------|---------|-------|
| **Daily Briefing** | `daily_briefing` | Sonnet | Scheduled 6:30 AM + `POST /api/coaching/today/generate` | Yes |
| **Sleep Coaching** | `sleep` | Sonnet | Scheduled 6:30 AM + `POST /api/coaching/sleep/generate` | Yes |
| **Post-Workout Analysis** | `post_workout` | Sonnet | Manual: `POST /api/activities/{id}/analyze` | No |
| **Weekly Plan** | `gym_adjustment` (per gym day) + `cardio_plan` (all cardio slots) | Sonnet (gym) / Opus (cardio) | Scheduled Sunday 6 PM + `POST /api/plans/generate` | Yes |
| **Weekly Recap** | `weekly_recap` | Sonnet | Scheduled Monday 7 AM + `POST /api/coaching/weekly-recap/generate` | Yes |

**Weekly Plan** is a compound feature — it makes one `gym_adjustment` LLM call per gym day in the active routine, plus one `cardio_plan` call covering all remaining cardio slots.

#### Weekly Plan generation flow (`POST /api/plans/generate`)

The UI Regenerate button calls `POST /api/plans/generate?week_start=<YYYY-MM-DD>&force=true`. With `force=true`, the existing active plan is marked `"replaced"` instead of returning 409. The endpoint then:

1. Validates `week_start` is a Monday
2. Fetches shared context: availability slots, 7-day health trends, mesocycle context, active routine, cardio goals, sport profiles
3. Interleaves gym days across availability slots for optimal spacing (e.g. 5 slots + 2 gym days → Cardio, **Gym**, Cardio, **Gym**, Cardio), then assigns remaining slots to cardio
4. Creates a `WeeklyPlan` record and flushes to get an ID
5. **Gym track** — for each gym slot: fetches last week's per-exercise performance → calls LLM (`daily_model`, `GymAdjustmentResponse`) → saves `PlannedSession` with adjusted weights/RPE; falls back to raw routine on LLM failure
6. **Cardio track** — single call for all cardio slots: fetches last week's cardio performance → calls LLM (`weekly_model`, max 8192 tokens, `CardioPlanResponse`) → saves one `PlannedSession` per session; falls back to placeholder sessions on LLM failure
7. Each LLM call is logged to `PromptLog`
8. Finalizes plan with `summary` and `raw_llm_output`, commits, returns full plan with sessions

Total LLM calls per generation: **N + 1** (one per gym day + one for all cardio slots).

### What Each Feature Sends to the LLM

| Feature | Health Data | Activities | Plan/Routine | Profile | Time Window |
|---------|-------------|------------|--------------|---------|-------------|
| **Daily Briefing** | Today + 3-day trends (21 fields each) | Last 3 days | Today's planned sessions | Sport profiles | 3 days |
| **Weekly Plan** | 7-day trends | Last 14 days | Mesocycle context | — | 14 days |
| **Post-Workout** | Today's snapshot | Target activity (full detail + gym sets) + 5 similar | Matching planned session | — | Varies |
| **Sleep Coaching** | 14-day sleep trends (sleep + recovery fields) | Last 3 days | Tomorrow's planned session | — | 14 days |
| **Weekly Recap** | 7-day trends | Week's activities | Plan adherence + 4-week history + routine | — | 7 days + 4-week history |
| **Gym Adjustment** | 7-day trends | Last week's gym sets (per exercise) | Routine day exercises | Sport profiles + mesocycle | 7 days |
| **Cardio Plan** | 7-day trends | Last week's cardio activities | Cardio goals + availability | Sport profiles + mesocycle | 7 days |

**Health data formatting** — 21 of 23 DailyHealthSnapshot fields sent (excludes `raw_data` and internal IDs). Formatted with human-readable labels.

**Activity formatting** — Two modes:
- *List context* (daily briefing, recap, etc.): compact format with `title`, `sport`, `start_time`, `duration_minutes` only
- *Detail context* (post-workout): all 12 fields with labels + full gym set breakdown

**Prompt templates** are versioned in `prompts/v1/` and `prompts/v2/`. Each template receives pre-formatted markdown sections assembled by `prompt_builder.py`.

## Architecture Decisions

- **Hevy CSV over Hevy API** — CSV export is free; the API requires a Pro subscription
- **HTMX over React/Vue** — No separate frontend build; serves directly from FastAPI
- **SQLite for MVP** — Sufficient for single-user; SQLAlchemy ORM makes PostgreSQL migration a config change
- **APScheduler over Celery** — No broker needed for a few daily jobs in a single-user app
- **Dual LLM strategy** — Sonnet for cost-efficient daily tasks, Opus for high-quality weekly plan generation
- **Prompt versioning** — Templates in `v1/`, `v2/` directories; `PromptLog` records which version produced each output

## License

Private project. All rights reserved.
