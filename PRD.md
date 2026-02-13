# MyCoach — Product Requirements Document (PRD)

## Context

**Problem:** Personal trainers are expensive and not always available. Generic fitness apps lack deep personalization. Users who train across multiple sports (gym, swimming, padel) have no single system that understands all their training holistically, adapts to their biometric data (sleep, HRV, stress, recovery), and provides progressive programming.

**Solution:** MyCoach is an AI-powered personal fitness coaching app that fetches data from multiple sources (Garmin watch for health/biometrics, Hevy CSV exports for detailed gym workouts), analyzes it with Claude API, and delivers personalized weekly workout plans, daily coaching feedback, post-workout analysis, and sleep recommendations.

**Target user (MVP):** Single technically-proficient user training gym + swimming + padel, wearing a Garmin watch 24/7, logging gym workouts in Hevy (free tier).

**Long-term vision:** A sport-agnostic coaching platform where any user can add any sport, connect any wearable or fitness app (Garmin, Strava, Apple Health, Google Health, Oura Ring, Whoop), and receive professional-grade AI coaching.

---

## 1. Core Features (MVP)

### 1.1 Multi-Source Data Ingestion

**Architecture:** A plugin-based data source system with a common `DataSource` interface. Each source implements `authenticate()`, `fetch()`, and `map_to_domain()`. This allows adding new sources (Strava, Apple Health, Oura, etc.) without changing core logic.

**MVP Data Sources:**

**Garmin (health + biometrics + swim/padel activities):**
- Via `garminconnect` Python library + `garth` for auth
- **Data:** Heart rate & HRV, sleep (duration/stages/score), Body Battery & stress, training readiness/load/status, VO2 max, activities (swimming, padel, cardio), steps, respiration, SpO2, intensity minutes
- Scheduled daily fetch (default 6:00 AM) + manual trigger
- Robust re-auth on token expiration

**Hevy CSV Import (gym workout data):**
- Via Hevy's free CSV export (no Pro subscription required)
- **Workflow:** User exports CSV from Hevy app (Profile > Settings > Export Workouts), then uploads to MyCoach via PWA or drops in a watched folder
- **CSV columns:** title, start_time, end_time, exercise_title, superset_id, exercise_notes, set_index, set_type, weight_lbs, reps, distance_miles, duration_seconds, rpe
- **Import features:** Deduplication (skip already-imported workouts by date+title), incremental import (only process new rows), validation + error reporting
- **Key benefit:** Detailed gym data (exact weights, reps, RPE per set) that Garmin's strength tracking doesn't capture accurately. Zero subscription cost.

**Data merging strategy:** Garmin provides the biometric context (how the body responded — HR, calories, training effect), Hevy CSV provides the gym workout details (what was actually done — exercises, weights, reps). For gym sessions, Hevy data is the source of truth for exercise details; Garmin provides the HR/calorie overlay. The system matches gym activities by date/time overlap. For swimming and padel, Garmin is the sole source.

### 1.2 Weekly Workout Plan Generation
- User inputs weekly availability (day + time + duration + preferred sport)
- LLM generates a complete training plan considering: availability, last 7 days of biometric data, previous week's performance, current mesocycle position, injuries/constraints
- **Gym:** exercise name, sets, reps, RPE, rest, tempo, weight progression
- **Swimming:** drills, distances, intervals, stroke focus, pace targets
- **Padel:** drill types, match play, focus areas, partner requirements
- Delivered Sunday evening, accessible via PWA + email

### 1.3 Daily Coaching Feedback
- Generated each morning after Garmin sync
- Sleep quality assessment, recovery status (Body Battery, HRV, stress)
- Training readiness verdict: "Go hard" / "Moderate" / "Active recovery" / "Rest"
- Adjustments to today's planned workout based on current state
- Sleep recommendation for tonight

### 1.4 Post-Workout Analysis
- Triggered when new activities detected in Garmin data
- Actual vs planned comparison, HR zone analysis, training effect, performance trends
- Recommendations for next similar session

### 1.5 Sleep Coaching
- 14-day sleep trend analysis (consistency, architecture, correlation with performance)
- Recommended bedtime based on tomorrow's planned activity
- Personalized sleep hygiene tips

### 1.6 Progressive Programming
- Mesocycle tracking (4-6 week blocks with progressive overload + deload)
- Auto-progression when performance targets are met
- Sport balancing and fatigue management (acute:chronic load ratio)

