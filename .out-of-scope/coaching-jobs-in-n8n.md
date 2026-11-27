# Coaching Jobs in n8n

MyCoach runs its own scheduled jobs (`daily_briefing`, `weekly_plan`, `weekly_recap`, `post_workout_analysis`, `garmin_sync`) in-process with APScheduler. It does not move their scheduling or execution into n8n workflows.

## Why this is out of scope

The n8n discipline from #85/#106 is "it delivers, it never interprets." Four of the five jobs fail that bar outright: they build a coaching context, call the LLM and write coaching artifacts. That is the interpreting half of the system.

Moving only the cron and leaving the job bodies in Python doesn't help either. The `daily_briefing` trigger is deliberately a dumb 15-minute poll, and every real rule (generation and evaluation cutoffs, attempt budget, one alert a day) lives in `briefing_window`. That keeps the rules in one place that can be checked against `job_runs`. An n8n schedule would either take over half of those rules, splitting them across a workflow and a decision function, or act as a plain timer that adds a network hop and a second failure surface for nothing.

`job_runs` is coaching-side state, not scheduler bookkeeping. The dashboard, the job-log page, the job-runs API, the briefing-window logic and the trace-coverage tool all read it. Splitting execution would force a split ownership question about that table for no gain.

The remaining job, `garmin_sync`, is the only one shaped like schedule-fetch-deliver, and moving it is already rejected in `garmin-ingestion-in-n8n.md`.

The prompt for this request was two Postgres-cutover bugs in the scheduler loop (#125, #153). Those were bugs in our own code that the cutover exposed, and both are fixed. A different runtime would have moved them, not prevented them.

## What is not rejected

`POST /scheduler/trigger/{job_id}` already lets an external system fire a job on demand. If a real external orchestration need appears, start from that endpoint in a new issue.

## Prior requests

- #154: "Consider moving MyCoach's own scheduled jobs (briefing/plan/recap) to n8n"
