# MyCoach — Product Requirements Document (PRD)

## Context

**Problem:** Personal trainers are expensive and not always available. Generic fitness apps lack deep personalization. Users who train across multiple sports (gym, swimming, padel) have no single system that understands all their training holistically, adapts to their biometric data (sleep, HRV, stress, recovery), and provides progressive programming.

**Solution:** MyCoach is an AI-powered personal fitness coaching app that fetches data from multiple sources (Garmin watch for health/biometrics, Hevy CSV exports for detailed gym workouts), analyzes it with a configurable LLM provider (currently Gemini; Claude also supported), and delivers personalized weekly workout plans, daily coaching feedback, post-workout analysis, and sleep recommendations.

**Target user (MVP):** Single technically-proficient user training gym + swimming + padel, wearing a Garmin watch 24/7, logging gym workouts in Hevy (free tier).

**Long-term vision:** A sport-agnostic coaching platform where any user can add any sport, connect any wearable or fitness app (Garmin, Strava, Apple Health, Google Health, Oura Ring, Whoop), and receive professional-grade AI coaching.

---

## Current Status (2026-07)

Codebase audit confirmed the MVP backend is **built and functional**, not stubs:

- ✅ **Daily morning digest** — scheduler runs Garmin sync → daily briefing →
  email, end-to-end (`scheduler/`, `coaching/engine.py`, `email/sender.py`).
- ✅ **Weekly recap** — Monday job generates a full review (adherence, health
  trends, gym history, sport goals) with AI tips for the coming week, emailed.
- ✅ **Weekly plan generation** (two-track gym + cardio), **post-workout analysis**.
- ✅ **Email** — Resend + SMTP backends, all templates, per-user prefs.
- ✅ **PWA** — 8 data-backed pages, base layout (Tailwind + HTMX), SW + manifest.

**Remaining work (roadmap order — see `TODO.md`):**

1. **Verify & harden automation** — confirm the daily/weekly emails actually fire
   end-to-end; make the weekly-recap schedule config-driven (currently hardcoded).
