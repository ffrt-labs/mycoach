# MyCoach — Development TODO

## Phase 0: Foundation (Week 1)

- [ ] Initialize project with `pyproject.toml` using `uv`
  - [ ] Define dependencies: fastapi, uvicorn, sqlalchemy, alembic, pydantic, pydantic-settings
  - [ ] Define dev dependencies: pytest, pytest-asyncio, httpx, ruff, mypy
  - [ ] Define future dependencies (commented): garminconnect, garth, anthropic, apscheduler, jinja2
  - [ ] Set up `src/mycoach/` package layout
- [ ] Create full directory structure per PRD Section 4
  - [ ] `src/mycoach/` — main.py, config.py, database.py
  - [ ] `src/mycoach/models/` — __init__.py + all model files
  - [ ] `src/mycoach/schemas/` — __init__.py
  - [ ] `src/mycoach/sources/` — base.py, garmin/, hevy/
  - [ ] `src/mycoach/coaching/` — engine.py, llm_client.py, sports/
  - [ ] `src/mycoach/api/` — routes/, pages/, deps.py
  - [ ] `src/mycoach/email/` — sender.py, templates/
  - [ ] `src/mycoach/scheduler/` — scheduler.py, jobs.py
  - [ ] `src/mycoach/prompts/v1/` — prompt template files
  - [ ] `src/mycoach/templates/` — Jinja2 HTML templates
  - [ ] `src/mycoach/static/` — CSS, JS, manifest
  - [ ] `tests/` — conftest.py + test directories
  - [ ] `scripts/` — utility scripts
  - [ ] `alembic/` — migration directory
- [ ] Implement `config.py` with Pydantic Settings
  - [ ] App settings (env, api_token, log_level)
  - [ ] Database settings (db_url)
  - [ ] Garmin settings (email, password, token_dir)
  - [ ] Claude API settings (api_key, model names, monthly cost ceiling)
  - [ ] Email settings (enabled, from, to, SMTP config)
  - [ ] Scheduler settings (sync hour, briefing hour, weekly plan day/hour)
- [ ] Implement `database.py`
  - [ ] SQLAlchemy 2.0 async engine + session factory
  - [ ] Base model class
  - [ ] Session dependency for FastAPI
- [ ] Implement all SQLAlchemy ORM models
  - [ ] `models/user.py` — User, SportProfile
  - [ ] `models/data_source.py` — DataSourceConfig
  - [ ] `models/health.py` — DailyHealthSnapshot
  - [ ] `models/activity.py` — Activity, GymWorkoutDetail
  - [ ] `models/plan.py` — WeeklyPlan, PlannedSession
  - [ ] `models/availability.py` — WeeklyAvailability
  - [ ] `models/coaching.py` — CoachingInsight
  - [ ] `models/mesocycle.py` — MesocycleConfig
  - [ ] `models/prompt_log.py` — PromptLog
- [ ] Implement `main.py` — FastAPI app factory with lifespan
  - [ ] Database table creation on startup
  - [ ] Include API routers
  - [ ] CORS middleware
- [ ] Set up Alembic
  - [ ] `alembic init alembic`
  - [ ] Configure `alembic/env.py` for SQLAlchemy models
  - [ ] Generate initial migration
  - [ ] Run migration
- [ ] Create API route stubs
  - [ ] `api/routes/system.py` — GET /api/system/status (health check)
  - [ ] `api/routes/health.py` — stubs
  - [ ] `api/routes/activities.py` — stubs
  - [ ] `api/routes/plans.py` — stubs
  - [ ] `api/routes/availability.py` — stubs
  - [ ] `api/routes/coaching.py` — stubs
  - [ ] `api/routes/profile.py` — stubs
  - [ ] `api/routes/sources.py` — stubs
- [ ] Create `.env.example` with all env var placeholders
- [ ] Set up ruff config in `pyproject.toml`
- [ ] Set up pytest config in `pyproject.toml`
- [ ] Create `tests/conftest.py` with test DB fixture
- [ ] **VERIFY:** `uvicorn src.mycoach.main:app` starts, `GET /api/system/status` returns 200

