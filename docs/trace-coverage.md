# Trace-coverage check

`mycoach.tools.trace_coverage` checks that every scheduled coaching run in the
last N days has a trace in Langfuse. It is the evidence for the gate that
retires `prompt_logs` (#169, #179).

## Usage

Run it where the app's settings are loaded: the database URL and
`LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`.

```sh
docker compose exec mycoach python -m mycoach.tools.trace_coverage --days 30
```

From a laptop, set `LANGFUSE_BASE_URL` to the Langfuse UI host. The in-cluster
`http://langfuse-web:3000` name only resolves on the `langfuse-ingest` network.

`--days` defaults to 30 and must be 1 to 89, because Langfuse deletes traces
after 90 days.

Output is a summary line, then one line for each run that is not covered:

```
Trace coverage, last 30 days: 41 runs checked, 39 covered, 1 missing, 1 unresolved.
  MISSING    job_run 812 daily_briefing 2026-11-02 06:15Z  daily briefing 2026-11-02  trace 3f0c…
  UNRESOLVED job_run 790 post_workout_analysis 2026-10-28 21:00Z  post-workout batch  (no post-workout insight created during the run)
```

| Exit code | Meaning |
|---|---|
| 0 | Every run is covered |
| 1 | At least one run is missing its trace, or could not be matched to its artifact |
| 2 | Langfuse is not configured or the API call failed, or `--days` is out of range |

## What is checked

The check uses `job_runs` rows with `status = 'success'` for the jobs that call the LLM:
`daily_briefing`, `weekly_plan`, `weekly_recap` and `post_workout_analysis`.
Skipped and failed runs are ignored, and so are `daily_briefing_alert` and
`garmin_sync`. Manual runs from the API routes write no `JobRun`, so they are
not checked.

A trace counts as present when the Langfuse v2 observations endpoint returns a
root observation for its trace ID (`GET /api/public/v2/observations?traceId=…&isRootObservation=true`).
SDK v5 has no trace-get call. This endpoint is the real-time read path.

## Matching a run to its trace

`JobRun` stores no user, date, plan or activity. Each run's key is rebuilt from
the artifact it wrote, and the trace ID comes from the run builders in
`mycoach.observability.tracing` (`weekly_plan_run`, `daily_briefing_run`, …).
Those are the same builders the engine traces with.

A run's window is `[started_at, started_at + duration_ms]`, padded by one second.

| Job | Artifact written in the window | Expected trace |
|---|---|---|
| `daily_briefing` | `CoachingInsight` of type `daily_briefing` | `daily-briefing:<user>:<insight_date>` |
| `weekly_recap` | `CoachingInsight` of type `weekly_recap` | `weekly-recap:<user>:<insight_date>` |
| `weekly_plan` | `WeeklyPlan` | `weekly-plan:<plan.id>` |
| `post_workout_analysis` | every `post_workout` insight (a batch) | one `post-workout:<activity_id>` per insight |

### Forced regenerations

Scheduled jobs never force a run, so a `JobRun`'s own trace always has the
natural-key seed. A later forced regeneration from the dashboard deletes that
run's artifact and writes a new one outside the run's window. When the window
has no artifact, the check rebuilds the key from the run's start time, using
the local day in the scheduler's timezone (`MYCOACH_SCHEDULER_TIMEZONE`):

- **Daily briefing / weekly recap**: the replacement insight has the same key
  (that day, or the Monday of the previous week). The run is covered if the
  natural trace exists, or if Langfuse holds a regeneration of it. A
  regeneration is a root observation in the same week session and trace name
  whose `seed` metadata is `<natural seed>:regen:<timestamp>` (recognised by
  `trace_ids.is_regen_of`) and whose trace ID is that seed's. Its timestamp is
  not stored anywhere, so it is listed, not rebuilt.
- **Weekly plan**: a forced plan replaces the old plan with a new ID, so the
  scheduled plan's ID is gone. The replacement plan for the next Monday is
  checked, and its trace counts.
- **Post-workout batch**: a batch records no activity IDs. A regenerated
  activity drops out of its batch without notice. A batch with no insight left
  in its window is reported as **unresolved**.

A run whose key leads to no artifact at all is also **unresolved**, for
example a deleted insight. Unresolved runs fail the check, because their
coverage cannot be shown.

Known limits: a manual run that finishes inside a scheduled run's window is
counted as that run's artifact. The weekly plan and recap jobs compute their
dates with the process's local date. The fallback assumes this matches the
scheduler's timezone, which holds unless a job is scheduled within the UTC
offset of midnight.