2. **Retire the fragile Hevy web-API auto-sync** (keep the CSV import) — see §11 / §9.
3. **Canonical ingestion layer** + universal push endpoint (API-key auth) — see §11.
4. **Offline companion gym logger PWA** (the #1 remaining user feature) — log lifts
   offline at the gym, auto-sync at home over the LAN; needs HTTPS/Caddy — see §11.
5. **PWA polish** — settings page, dedicated workout-detail page, icon assets.

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

**Hevy (gym workout data):**

**Primary: Hevy API Sync (direct fetch):**
- Via Hevy's internal web API (`api.hevyapp.com`) using username/password authentication
- **Endpoints:** `GET /workout_count`, `GET /workouts_batch/{index}` (cursor-based pagination)
- **Auth:** Login with email/password to get JWT token, sent as `Authorization: Bearer <token>` + `x-api-key: shelobs_hevy_web` + `hevy-platform: web`
- **Sync modes:** Manual trigger (UI button) + scheduled auto-sync (daily, before Garmin sync)
- **Data:** Full exercise detail per workout: sets, reps, weight_kg, RPE, muscle_group, exercise_template_id, duration, distance
- **Features:** Incremental sync (only new workouts), deduplication by title+start_time, automatic Garmin merge after sync

**Fallback: Hevy CSV Import:**
- Via Hevy's free CSV export (no Pro subscription required)
- **Workflow:** User exports CSV from Hevy app (Profile > Settings > Export Workouts), then uploads to MyCoach via PWA
- **CSV columns:** title, start_time, end_time, exercise_title, superset_id, exercise_notes, set_index, set_type, weight_lbs, reps, distance_miles, duration_seconds, rpe
- **Import features:** Deduplication (skip already-imported workouts by date+title), incremental import (only process new rows), validation + error reporting

- **Key benefit:** Detailed gym data (exact weights, reps, RPE per set) that Garmin's strength tracking doesn't capture accurately. Zero subscription cost.

**Data merging strategy:** Garmin provides the biometric context (how the body responded — HR, calories, training effect), Hevy provides the gym workout details (what was actually done — exercises, weights, reps). For gym sessions, Hevy data is the source of truth for exercise details; Garmin provides the HR/calorie overlay. The system matches gym activities by date/time overlap. For swimming and padel, Garmin is the sole source.

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

### 1.5 Progressive Programming
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
| Hevy | API sync (web API) + CSV import fallback |
| LLM | Provider-configurable (`MYCOACH_LLM_PROVIDER`) — Gemini (in use) or Claude/Anthropic |
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
- **CoachingInsight** — daily briefing, post-workout analysis, weekly recap
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
- **Provider strategy:** The LLM layer is provider-configurable via `MYCOACH_LLM_PROVIDER`. **Gemini is the provider currently in use** (`gemini-2.5-flash` daily, `gemini-2.5-pro` weekly); **Claude/Anthropic remains fully supported** as an alternative (`claude-sonnet-4-5` daily, `claude-opus-4-6` weekly). Providers are swappable behind a common `LLMClient` interface (`coaching/providers/`).
- **Model strategy (provider-neutral):** A cheaper/faster model for daily briefings, a stronger/higher-reasoning model for weekly plan generation. This dual-model split applies to both providers (Gemini Flash → Pro, Claude Sonnet → Opus).
- **Token budget management:** Raw Garmin data summarized into compact representations before prompting. Rolling context windows (7 days for plans, 3 days for dailies)
- **Prompt versioning:** Templates in `prompts/v1/`, `v2/`, etc. PromptLog tracks which version produced each output.
- **Response validation:** JSON parsed → Pydantic validated → retry once on failure → fallback to text

**Estimated monthly cost:** Provider-dependent for a single user. The ~$20-30/month ceiling (`MYCOACH_CLAUDE_MONTHLY_COST_CEILING`) reflects the Claude/Anthropic configuration; Gemini (the provider currently in use) is typically lower for the same workload. Track actual spend via `PromptLog`.

---

## 6. Development Phases

| Phase | Scope | Status |
|-------|-------|--------|
| **0: Foundation** | Project scaffolding, FastAPI, SQLAlchemy, Alembic, config, health check | ✅ Done |
| **1: Data Sources** | Garmin auth + fetch + mappers, Hevy CSV parser + import, data merging, manual sync endpoint | ✅ Done |
| **2: Coaching Core** | LLM client, prompt builder, response parser, daily briefing | ✅ Done |
| **3: Weekly Plans** | Availability input, mesocycle tracking, sport modules, plan generation | ✅ Done |
| **4: Post-Workout** | Activity analysis, plan adherence tracking | ✅ Done |
| **5: Automation** | Scheduler, weekly recap, ~~post-workout auto-analysis~~, full daily pipeline | ✅ Done |
| **6: PWA Frontend** | Dashboard, plan view, availability input, history, ~~settings~~, service worker | 🟡 Mostly (no settings/workout-detail page, missing icons) |
| **7: Email** | Email templates, send triggers, email preferences | ✅ Done |
| **8: Polish** | ~~Error handling~~, ~~encryption~~, ~~logging~~, ~~profile API~~, ~~sport profile API~~, ~~testing~~, ~~deployment~~ | ✅ Done |

**Current roadmap (remaining work — see `TODO.md` "Next Steps"):**

| Step | Scope |
|------|-------|
| **1: Verify automation** | Confirm daily digest + weekly recap fire end-to-end into inbox; make weekly-recap schedule config-driven |
| **2: Retire Hevy web-API sync** | Remove fragile web-API sync + keepalive; keep Hevy CSV import (§11 / §9.0) |
| **3: Canonical ingestion** | Source-agnostic `WorkoutImport` schema + `import_workouts` + `Activity.external_id` (§11 / §9.1) |
| **4: Universal push endpoint** | `POST /api/sources/import/workouts` + API-key auth (§11 / §9.2) |
| **5: Offline gym logger PWA** | Standalone offline-first `/logger`, LAN sync, HTTPS/Caddy (§11 / §9.3) |
| **6: PWA polish** | Settings page, workout-detail page, icon assets, manifest colors |

---

## 7. API Endpoints (Key)

```
POST /api/sources/sync                    # Trigger sync from all sources (Garmin auto-fetch)
POST /api/sources/sync/garmin             # Trigger Garmin sync manually
POST /api/sources/sync/hevy                # Sync workouts from Hevy API
POST /api/sources/import/hevy             # Upload Hevy CSV file (fallback)
GET  /api/sources/status                  # Connection status of all data sources
GET  /api/health/today                    # Today's health snapshot
GET  /api/health/trends?days=30           # Health trends

GET  /api/activities                      # List activities
GET  /api/activities/{id}/analysis        # Post-workout analysis

POST /api/availability                    # Set weekly availability
POST /api/plans/generate                  # Generate weekly plan
GET  /api/plans/current                   # Current week's plan
GET  /api/plans/{id}/adherence            # Plan adherence stats (completed/total %)

GET  /api/coaching/today                  # Today's daily briefing
GET  /api/system/status                   # Health check
```

---

## 8. Key Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Garmin blocks unofficial API | Store data locally once fetched; begin official API application early |
| Hevy internal API changes/blocks | CSV import remains as fallback; API client handles 401 re-auth; login endpoint discovery at startup |
| Hevy CSV export format changes | Store raw CSV alongside parsed data; parser is simple to update if columns change |
| LLM hallucinations in workout plans | Safety guardrails in prompts, output validation (RPE ranges, reasonable reps/sets) |
| Auth token expiration | Robust re-auth via garth, alerting on failure |
| LLM API cost overruns | Token budget tracking, monthly ceiling, cheaper models for routine tasks; provider switchable to lower-cost option |
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
- ~~**Hevy API direct sync** — replace manual CSV upload with direct API fetch from Hevy's web API. Username/password auth, scheduled + manual sync, CSV kept as fallback.~~
- ~~**Built-in gym workout logger** — replace Hevy CSV with a minimal PWA logger.~~ → planned as the offline companion PWA in **§11**.
- ~~**wger integration** — deploy self-hosted wger and connect via REST API.~~ Rejected (too bloated); OpenLift also rejected (not self-hostable yet). See **§11**.
- Nutrition tracking integration
- Interactive AI chat for ad-hoc coaching questions
- Native push notifications
- Injury tracking
- User-selectable data sources (onboarding flow: "Which apps do you use?")
- ~~**Richer activity summaries in LLM prompts** — `_format_activities()` now sends distance, avg_hr, calories, training_effect_aerobic alongside the original 4 fields. `_format_health()` now includes sleep stages, body_battery_low, and max_hr.~~

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

---

## 11. Open Workout Ingestion & Offline Companion Logger

> Supersedes the struck-through "Hevy API direct sync" and the "Built-in gym workout logger" /
> "wger integration" items in §9. Planned feature — not yet implemented.

### 11.1 Context / Why

Hevy's unofficial web API is fundamentally fragile: refreshing an access token requires a
still-valid (~15-min) access token sent as `Authorization: Bearer`, and the only way to obtain one
is a reCAPTCHA-gated `/login` that no headless server can call. Reliable hands-off auto-sync is
therefore impossible without a keep-alive hack that breaks on any outage.

Evaluated open-source replacements and rejected both: **wger** (too bloated — diet/nutrition/health,
we only want lifting) and **OpenLift** (not viable yet — backend not open-sourced, self-hosting
"coming soon", hosted-only undocumented GraphQL API). Decision: **build from scratch.**

Hard constraint: **MyCoach is homelab/LAN-only and must stay non-externally accessible.** So logging
cannot happen "in the app" from the gym (that would require exposing MyCoach externally). Instead we
log **offline on the phone** and sync to MyCoach over the home LAN.

### 11.2 Goals

- Make MyCoach **open to any workout source** via a canonical ingestion schema + a single
  authenticated push endpoint (companion app, scripts, iOS Shortcuts, future apps).
- Provide a **standalone offline-first companion PWA** (served by MyCoach on the LAN, vanilla JS)
  that logs lifts fully offline at the gym and pushes to MyCoach when back on home WiFi.
- **Retire** the Hevy web-API auto-sync; keep the reliable **Hevy CSV import** as a fallback.
- Full data ownership, zero third-party API fragility, no external exposure of MyCoach.

### 11.3 Data Model changes

- **Activity.external_id** (new, String, nullable, indexed) — source-native/stable id for robust
  deduplication. Dedup key: `(user_id, data_source, external_id)` when present, else fall back to
  `(title, start_time)`.
- **Activity.data_source** gains value `"logger"` (companion app). Garmin merge (§1.1) generalized
  to overlay HR/calories onto gym activities where `data_source in ("hevy", "logger")`.

### 11.4 Canonical ingestion schema (source-agnostic)

`WorkoutImport { external_id, source, title, sport="gym", start_time, end_time, notes, sets[] }`
with `WorkoutSetImport { exercise_title, superset_id, exercise_notes, set_index, set_type,
weight_kg, reps, distance_meters, duration_seconds, rpe }` — a promotion of today's Hevy
intermediate dataclasses. A generic `import_workouts(session, user_id, workouts, source)` maps these
to `Activity` + `GymWorkoutDetail`. The Hevy CSV parser is refactored to emit this schema.

### 11.5 Endpoints

- `POST /api/sources/import/workouts` — **universal push**, body `WorkoutImportBatch`, guarded by an
  API-key dependency (`X-API-Key` vs `MYCOACH_API_TOKEN`). Imports + auto-merges with Garmin.
- `GET /api/logger/exercises` — distinct exercise titles from the user's history (offline
  autocomplete source for the companion app).
