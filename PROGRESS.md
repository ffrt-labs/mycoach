# MyCoach — Progress Tracker

> This file tracks what has been completed, when, and any notes/decisions made along the way.
> PRD.md defines the end state. TODO.md lists all tasks. This file records actual progress.

---

## Current Phase: 9 — Companion logger & cleanup

**Status:** In progress. Phases 0–8 are implemented; the automation pipeline was
verified end to end on 2026-07-28 (see the log entry for that date). Remaining
work is ordered in TODO.md's "Next Steps" section.

> This file was itself stale for months — the phase table below still read "Not
> started" for every phase long after they shipped. Entries between 2026-02-13
> and 2026-07-28 are missing; the git history is the record for that period.

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

### 2026-07-28 — End-to-end automation verification (Step 1 closed)

Every scheduled job was triggered by hand through
`POST /api/system/scheduler/trigger/{job}` against the real Garmin account and
the live Resend backend, and the resulting `job_runs` rows were reconciled
against the actual inbox.

| # | Job | Status | Delivered | Note |
|---|-----|--------|-----------|------|
| 1 | `garmin_sync` | success | — | 3 health snapshots, 0 new activities |
| 2 | `daily_briefing` | success | ✅ 12:53:59Z | briefing email arrived |
| 3 | `weekly_recap` | success | ✅ 12:54:39Z | recap for week of 2026-07-20 arrived |
| 4 | `daily_briefing` | skipped | — | briefing already existed — 13 ms, no send |
| 5 | `post_workout_analysis` | skipped | — | no new activities — 14 ms, no send |
| 6 | `weekly_plan` | skipped | — | no availability set for w/c 2026-08-03 |
| 7 | `daily_briefing` | **failed** | — | induced: invalid Resend key |
| 8 | `daily_briefing` | success | ✅ 12:56:57Z | recovery after restoring the key |

Runs 7 and 8 needed the briefing job to reach its send step, which run 4 shows
it will not do once a briefing exists for the day. Today's `daily_briefing`
insight was therefore deleted before each of them, so the job would regenerate
and attempt a send. Runs 4, 7 and 8 are all honest records — but they are not a
sequence the job could have produced unaided, and a later reader should not
puzzle over it.

Runs 5 and 6 skipped for want of input, which means the **post-workout** and
**weekly-plan** emails were never attempted: two of the four templates are still
unobserved in a real inbox. Carried as an open item in TODO.md Step 1 rather
than counted as verified here.

**Reconciliation:** exactly three runs recorded `email_delivered = true` and
exactly three messages arrived, timestamps matching to the second. No run
reported success without a corresponding delivery. Each skip was a genuine
no-op — all three finished in well under half a second, before any LLM call or
send, for a stated reason.

**Induced failure.** Run 7 was driven with a deliberately invalid Resend API
key. Its recorded error reads in full:

> `daily briefing email send failed — Resend rejected the message: API key is invalid`

That is diagnosable without consulting any other table or log. Getting there
required a code change, not just a check: the backends previously swallowed the
rejection reason and returned `False`, so the run would have recorded only
"daily briefing email send failed". `email/sender.py` now raises
`EmailSendError` carrying the backend's own words, and a `False` return means
only that no backend was available to attempt the send at all.

**What the audit established, and what the docs had wrong:**

- **The email phase was ticked complete having never sent a message.** Phase 7
  was marked ✅ on 2026-07-12 on the strength of the code existing and the
  triggers being wired. No real delivery had ever happened. TODO.md's Phase 7
  note now says so.
- **The roadmap's email-configuration item was not merely unverified — it was
  false.** Step 1 carried "confirm `.env` is set for real delivery" as an
  unchecked box; the truth was that it was *not* set, so every send would have
  returned False. Compounding it, jobs discarded that False and logged "email
  sent" anyway until #9.
- **The March→July gap in coaching output was disuse, not a defect.** Insights
  run daily through 2026-03-18, then stop until 2026-07-10; activity data has
  the same shape, with nothing logged between 2026-03-10 and 2026-07-12. The app
  simply was not being run or fed during that window. Nothing in the scheduler
  misfired — the jobs are idempotent and skip cleanly when there is no new data,
  which is precisely what runs 4–6 above demonstrate.

**Follow-ups recorded in TODO.md Step 1 rather than left implicit:** verifying a
custom sending domain (mail currently leaves as `onboarding@resend.dev`,
Resend's shared sandbox sender), and wiring `job_runs` into an observability
stack once one exists — nothing consumes the rows today, so this reconciliation
remains a manual exercise.

**One observation, not yet explained:** a daily briefing also arrived at
05:30:19Z, before any of the above, matching the deployed instance's 06:30
Europe/London schedule. It has no corresponding row in this local `job_runs`
table and no matching insight in this database, so that deployment is running
against its own data. Run history is therefore per-instance, which is worth
keeping in mind before treating any single `job_runs` table as the whole story.

---

## Phase Summary

Two statuses, deliberately kept apart — conflating them is how Phase 7 came to
be ticked complete having never sent a message:

- **Implemented** — the code exists and reads correct. This is what the
  2026-07-12/16 audits established, and it is all they could establish.
- **Verified** — the behaviour has been observed working against real data and
  real backends, with a date naming the run.

| Phase | Name | Status | Started | Completed |
|-------|------|--------|---------|-----------|
| 0 | Foundation | Implemented (audit 2026-07-12) | 2026-02-13 | — |
| 1 | Data Sources | Implemented (audit 2026-07-12) | — | — |
| 2 | Coaching Core | Implemented (audit 2026-07-12) | — | — |
| 3 | Weekly Plans | Implemented (audit 2026-07-12) | — | — |
| 4 | Post-Workout | Implemented (audit 2026-07-12) | — | — |
| 5 | Automation | **Verified** end to end 2026-07-28 | — | 2026-07-28 |
| 6 | PWA Frontend | Implemented, less settings + workout-detail pages | — | — |
| 7 | Email | **Verified** for briefing + recap 2026-07-28; weekly-plan and post-workout emails still unobserved | — | — |
| 8 | Polish | Implemented (audit 2026-07-12) | — | — |
| 9 | Companion logger & cleanup | In progress | — | — |

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