### 1.7 PWA Interface (Mobile-First)
- **Dashboard:** Today's readiness, planned workout, health metrics
- **Weekly Plan:** Full plan view, expandable sessions
- **Availability Input:** Calendar/time picker for next week
- **Workout Detail, Post-Workout Report, History, Settings**
- Tech: Jinja2 + HTMX + Tailwind CSS + Service Worker

### 1.8 Email Delivery
- Weekly plan email (Sunday evening)
- Daily briefing email (each morning)
- Post-workout email (after analysis)
- Weekly recap email (end of week)

---

## 2. Tech Stack

| Layer | Technology |
|-------|-----------|
| Web Framework | FastAPI + Uvicorn |
| Database | SQLite (MVP) → PostgreSQL (future), via SQLAlchemy 2.0 + Alembic |
| Garmin | `garminconnect` + `garth` |
| Hevy | CSV import (free export from Hevy app) |
| LLM | Anthropic Python SDK (Claude API) |
| Scheduler | APScheduler |
| Frontend | Jinja2 + HTMX + Tailwind CSS |
| Email | Resend API or SMTP |
| Testing | pytest + pytest-asyncio + httpx |
| Quality | ruff (lint + format), mypy |
| Python | 3.11+ |

---

## 3. Data Model (Key Entities)

- **User** — profile, fitness level, goals
- **DataSourceConfig** — per-source config (source type: garmin/hevy_csv/strava/...), credentials (encrypted), enabled flag
- **SportProfile** — per-sport skill level, goals, preferences, benchmarks
- **WeeklyAvailability** — time slots for the upcoming week
- **DailyHealthSnapshot** — aggregated daily metrics from all health sources (HR, HRV, sleep, Body Battery, stress, training readiness)
- **Activity** — completed workouts with HR zones, training effect, details. Includes `data_source` field (garmin/hevy/merged)
- **GymWorkoutDetail** — from Hevy CSV: exercises, sets, reps, weight, RPE, supersets (linked to Activity via date/time matching)
- **WeeklyPlan** — generated plan with mesocycle tracking, prompt version
- **PlannedSession** — individual session within a plan (exercises, targets, notes)
- **CoachingInsight** — daily briefing, post-workout analysis, sleep coaching, weekly recap
- **MesocycleConfig** — training block tracking (phase, progression rules)
- **PromptLog** — all LLM calls logged (tokens, latency, prompt/response text)

---

## 4. Project Structure

```
mycoach/
├── pyproject.toml
├── alembic.ini
├── .env.example / .env
├── src/mycoach/
│   ├── main.py                    # FastAPI app factory
│   ├── config.py                  # Pydantic Settings
│   ├── database.py                # DB engine, sessions
│   ├── models/                    # SQLAlchemy ORM (user, health, activity, plan, coaching, mesocycle, prompt_log)
│   ├── schemas/                   # Pydantic DTOs
│   ├── sources/                   # Data source plugin system
│   │   ├── base.py               # Abstract DataSource interface
│   │   ├── garmin/               # client.py, auth.py, fetcher.py, mappers.py
│   │   └── hevy/                 # csv_parser.py, mappers.py (CSV import, no API needed)
│   ├── coaching/                  # engine.py, llm_client.py, prompt_builder.py, response_parser.py, context.py
│   │   └── sports/               # base.py, gym.py, swimming.py, padel.py
│   ├── api/routes/                # health, activities, plans, availability, coaching, profile, system
│   ├── api/pages/                 # PWA HTML page routes
│   ├── email/                     # sender.py + Jinja2 email templates
│   ├── scheduler/                 # scheduler.py, jobs.py
│   ├── prompts/v1/               # Versioned prompt templates + output schemas
│   ├── templates/                 # PWA Jinja2 templates + components
│   └── static/                   # CSS, JS (HTMX), Service Worker, manifest
├── alembic/                       # DB migrations
├── tests/                         # test_garmin/, test_coaching/, test_api/, test_scheduler/
└── scripts/                       # seed_data.py, test_garmin_connection.py
```

---

## 5. LLM Integration Strategy

- **Prompt architecture:** System prompt (coaching persona) + context assembly (data from DB) + structured output (JSON with Pydantic validation)
- **Model strategy:** Sonnet for daily briefings (cheaper/faster), Opus for weekly plan generation (better reasoning)
- **Token budget management:** Raw Garmin data summarized into compact representations before prompting. Rolling context windows (7 days for plans, 3 days for dailies)
- **Prompt versioning:** Templates in `prompts/v1/`, `v2/`, etc. PromptLog tracks which version produced each output.
- **Response validation:** JSON parsed → Pydantic validated → retry once on failure → fallback to text