- `GET /logger` — serves the standalone companion PWA (own manifest, service worker, icon).
- Kept: `POST /api/sources/import/hevy` (CSV). Removed: `POST /api/sources/sync/hevy`,
  `POST /api/sources/hevy/refresh-token`, Hevy scheduler jobs.

### 11.6 Companion PWA (offline-first, LAN)

Vanilla JS + Tailwind + service worker + IndexedDB, **gym-only**. Install once at home (own app
icon); log sessions fully offline at the gym; edit/delete allowed while unsynced (read-only after
sync); auto-push unsynced sessions (each tagged with a client UUID = `external_id`) to the universal
endpoint when MyCoach is reachable on the LAN. Free-text exercise entry with autocomplete cached
locally. API key entered once and stored in localStorage.

**Prerequisite — HTTPS / secure context:** Service workers (hence offline caching) only register in
a secure context (HTTPS or `localhost`). A plain `http://<lan-ip>:8000` is non-secure and offline
mode will not work, especially on iOS. MyCoach must therefore be fronted by HTTPS on the LAN —
provided by the separate `homelab-edge` repo (one shared Caddy + wildcard cert for every app on
the home server, not something MyCoach configures itself). This is what makes "log at the gym
with no signal, sync at home" work.

### 11.7 Verification

Tests for `import_workouts` (dedup by external_id + fallback), the push endpoint (API-key
rejection + idempotent re-post), refactored Hevy CSV path, generalized merge, and the Alembic
migration. End-to-end: install `/logger` on a phone, airplane-mode log a session, re-enable WiFi,
confirm auto-sync into `/history`.