---

## Phase 1: Data Sources (Week 2)

- [ ] Implement `sources/base.py` — abstract DataSource interface
- [ ] Implement Garmin source
  - [ ] `sources/garmin/auth.py` — garth-based authentication, token persistence, re-auth
  - [ ] `sources/garmin/client.py` — garminconnect wrapper with all needed API methods
  - [ ] `sources/garmin/fetcher.py` — orchestrate daily fetch (health + activities)
  - [ ] `sources/garmin/mappers.py` — raw Garmin JSON → DailyHealthSnapshot + Activity models
  - [ ] Handle auth failures gracefully (retry, alert)
- [ ] Implement Hevy CSV source
  - [ ] `sources/hevy/csv_parser.py` — parse Hevy CSV export format
  - [ ] `sources/hevy/mappers.py` — CSV rows → GymWorkoutDetail + Activity models
  - [ ] Deduplication logic (skip already-imported workouts by date+title)
  - [ ] Validation + error reporting for malformed CSV rows
- [ ] Implement data merging logic
  - [ ] Match gym activities between Garmin and Hevy by date/time overlap
  - [ ] Create merged Activity records (Hevy exercises + Garmin HR/calories)
- [ ] Implement API endpoints
  - [ ] `POST /api/sources/sync` — trigger all sources
  - [ ] `POST /api/sources/sync/garmin` — trigger Garmin sync
  - [ ] `POST /api/sources/import/hevy` — upload + import Hevy CSV
  - [ ] `GET /api/sources/status` — connection status
  - [ ] `GET /api/health/today` — today's health snapshot
  - [ ] `GET /api/health/{date}` — specific date
  - [ ] `GET /api/health/trends?days=30` — trends
  - [ ] `GET /api/activities` — list activities (paginated)
  - [ ] `GET /api/activities/{id}` — activity detail
- [ ] Write tests with mock Garmin responses and sample Hevy CSV
- [ ] Create `scripts/test_garmin_connection.py` — standalone auth test
- [ ] **VERIFY:** Garmin sync stores real data in SQLite; Hevy CSV imports correctly

---

## Phase 2: Coaching Core (Week 3)

- [ ] Implement `coaching/llm_client.py` — Anthropic SDK wrapper
  - [ ] API call with retries and error handling
  - [ ] Model selection (sonnet vs opus based on prompt type)
  - [ ] Token usage tracking
- [ ] Implement `coaching/prompt_builder.py`
  - [ ] Context assembly from DB (health data, activities, profile)
  - [ ] Template rendering with variable substitution
  - [ ] Token budget management (data summarization)
- [ ] Implement `coaching/response_parser.py`
  - [ ] JSON response parsing
  - [ ] Pydantic model validation per prompt type
  - [ ] Retry on validation failure
  - [ ] Fallback to text response
- [ ] Implement `coaching/context.py` — DB queries for prompt context
- [ ] Write prompt templates
  - [ ] `prompts/v1/system_base.txt` — coaching persona
  - [ ] `prompts/v1/daily_briefing.txt` — daily coaching template
- [ ] Implement `coaching/engine.py` — central orchestrator
  - [ ] `generate_daily_briefing()` method
  - [ ] Store results as CoachingInsight
  - [ ] Log to PromptLog
- [ ] Implement API endpoints
  - [ ] `GET /api/coaching/today` — today's daily briefing
  - [ ] `GET /api/coaching/{date}` — specific date
  - [ ] `POST /api/coaching/generate/daily_briefing` — manual trigger
- [ ] Write tests with mocked Claude API responses
- [ ] **VERIFY:** Generate a real daily briefing from actual Garmin data

---

## Phase 3: Weekly Plans (Week 4)

- [ ] Implement availability management
  - [ ] `POST /api/availability` — set weekly availability
  - [ ] `GET /api/availability/next-week` — get next week's slots
  - [ ] `PUT /api/availability/{id}` — update