**Estimated monthly cost:** ~$20-30/month for a single user

---

## 6. Development Phases

| Phase | Scope | Duration |
|-------|-------|----------|
| **0: Foundation** | Project scaffolding, FastAPI, SQLAlchemy, Alembic, config, health check | Week 1 |
| **1: Data Sources** | Garmin auth + fetch + mappers, Hevy CSV parser + import, data merging, manual sync endpoint | Week 2 |
| **2: Coaching Core** | LLM client, prompt builder, response parser, daily briefing | Week 3 |
| **3: Weekly Plans** | Availability input, mesocycle tracking, sport modules, plan generation | Week 4 |
| **4: Post-Workout** | Activity analysis, plan adherence tracking | Week 5 |
| **5: Automation** | Scheduler, sleep coaching, weekly recap, full daily pipeline | Week 5-6 |
| **6: PWA Frontend** | Dashboard, plan view, availability input, history, settings, service worker | Week 6-7 |
| **7: Email** | Email templates, send triggers, email preferences | Week 7-8 |
| **8: Polish** | Error handling, encryption, logging, testing, deployment | Week 8-9 |

---

## 7. API Endpoints (Key)

```
POST /api/sources/sync                    # Trigger sync from all sources (Garmin auto-fetch)
POST /api/sources/sync/garmin             # Trigger Garmin sync manually
POST /api/sources/import/hevy             # Upload Hevy CSV file for import
GET  /api/sources/status                  # Connection status of all data sources
GET  /api/health/today                    # Today's health snapshot
GET  /api/health/trends?days=30           # Health trends

GET  /api/activities                      # List activities
GET  /api/activities/{id}/analysis        # Post-workout analysis

POST /api/availability                    # Set weekly availability
POST /api/plans/generate                  # Generate weekly plan
GET  /api/plans/current                   # Current week's plan

GET  /api/coaching/today                  # Today's daily briefing
GET  /api/coaching/sleep                  # Sleep recommendations

GET  /api/system/status                   # Health check
```

---

## 8. Key Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Garmin blocks unofficial API | Store data locally once fetched; begin official API application early |
| Hevy CSV export format changes | Store raw CSV alongside parsed data; parser is simple to update if columns change |
| LLM hallucinations in workout plans | Safety guardrails in prompts, output validation (RPE ranges, reasonable reps/sets) |
| Auth token expiration | Robust re-auth via garth, alerting on failure |
| Claude API cost overruns | Token budget tracking, monthly ceiling, cheaper models for routine tasks |
| Scope creep | Strict phase-gating, each phase has a clear milestone |

---

## 9. Future Features (Post-MVP)

- Official Garmin Health API (OAuth2)
- Multi-user support + PostgreSQL migration
- Sport-agnostic plugin architecture (any sport)
- **Additional data source plugins:**
  - **Strava** — running, cycling, swimming activities with detailed metrics
  - **Apple Health** — aggregated health data from iPhone/Apple Watch
  - **Google Health Connect** — Android health data aggregation
  - **Oura Ring** — sleep, readiness, activity, HRV (complements Garmin)
  - **Whoop** — strain, recovery, sleep (alternative wearable)
- **Built-in gym workout logger** — replace Hevy CSV with a minimal PWA logger (exercise picker, sets/reps/weight input). Eliminates manual CSV export step.
- **wger integration** — alternative: deploy open-source wger (self-hosted) and connect via REST API
- Nutrition tracking integration
- Interactive AI chat for ad-hoc coaching questions
- Native push notifications
- Injury tracking
- User-selectable data sources (onboarding flow: "Which apps do you use?")

---

## 10. Verification Plan

1. **Phase 0:** `uvicorn src.mycoach.main:app` starts, `GET /api/system/status` returns 200
2. **Phase 1:** `POST /api/sources/sync/garmin` fetches real Garmin data; `POST /api/sources/import/hevy` imports a real Hevy CSV; verify both in SQLite
3. **Phase 2:** Generate a daily briefing from real data, inspect Claude output quality
4. **Phase 3:** Submit availability → receive structured weekly plan with sport-specific details
5. **Phase 4:** Complete a workout on Garmin → sync → verify post-workout analysis appears
6. **Phase 5:** Let system run for 7 days unattended, verify all automated jobs execute
7. **Phase 6:** Open PWA on mobile, navigate all screens, verify data renders correctly
8. **Phase 7:** Verify emails arrive in inbox with correct formatting
9. **Phase 8:** Run full test suite (`pytest`), verify >80% coverage on critical paths
