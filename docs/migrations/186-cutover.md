# Health module boundary and in-process events: what was built, deviations, human cutover (map #85, ticket #186)

Implements the decisions in #111 (module interface), #112 (events and settlement) and the seam of
ADR 0001/0002. Builds on #184 (`docs/migrations/184-cutover.md`) and #185
(`docs/migrations/185-cutover.md`): the health history gains `h186a1b2c3d4` on top of
`h185a1b2c3d4`, and the coaching history gains `c186a1b2c3d4` on top of `c184a1b2c3d4`. Nothing
here has been applied to the real database. The code, migrations and tests are in the branch; the
steps in section 5 are for a human on the homelab host.

## 1. What was built

**The health module (`mycoach.health`).** The only way coaching, the API routes and the
scheduler reach health data.

- Session-less: every function opens its own session through `mycoach.health.runtime`
  (default: `mycoach.database.health_async_session`), commits its own transaction and returns
  plain data (dicts, strings, dataclasses). Dates and datetimes cross as ISO strings. No ORM
  object crosses the boundary; this is the shape an HTTP client has after the physical split.
- Commands: `record_workout` / `record_workouts` (one idempotent call per workout plus its sets),
  `record_garmin_activities`, `link_garmin`, `settle_due_activities`, `sync_garmin` (fetch, link,
  settle in one call), `issue_athlete`, `store/delete/has_source_credentials`.
- Queries: snapshots (`get_today_health`, `get_health_trends`, `get_snapshot`, `list_snapshots`,
  `get_last_sync`), activities (`get_recent_activities`, `get_activities_for_week`,
  `get_activity`, `get_activity_with_details`, `get_activity_facts`, `get_similar_activities`,
  `list_activities`, `list_sports`, `list_settled_activities_since`), the gym performance
  aggregation (`get_gym_performance_history`, `get_last_week_gym_performance`,
  `get_last_week_cardio_performance`, ...), the Logger's reads (`list_exercise_catalogue`,
  `get_last_performed`).
- An activity is named by its **external id**. Activity dicts carry `external_id`, `sources`
  (list) and `settled`; they carry no internal `id`, no `data_source`, and gym sets carry no
  prescribed fields.

**Events (`mycoach.health.events`).** Frozen dataclasses `CanonicalActivityRecorded`,
`CanonicalActivitySettled`, `CanonicalActivityRevised`, each with `.to_dict()`.

- Deterministic ids: `sha256` of `(type, external_id[, revised_at])`, so a replayed intake or
  link raises events with the same ids.
- Health returns them as plain data. It never imports coaching and never dispatches.
- Settlement (#112): a Garmin-only activity settles at intake (reason `reconciled`); a gym-sourced
  one settles when a Garmin record links onto it (`reconciled`) or
  `Settings.activity_settlement_deadline_hours` (24) after its start (`deadline`). Linking onto
  an already-settled activity raises `Revised` with the changed cache columns.

**Dispatch (`mycoach.coaching.health_events`).** `dispatch(events, handlers)` calls each handler
per event and **isolates failures**: a failing handler is recorded in the result and never
raises. It runs only after health's own commit.

- `claim_on_recorded`: the Logger's `planned_session_id` is bound by the call site (the event
  carries none); it calls `claim_planned_session` and reports a refusal as a non-fatal note.
- `post_workout_on_settled`: final post-workout coaching, then the email.
- `post_workout_on_revised`: a retained Insight **revision** (never an overwrite) only when
  `changed_fields` intersects the prompt fields (avg_hr, max_hr, calories, both training effects,
  epoc, recovery time, distance, duration). Deduplicated by the event id stored in
  `coaching_insights.source_event_id`, so re-invoking a handler is safe.
- `coaching.workout_log.log_workouts` is the one call site for the Logger push, the universal
  import endpoint and the Hevy CSV: `health.record_workouts`, then coaching's prescription
  fulfillments, commit, then the dispatched `Recorded` events.

**Settlement check on the Garmin-sync job, no new cron.** `scheduler.jobs._garmin_sync` calls
`health.sync_garmin` (which settles what is due) and dispatches the returned events. The nightly
`post_workout_analysis` job stays, as a **catch-up scan** over settled activities with no
insight yet, so a failed handler is retried the next evening (ADR 0002: nothing redelivers).