- [ ] Implement sport-specific coaching modules
  - [ ] `coaching/sports/base.py` — abstract BaseSportCoach
  - [ ] `coaching/sports/gym.py` — exercise DB, progression rules, RPE guidance
  - [ ] `coaching/sports/swimming.py` — drill types, distances, intervals
  - [ ] `coaching/sports/padel.py` — drill types, match play, focus areas
- [ ] Implement mesocycle tracking
  - [ ] Create/update MesocycleConfig
  - [ ] Track current week within mesocycle
  - [ ] Deload recommendation logic
- [ ] Write prompt template
  - [ ] `prompts/v1/weekly_plan.txt` — weekly plan generation
  - [ ] Define JSON output schema for structured plans
- [ ] Implement weekly plan generation in coaching engine
  - [ ] `generate_weekly_plan(availability)` method
  - [ ] Store as WeeklyPlan + PlannedSession records
- [ ] Implement API endpoints
  - [ ] `POST /api/plans/generate` — generate weekly plan
  - [ ] `GET /api/plans/current` — current week's plan
  - [ ] `GET /api/plans/{id}` — specific plan
  - [ ] `GET /api/plans/{id}/sessions` — sessions list
  - [ ] `GET /api/plans/{id}/sessions/{sid}` — session detail
- [ ] Write tests
- [ ] **VERIFY:** Submit availability → receive structured weekly plan with sport-specific details

---

## Phase 4: Post-Workout Analysis (Week 5)

- [ ] Write prompt template
  - [ ] `prompts/v1/post_workout.txt` — post-workout analysis
- [ ] Implement activity detection (new activities since last sync)
- [ ] Implement activity-to-planned-session linking
  - [ ] Match by date + sport type + time overlap
  - [ ] Update PlannedSession.completed + linked_activity_id
- [ ] Implement post-workout analysis in coaching engine
  - [ ] `generate_post_workout_analysis(activity)` method
  - [ ] Actual vs planned comparison
  - [ ] Performance trend analysis (last 5 similar activities)
  - [ ] Store as CoachingInsight
- [ ] Implement plan adherence tracking
  - [ ] Calculate weekly adherence % (completed / planned sessions)
- [ ] Implement API endpoints
  - [ ] `GET /api/activities/{id}/analysis` — post-workout analysis
  - [ ] `POST /api/activities/{id}/analyze` — trigger analysis
  - [ ] `PATCH /api/plans/{id}/sessions/{sid}` — mark completed
- [ ] Write tests
- [ ] **VERIFY:** Complete workout on Garmin → sync → post-workout analysis appears

---

## Phase 5: Automation (Week 5-6)

- [ ] Implement `scheduler/scheduler.py` — APScheduler setup
  - [ ] In-process scheduler with configurable timezone
  - [ ] Job persistence (survive restarts)
- [ ] Implement `scheduler/jobs.py` — job definitions
  - [ ] Daily Garmin sync job (configurable time, default 6:00 AM)
  - [ ] Daily briefing generation (after sync, default 6:30 AM)
  - [ ] Post-workout analysis (triggered after each sync if new activities)
  - [ ] Weekly plan reminder (Sunday evening)
  - [ ] Weekly recap generation (Sunday)
- [ ] Implement weekly recap
  - [ ] `prompts/v1/weekly_recap.txt` — recap template
  - [ ] `generate_weekly_recap()` in coaching engine
  - [ ] `GET /api/coaching/weekly-recap` endpoint
- [ ] Implement system monitoring endpoints
  - [ ] `GET /api/system/scheduler` — scheduler status, next run times
  - [ ] `POST /api/system/scheduler/trigger/{job}` — manual trigger
- [ ] Handle edge cases: missed runs, overlapping jobs, error recovery
- [ ] Write tests
- [ ] **VERIFY:** Let system run for 7 days unattended, all automated jobs execute

---

## Phase 6: PWA Frontend (Week 6-7)

- [ ] Set up Jinja2 template rendering in FastAPI
- [ ] Create base layout (`templates/base.html`)
  - [ ] Tailwind CSS (CDN)
  - [ ] HTMX (CDN)
  - [ ] Mobile-first responsive design
  - [ ] Navigation component
- [ ] Build pages
  - [ ] Dashboard — readiness score, today's workout, Body Battery, sleep summary
  - [ ] Weekly Plan — full plan view, tap into sessions
  - [ ] Availability Input — calendar/time picker for next week
  - [ ] Workout Detail — full workout prescription
  - [ ] Post-Workout Report — analysis of completed sessions
  - [ ] History — past weeks' plans and reports
  - [ ] Settings — Garmin status, Hevy CSV upload, preferences
- [ ] Implement HTMX interactions
  - [ ] Dynamic content loading without full page refresh
  - [ ] Form submissions (availability, plan generation)
  - [ ] Workout completion toggles
- [ ] PWA setup
  - [ ] `static/manifest.json` — app manifest
  - [ ] `static/js/sw.js` — service worker (offline shell, caching)
  - [ ] App icons
- [ ] Test on mobile devices
- [ ] **VERIFY:** Open PWA on phone, navigate all screens, data renders correctly

---

## Phase 7: Email Delivery (Week 7-8)

- [ ] Set up email service (Resend API or SMTP)
- [ ] Create HTML email templates (responsive, email-client compatible)
  - [ ] `email/templates/weekly_plan.html`
  - [ ] `email/templates/daily_briefing.html`
  - [ ] `email/templates/post_workout.html`
  - [ ] `email/templates/weekly_recap.html`
- [ ] Implement `email/sender.py`
  - [ ] Send function with template rendering
  - [ ] Error handling + retry
- [ ] Wire email triggers into coaching engine
  - [ ] After daily briefing → send email
  - [ ] After weekly plan → send email
  - [ ] After post-workout analysis → send email
  - [ ] After weekly recap → send email
- [ ] Add email preferences to user profile
- [ ] Test email rendering (Gmail, Apple Mail)
- [ ] **VERIFY:** Receive well-formatted emails in inbox

---

## Phase 8: Polish & Hardening (Week 8-9)

- [ ] Error handling
  - [ ] Global exception handler in FastAPI
  - [ ] Garmin session recovery (auto re-auth)
  - [ ] Claude API fallbacks
- [ ] Security
  - [ ] Encrypt stored Garmin credentials (cryptography.fernet)
  - [ ] Validate all API inputs via Pydantic
  - [ ] HTTPS setup (reverse proxy guide)
- [ ] Observability
  - [ ] Structured logging (structlog)
  - [ ] Log rotation
  - [ ] Health check includes: last sync time, scheduler status, recent errors