**Coaching rekeyed to external ids.** `planned_sessions.activity_external_id` and
`coaching_insights.activity_external_id` replace `activity_id`. `plan_linking.py` claims and
links by external id (`claim_planned_session` declines non-fatally with a reason string, as
#111 decided). `observability.adherence`, trace seeds (`post_workout_seed`), the engine,
`prompt_builder` (no more direct health SQL, no `snapshot_to_dict`/`activity_to_dict`), the
Logger adapters and every route/page read health through the module.

**The athlete mapping.** `coaching.athlete.athlete_id_for(session, user_id)` reads
`users.health_athlete_id`. It falls back to `user_id` while that column is null (see
deviations).

**Config.** `MYCOACH_HEALTH_DB_URL` (new): the health module's own runtime login. Empty falls
back to `MYCOACH_DB_URL`. `docker-compose.yml` passes it from `MYCOACH_HEALTH_RUNTIME_DB_URL`.

### Migrations

**`h186a1b2c3d4` (health):** adds `canonical_activities.settled_at` / `settlement_reason`;
marks every existing activity settled with reason `reconciled` (so no burst of final-coaching
events fires on the first sync); fills any NULL `external_id` from the linked source records (a
gym source's id wins over a Garmin one); makes `external_id` NOT NULL; adds a unique index
`(athlete_id, external_id)`. It refuses, changing nothing (decided before the first DDL, since
SQLite cannot roll DDL back), when two activities of one athlete would share an external id.

**`c186a1b2c3d4` (coaching):** adds `activity_external_id` to `planned_sessions` and
`coaching_insights`, `revision` (default 1) and `source_event_id` to `coaching_insights`;
translates every existing `activity_id` through `health.canonical_activities`; drops
`activity_id`. It refuses, changing nothing, when a non-null `activity_id` does not resolve to a
canonical activity that has an external id.

**ORDER.** Upgrade: health `h186` first, then coaching `c186` (c186 reads
`canonical_activities`, once, so it needs a role that can: the superuser, as in #184).
`python -m mycoach.tools.migrate` now interleaves them (coaching to `c184`, the whole health
history, then the rest of coaching), so one run does the right thing. Downgrade: coaching
`c184` first, then health `h185`.

**`health_analytics` views: no break.** Neither revision touches a view. Every view lists its
columns explicitly, so the new `settled_at`/`settlement_reason` do not appear, and no published
column changes type or order. `test_the_analytics_views_are_unchanged_by_186` compares the
published columns before and after the revisions on Postgres. The `activities` view still reads
`data_source` (kept, deviation 7). Nothing in the views referenced a prescribed column or
`activity_id` of a coaching table.

## 2. Deviations from the designs, and why

1. **The module is session-less with a module-level session factory** (#111's prototype took a
   session). A session argument would let an ORM session, and so SQL, cross the seam; opening its
   own is what an HTTP client would do after the split. The cost: tests commit before calling
   code that reads through health, and the test engine is now a temp file instead of
   `:memory:` (a shared in-memory connection let a health session roll back coaching's
   uncommitted flushes).
2. **`record_workout` takes a `WorkoutImport` and an `athlete_id`**, not loose arguments; the
   importer's existing dataclass already is the workout-plus-sets payload.
3. **The `Recorded` handler is bound by the call site to `planned_session_id`** because the
   event payload carries none (it is coaching's concept).
4. **Manual routes dispatch only `Recorded`.** A `Settled` raised by a manual
   `/api/sources/merge` or import is not dispatched: the nightly catch-up scan covers it. A
   `Revised` raised the same way is lost (no scan covers a revision). Only the Garmin-sync job
   dispatches all three. If a revision from a manual link matters, the route should dispatch too.
5. **The nightly post-workout job is kept** as the catch-up scan rather than deleted, because
   nothing redelivers a failed handler before the broker exists.
6. **The athlete mapping falls back to `user_id`** while `users.health_athlete_id` is null (the
   #184 migration backfilled it to `id`, so it is not null in a migrated database). The
   `athlete_id == user_id` convention is retired in code but not in the data.
7. **`canonical_activities.data_source` is kept** (the #185 decision), so the `activities` view
   keeps publishing it. The Python API no longer exposes it; `sources` replaces it.
8. **Wire changes.** `/api/activities/{activity_external_id}` (with `/analysis` and
   `/analyze`) replaces the integer id; the `PATCH` plan parameter is `activity_external_id`.
   `ActivityRead` gains `external_id`, `sources`, `settled` and loses `id`, `user_id`,
   `data_source`, `garmin_activity_id`; `GymWorkoutDetailRead` loses `id`/`activity_id`;
   `DailyHealthSnapshotRead` loses `id`/`user_id`. The history page's handlers use the external
   id. The Logger's wire format is unchanged.
9. **Trace ids for post-workout runs are seeded from the external id**, not the internal id.
   Historical re-runs get a different trace id than the original run did.
10. **The "Data source" line is gone from the post-workout prompt**; it printed the internal
    `data_source`, which is no longer part of the module's data. This changes the prompt text.
11. **`Activity.external_id` has a uuid default** and is NOT NULL, so every activity is
    nameable; a Garmin-only one carries its Garmin id, a Hevy-CSV one its derived id.
12. **`src/mycoach/tools/cleanup_activity_58.py` and its test were deleted.** The one-off tool is
    tied to internal ids. If it has not run in production, run it from the pre-#186 commit
    before the cutover, or rewrite it against external ids.
13. **The Postgres "unchanged app on one bridge login" test was removed**; it asserted the
    bridge login this ticket retires. `test_health_boundary_postgres.py` replaces it with the
    two-login version.

**Residual risk (not fixed).** A Garmin-only gym activity settles immediately, so final
post-workout coaching can run before the Logger sync arrives. If the Garmin activity is later
linked onto the gym activity and then hard-deleted (the #185 behaviour), an insight or planned
session keyed to the Garmin-only external id dangles. The deadline rule in #112 assumes gym
activities arrive first; it should be revisited if Garmin-first gym sessions are common.

## 3. Verified

- **Seam, no SQL path.** `tests/test_health/test_boundary.py` (AST): no non-health module
  imports a health model or names a health table or schema in a SQL string; health and sources
  never import coaching; every health model lives in the health schema.
  `tests/test_migrations/test_health_boundary_postgres.py` (Postgres, `MYCOACH_TEST_PG_URL`):
  a login that is a member of `coaching_role` only gets `permission denied` on every health
  table, including the cross-schema join; a `health_role`-only login is refused on coaching.
- **The app flows on two separate logins** (same Postgres file): a Logger push with a plan claim
  and a prescription, then the post-workout read, with coaching on the coaching-only login and
  health on the health-only one.
- **Events and dispatch.** `tests/test_health/test_events.py`, `test_module.py` (settlement
  scenarios, idempotency, deterministic ids), `tests/test_coaching/test_health_events.py`
  (isolation, claim, settled, revised and its dedup), `tests/test_scheduler/test_sync_settlement.py`
  (the sync job settles a due activity, dispatches after health's commit, a failing handler does
  not fail the sync, no re-coaching on the next sync).
- **Post-workout still fires at the right time**: the settled handler on the sync, plus the
  catch-up scan (`tests/test_scheduler/test_jobs.py`, `test_job_runs.py`,
  `tests/test_post_workout/`).
- **Migrations.** SQLite: `tests/test_migrations/test_activity_external_ids_sqlite.py` (backfill,
  uniqueness, both refusals leave the schema untouched, re-keying, downgrade). Postgres: the
  #184/#185 tests were moved onto the #186 layout and still run the whole path.

Not verified: any run against the production database or a restored production dump.

## 4. Pre-flight

Run against production (read-only) before touching anything.

```sql
-- 1. Duplicate external ids per athlete. Must be zero rows; h186 refuses otherwise.
SELECT athlete_id, external_id, count(*) FROM health.canonical_activities
 WHERE external_id IS NOT NULL GROUP BY 1,2 HAVING count(*) > 1;

-- 2. Links that will not resolve. Must be zero rows; c186 refuses otherwise.
SELECT 'planned_sessions', p.id, p.activity_id FROM coaching.planned_sessions p
 WHERE p.activity_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM health.canonical_activities a WHERE a.id = p.activity_id)
UNION ALL
SELECT 'coaching_insights', i.id, i.activity_id FROM coaching.coaching_insights i
 WHERE i.activity_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM health.canonical_activities a WHERE a.id = i.activity_id);

-- 3. Counts to compare afterwards.
SELECT count(*) FROM health.canonical_activities;
SELECT count(*) FILTER (WHERE activity_id IS NOT NULL) FROM coaching.planned_sessions;
SELECT count(*) FILTER (WHERE activity_id IS NOT NULL) FROM coaching.coaching_insights;
SELECT count(*) FROM coaching.users WHERE health_athlete_id IS NULL;   -- expect 0
```

If query 2 lists rows, they point at an activity an old reconciliation deleted. Decide per row
(clear the pointer, or re-point it by hand); do not edit the migration to skip the guard.

Check which Grafana panels read `health_analytics.activities.data_source` and `gym_sets`:
none should change, but confirm they render after step 8.

## 5. Human cutover checklist

Nothing below has been done. **Precondition (same as #104/#107/#184/#185):** every cached or
pending logger session is drained and synced. Unsynced IndexedDB sessions arriving
mid-migration are how gym history goes missing. #184 and #185 must already be applied.

1. **Drain.** Confirm no pending sessions on the installed logger devices, then stop all
   writers: `docker compose stop mycoach mycoach-logger`. Leave `mycoach-db` up.
2. **Run the pre-flight queries.** Stop on any surprise. If the one-off
   `cleanup_activity_58` tool has not been run, run it now from the pre-#186 commit (see
   deviation 12).
3. **Back up and prove the backup**: an on-demand dump with the `mycoach-db-backup` sidecar
   (`docs/db-backup.md`), restore into a scratch database, compare the step 3 counts.
4. **Apply the migrations as the superuser** (the cutover reads across the two histories; the
   runner interleaves them in the right order):
   ```
   docker compose run --rm --no-deps -e MYCOACH_DB_URL="postgresql+asyncpg://mycoach:${MYCOACH_DATABASE_PASSWORD}@mycoach-db/mycoach" -e MYCOACH_COACHING_OWNER_DB_URL= -e MYCOACH_HEALTH_OWNER_DB_URL= mycoach python -m mycoach.tools.migrate
   ```
   Each revision is one transaction; a refusal changes nothing (and a re-run after fixing the
   data continues where it stopped).
5. **Verify the data.**
   ```sql
   SELECT count(*) FROM health.canonical_activities;                       -- = step 3 count
   SELECT count(*) FROM health.canonical_activities WHERE external_id IS NULL;   -- 0
   SELECT settlement_reason, count(*) FROM health.canonical_activities GROUP BY 1; -- all reconciled
   SELECT count(*) FILTER (WHERE activity_external_id IS NOT NULL) FROM coaching.planned_sessions; -- = step 3
   SELECT count(*) FILTER (WHERE activity_external_id IS NOT NULL) FROM coaching.coaching_insights; -- = step 3
   SELECT count(*) FROM coaching.coaching_insights WHERE revision <> 1;   -- 0
   ```
6. **Create the two runtime logins and set secrets** (never in a migration; follow
   `homelab-host/SECRETS.md`):
   ```sql
   CREATE ROLE health_runtime   LOGIN PASSWORD '<secret>';
   CREATE ROLE coaching_runtime LOGIN PASSWORD '<secret>';
   GRANT health_role   TO health_runtime;
   GRANT coaching_role TO coaching_runtime;
   ```
   Set deploy secrets: `MYCOACH_RUNTIME_DB_URL` = the `coaching_runtime` login,
   `MYCOACH_HEALTH_RUNTIME_DB_URL` = the `health_runtime` login (both
   `postgresql+asyncpg://...@mycoach-db/mycoach`). The owner URLs from #184 stay.
7. **Remove the bridge login** only after the new image is up and verified in step 9. Leaving
   it in place is harmless but keeps a login that can join the two schemas:
   `REVOKE health_role, coaching_role FROM mycoach_app; DROP ROLE mycoach_app;`
   (drop any default privileges or owned objects first; there should be none), and delete
   `mycoach_app`'s password from the secrets store.
8. **Deploy the new image** (`docker compose up -d --build`). Container start runs
   `python -m mycoach.tools.migrate` as the owners: a no-op now.
9. **Verify.**
   ```sql
   -- as superuser:
   SET ROLE coaching_role; SELECT * FROM health.canonical_activities;  -- permission denied for schema health
   RESET ROLE;
   SET ROLE health_role;   SELECT * FROM coaching.users;               -- permission denied for schema coaching
   RESET ROLE;
   SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname IN ('mycoach_app','health_runtime','coaching_runtime');
   ```
   Then: `GET /api/system/status` is 200; the dashboard, history and plan pages load; open an
   activity from the history page and run an analysis; log a test workout from the logger with a
   plan and confirm the set lands in `health.gym_workout_details`, the prescription in
   `coaching.prescription_fulfillments`, and the planned session is completed with its
   `activity_external_id`. Force a Garmin sync (`POST /api/sources/sync/garmin?days=3`) twice:
   the second reports `activities_created: 0`, and no post-workout analysis is regenerated.
   Check that the scheduler log shows no `handler failed` lines. Check the Grafana dashboards
   still render (the views are unchanged).
10. **Keep the step 3 dump** until a few days of clean running.

**Rollback.** Stop the app, restore the step 3 dump into a fresh database, redeploy the previous
image (and re-create the `mycoach_app` login first if step 7 dropped it). Downgrades exist and
are tested (coaching to `c184a1b2c3d4` first, then health to `h185a1b2c3d4`) but a restore is the
safe path for production data: the coaching downgrade drops insight revisions beyond the first
and cannot re-derive an `activity_id` whose activity is gone.

## 6. Details later tickets depend on

- The health module is the HTTP-client shape for the physical split: every function is
  session-less and returns plain data; swapping `mycoach.health.runtime` for an HTTP transport is
  the seam. Events are returned, not pushed: a broker (ADR 0002) would deliver the same dicts
  (`.to_dict()`) with the same ids.
- Roles after this ticket: `health_owner`/`coaching_owner` (DDL), `health_role`/`coaching_role`
  (DML-only), `health_runtime` and `coaching_runtime` (the two logins), `grafana_role`
  (`health_analytics` only). `mycoach_app` is gone.
- `athlete_id == user_id` still holds in the data for the one athlete; `users.health_athlete_id`
  is the mapping the code now uses.

## 7. Review follow-ups / known limitations

Fixed after code review (not repeated here): prescription rows are now re-written on a re-push
of an already-recorded workout (`log_workouts` is retry-safe across its two commits);
Garmin-only activities settle with reason `immediate`, not `reconciled`; the 24h deadline applies
only to non-Garmin sources; `REVISION_FIELDS` derives from the post-workout prompt (this added
`hr_zones`, which reconciliation can change and the prompt reads); the exercise mirror is the
`health.sync_exercise_catalogue` command; one `DEFAULT_USER_ID` in `mycoach.api.defaults`.

Known and deliberately **not** changed:

- **Crash between the two commits, claim side.** Only the prescription rows self-repair on a
  re-push. The planned-session claim runs for newly created workouts only, so a crash after the
  health commit still loses that claim (it is reported nowhere). A nightly reconcile of
  "workouts without a claim" is not built.
- **Garmin-only `external_id` reuse vs #109.** A Garmin-only activity's canonical external id is
  the Garmin id (`"g-1"`, or `garmin:<id>` on a clash), not a uuid4/derived id as #109 sketched.
  Unique per athlete and stable, but it is a provider id leaking into coaching's keys.
- **No dedicated HTTP intake endpoint for n8n (#106).** Intake is the Python
  `intake_source_record` and the universal `/import/workouts`; an endpoint shaped for n8n does
  not exist yet.
- **The #117 audit was derived from the migrations, not the live database.** Re-run it against
  production before cutover; the `reconciled` backfill and the per-athlete external-id
  uniqueness assume the audit's result.
- **`src/mycoach/tools/cleanup_activity_58.py` was deleted (see section 2, item 12).** The one-off data fix is gone; recover it from
  history if activity 58 still needs cleaning.
- **Dangling-key risk.** If a Garmin-only activity (external id = Garmin id) is later linked to a
  gym activity and the Garmin canonical row is deleted, anything coaching stored against the
  Garmin external id (insights, fulfillments) is orphaned, as there is no cross-store FK.
- **Existing rows keep `reconciled`.** The h186 backfill labels history `reconciled`, including
  old Garmin-only rows; only new Garmin-only activities get `immediate`.
- **`settle_due_activities` treats a NULL `data_source` as not gym-sourced** (never settled by
  deadline).

Larger refactors left as follow-ups: TypedDicts for the health module's plain-dict returns;
splitting `health/queries.py` (800+ lines); de-duplicating `coaching/context.py`; renaming the
`user_id` columns on health tables to `athlete_id`; the many other per-module `USER_ID = 1`
constants (pages, other routes, scheduler) that this pass did not fold into `api.defaults`.