- [ ] Performance
  - [ ] Query optimization (check indexes on hot paths)
  - [ ] Cache coaching insights (don't regenerate if data unchanged)
- [ ] Rate limiting
  - [ ] Claude API budget enforcement (monthly ceiling)
  - [ ] Track costs in PromptLog
- [ ] Testing
  - [ ] Full test coverage for critical paths (>80%)
  - [ ] Integration tests with mock external services
- [ ] Documentation
  - [ ] README with setup instructions
  - [ ] API docs (FastAPI auto-generates OpenAPI/Swagger)
  - [ ] Deployment guide (Docker container)
- [ ] Deployment
  - [ ] Dockerfile
  - [ ] docker-compose.yml (app + optional Caddy for HTTPS)
  - [ ] Deploy to VPS or home server
- [ ] **VERIFY:** System runs reliably for 2+ weeks without manual intervention

---

## Post-MVP: Prompt & Coaching Improvements

- [ ] Enrich activity list formatting in `_format_activities()` (prompt_builder.py)
  - [ ] Currently only sends 4 fields (sport, title, start_time, duration_minutes) in list contexts
  - [ ] Add avg_hr, max_hr, calories, training_effect_aerobic to activity summaries
  - [ ] Affects: daily briefing, weekly recap, cardio plan prompts
  - [ ] Gives LLM training-load awareness when reasoning over recent activity history

---

## Phase 9: Open Workout Ingestion + Offline Companion Logger

> See PRD §11. Replaces Hevy web-API auto-sync with a canonical ingestion layer + a standalone
> offline-first companion PWA (LAN-only). Keeps Hevy CSV import.

### 9.0 Retire Hevy web-API auto-sync (keep CSV)

- [ ] Discard the uncommitted pair-based/keep-alive Hevy work in the working tree
- [ ] Remove `sources/hevy/api_client.py`, `api_source.py`, `api_parser.py`
- [ ] Remove routes `POST /api/sources/sync/hevy` and `POST /api/sources/hevy/refresh-token`
- [ ] Remove scheduler `hevy_sync` (+ keepalive) jobs from `scheduler/scheduler.py` + `jobs.py`
- [ ] Remove Hevy sync button + token re-seed UI from `templates/dashboard.html`
- [ ] Remove Hevy token/refresh/keepalive settings from `config.py`, `.env.example`, `docker-compose.yml`
- [ ] Keep `sources/hevy/csv_parser.py` + the CSV import route + dashboard/history CSV controls

### 9.1 Canonical ingestion layer

- [ ] `sources/workout_import.py` — `WorkoutImport` + `WorkoutSetImport` dataclasses (source-agnostic)
- [ ] `schemas/workout_import.py` — Pydantic `WorkoutImportBatch` request model
- [ ] `sources/importer.py::import_workouts(session, user_id, workouts, source)` — generic mapper → `Activity` + `GymWorkoutDetail`
- [ ] Alembic migration: add `Activity.external_id` (String, nullable, indexed)
- [ ] Dedup by `(user_id, data_source, external_id)` when present, else `(title, start_time)`
- [ ] Refactor `sources/hevy/csv_parser.py` to emit `WorkoutImport` (source `"hevy"`); route calls `import_workouts`; delete `hevy/mappers.py`
- [ ] Generalize `sources/merger.py` to merge gym activities where `data_source in ("hevy", "logger")`

### 9.2 Universal push endpoint + API-key auth

- [ ] `api/deps.py::require_api_key` — verify `X-API-Key` header against `settings.api_token`
- [ ] `POST /api/sources/import/workouts` — body `WorkoutImportBatch`, guarded, imports + auto-merges
- [ ] `GET /api/logger/exercises` — distinct `exercise_title`s from user's `GymWorkoutDetail`
- [ ] Tests: API-key rejection (401), valid batch counts, idempotent re-post (dedup)

### 9.3 Offline-first companion PWA (served on LAN)

- [ ] **Serve over HTTPS on the LAN (prerequisite for offline PWA)** — service workers only register
      in a secure context (HTTPS or `localhost`); a plain `http://<lan-ip>:8000` is non-secure and
      offline caching will NOT work (esp. iOS). Front MyCoach with Caddy (see `Caddyfile.example`)
      using either a real domain pointed at the LAN IP or a local CA cert (e.g. mkcert) trusted on
      the phone. Verify `navigator.serviceWorker.register()` succeeds on the phone before building the rest.
- [ ] `api/pages/logger.py` + `templates/logger/index.html` — standalone shell at `GET /logger`
- [ ] `static/logger/manifest.json` — own name / `start_url=/logger` / icons (reuse existing)
- [ ] `static/logger/sw.js` — service worker caching the app shell (offline), scoped to `/logger`
- [ ] `static/logger/app.js` — logging UI (vanilla JS + Tailwind), gym-only
  - [ ] Start session (title + auto start_time) → add exercises (free-text + autocomplete)
  - [ ] Add sets (weight, reps, set_type, RPE, notes); finish session
  - [ ] Edit/delete unsynced sessions; read-only once synced
  - [ ] Local session history + per-session sync status
- [ ] IndexedDB: queue sessions (client UUID = `external_id`, `synced` flag) + cached exercise list
- [ ] Sync: on load/online/"Sync now" → POST unsynced as `WorkoutImportBatch` (source `"logger"`); mark synced on 200; silent no-op when unreachable
- [ ] API-key settings field (stored in localStorage); document setting `MYCOACH_API_TOKEN`
- [ ] **VERIFY:** install `/logger` on phone → airplane-mode log a session → re-enable WiFi → auto-syncs into `/history`
