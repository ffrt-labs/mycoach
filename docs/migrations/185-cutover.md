# Source records and canonical activities: what was built, deviations, human cutover (map #85, ticket #185)

Implements the design decided in #109 (model), #110 (no provenance reconstruction), #113
(additive-only analytics views) and lifts the #116 disabling. Builds on #184
(`docs/migrations/184-cutover.md`): the health history gains one revision on top of
`h184a1b2c3d4`. Nothing here has been applied to the real database; the code, migration and
tests are in the branch, and the steps below are for a human on the homelab host.

## 1. What was built

**Tables** (all in `health`):

| Table | Purpose |
| --- | --- |
| `source_records` | One shared table, JSONB `raw_payload`, immutable. `(athlete_id, source, external_id)` unique. A trigger on Postgres rejects every `UPDATE` and `DELETE`, even from `health_role`. |
| `canonical_activities` | The old `activities` table, renamed. `garmin_activity_id` and `raw_data` are dropped (they live in `source_records`). Carries the HR/training-effect cache. |
| `canonical_activity_sources` | `(canonical_activity_id, source_record_id)` join: the reconciliation link. |
| `exercises` | Load-time mirror of the pinned catalogue (`exercise_catalogue.sync_exercises`, run at app startup and seeded by the migration). `gym_workout_details.exercise_id` is a nullable FK to it. |

`gym_workout_details.activity_id` is now column `canonical_activity_id` (performed sets attach to
the canonical activity). The ORM keeps the attribute name `activity_id`.

**Code:**

- `mycoach.sources.intake.intake_source_record`: the one shared intake. Idempotent on
  `(athlete, source, external_id)`; a replay changes nothing and never rewrites the record. The
  Garmin sync, the logger push and the Hevy CSV import all call it (`sources/importer.py`,
  `sources/garmin/mappers.py`). Source with no native id (Hevy CSV) are identified by
  `derived:` plus a hash of `(title, start_time)`, the old fallback dedup key made a string.
- `mycoach.sources.reconcile.link_garmin_to_gym` replaces `merger.py` (deleted). A Garmin-only
  gym activity that overlaps a gym canonical activity (same 30-minute rule as before) has its
  join row moved onto the gym activity, its HR/training-effect columns copied over, and the
  Garmin-only canonical activity hard-deleted. Both source records stay untouched. Replaying is a
  no-op, and so is a Garmin re-sync: the source record remembers the activity, so the deleted
  Garmin-only activity is not recreated.
- #116 is lifted: the scheduler's Garmin sync job, `/import/workouts`, `/import/hevy`,
  `/sync/garmin` and `/api/sources/merge` link again (`/merge` returns 200, not 503).
  The response field `activities_merged` keeps its name for wire compatibility.
- `/api/sources/status` reads `source_records.source`, not `data_source IN (...)`.
- The seam is preserved: `intake.py` and `reconcile.py` import nothing from coaching
  (`tests/test_sources/test_intake.py` checks it), and no new SQL joins coaching. The existing
  `importer.py` still calls coaching (`record_fulfillments`, `claim_planned_session`) exactly as
  #184 left it; #186 owns that.

**`h185a1b2c3d4` (health history):** renames the table and column, creates the new tables,
backfills one source record plus one link per existing activity, verifies the three counts
agree, drops the provenance columns, adds the exercise FK, and on Postgres replaces the views
and creates the immutability trigger. It is one transaction. It refuses, changing nothing, in
the two cases listed under pre-flight.

**`health_analytics` views (#113): additive, no break.** The migration uses
`CREATE OR REPLACE VIEW`, which Postgres only accepts if every existing column is kept with the
same type, so the additive rule is enforced by the database, and
`test_analytics_views_stay_readable_and_additive` checks it.

- `activities` gains `sources` (`text[]`, the source names of the linked source records). Its
  `data_source` column keeps publishing `merged` for an activity that has both a Garmin and a
  gym source record, so a panel filtering on it keeps working, although no stored column holds
  that value any more.
- `gym_sets` gains `canonical_activity_id`; `activity_id` stays (same values).
- `daily_metrics` is unchanged.

## 2. Deviations from the design, and why

1. **`canonical_activities.data_source` is kept** (as "the source that created the activity":
   `garmin`, `hevy` or `logger`, never `merged`). #109 lists no such column. Dropping it would
   have rewritten about 70 test constructors and the history/prompt/adherence readers for no
   behavioural gain; it is documented as a cache of the first contributing source record, like
   the HR columns. Provenance (the set of sources) is only ever read from the join table. One
   consequence: an activity that used to turn `merged` stays `logger`, so
   `coaching/context.py`'s "unlinked logger activities do not feed adherence" rule (#146) now
   also applies to a logger workout that has a Garmin record. With zero `merged` rows today this
   changes nothing in the real data.
2. **`source_records` carries `athlete_id`, and uniqueness is `(athlete_id, source,
   external_id)`**, not `(source, external_id)`. #109's column list omits it; the map says
   records stay athlete-aware, and the old dedup was per user. Behaviour for one athlete is
   identical.
3. **The class is still `Activity`** (mapped to `canonical_activities`), like `GymWorkoutDetail`.
   No rename, to avoid churn across about 40 files.
4. **A `merged` row aborts the migration** instead of being backfilled. #110 found 0 such rows
   on 2026-09-23; if the number is not 0 now, the gym-vs-Hevy distinction and the Garmin payload
   of those rows were already destroyed, and a backfill would invent provenance. See pre-flight.
5. **An exercise id the catalogue does not know is stored as NULL (custom exercise)** by the
   importer, and aborts the migration if one is already in the data: the column is now a FK to
   the catalogue mirror, so it cannot hold one. The logger's workout POST still accepts any id.
6. **`ActivityRead.garmin_activity_id` still exists in the API schema but is now always null**
   (the column moved to `source_records`). Left in place for wire compatibility; remove it with
   the next API cleanup.
7. **Immutability is a Postgres trigger only.** SQLite (the test engine) has no equivalent, and
   the trigger lives in the migration, so a fresh install built with `create_all` (the
   `docs/postgres-cutover.md` path) would lack it and the analytics views alike; run the health
   history on such an install. The downgrade is lossy for one case: see rollback.
8. The `athlete_id == user_id` convention and the bridge login from #184 are unchanged.

## 3. Verified

- SQLite (suite default): `tests/test_sources/test_intake.py`, `test_reconcile.py`,
  `test_importer.py` (source-record cases), `test_garmin.py` (source-record and re-sync cases),
  `test_sources_status.py`, `tests/test_exercise_mirror.py`,
  `tests/test_migrations/test_source_records_sqlite.py` (row counts, history preserved,
  provenance backfill, FK, replaying an intake is a no-op, both refusals, downgrade).
- Postgres 16 (set `MYCOACH_TEST_PG_URL`; skipped otherwise):
  `test_source_records_postgres.py` (owners and grants, trigger refuses `UPDATE`/`DELETE` as
  `health_role`, unique identity, FK, additive views and `grafana_role` read access, `merged`
  published for a linked activity, refusal leaves the schema untouched, upgrade-downgrade-upgrade)
  and `test_source_records_runtime_postgres.py` (intake and linking as `health_role` only,
  replay is clean). The #184 Postgres tests were adjusted to the new table names and still run
  the whole pre-seam to head path.

Not verified: any run against the production database or a restored production dump.

## Pre-flight

Run against production (read-only) before touching anything.

```sql
-- 1. #110's premise. Must be 0; the migration refuses otherwise.
SELECT data_source, count(*) FROM activities GROUP BY 1;            -- (health.activities after #184)

-- 2. Every exercise id must be in the catalogue; the migration lists any that are not.
SELECT DISTINCT exercise_id FROM gym_workout_details WHERE exercise_id IS NOT NULL;

-- 3. The backfill needs a unique (athlete, source, external id). Expect zero rows.
SELECT athlete_id, data_source, external_id, count(*) FROM activities
 WHERE data_source IN ('logger','hevy') AND external_id IS NOT NULL
 GROUP BY 1,2,3 HAVING count(*) > 1;
SELECT athlete_id, title, start_time, count(*) FROM activities
 WHERE data_source = 'hevy' AND external_id IS NULL GROUP BY 1,2,3 HAVING count(*) > 1;
SELECT garmin_activity_id, count(*) FROM activities
 WHERE data_source = 'garmin' GROUP BY 1 HAVING count(*) > 1;      -- unique already

-- 4. Row counts to compare against afterwards.
SELECT count(*) FROM activities; SELECT count(*) FROM gym_workout_details;
```

If query 1 shows a `merged` row, stop and decide what its provenance is (#110 said none exist);
do not edit the migration to skip the guard.

Check which Grafana panels read `health_analytics.activities.data_source`: it still returns
`merged` for linked activities, so none should break, but this is the column that changed meaning.

## Human cutover checklist

**Order.** #184's cutover must be applied first (or in the same window: `python -m
mycoach.tools.migrate` runs coaching, then health history `h184` then `h185`). Everything below
assumes #184 is done. Precondition (same as #104/#107/#184): every cached/pending logger session is
drained and synced. Unsynced IndexedDB sessions arriving mid-migration are how gym history goes
missing.

1. **Drain.** Confirm no pending sessions on the installed logger devices, then stop all writers:
   `docker compose stop mycoach mycoach-logger`. This also stops the scheduler's Garmin sync.
   Leave `mycoach-db` up.
2. **Re-check the `merged` count** (pre-flight query 1) and the other pre-flight queries. The
   real database had 0 `merged` rows as of 2026-09-23; confirm it still does.
3. **Back up and prove the backup**: on-demand dump with the `mycoach-db-backup` sidecar
   (`docs/db-backup.md`), restore into a scratch database, count the rows from query 4.
4. **Apply the migration as the schema owner** (the new tables and the trigger then belong to
   `health_owner`; running as the superuser also works, since the migration re-grants ownership):
   `docker compose run --rm --no-deps mycoach python -m mycoach.tools.migrate`.
   It is one transaction, so a refusal changes nothing.
5. **Verify (counts, history, ownership).**
   ```sql
   SELECT count(*) FROM health.canonical_activities;        -- = activities count from step 3
   SELECT count(*) FROM health.source_records;              -- same number
   SELECT count(*) FROM health.canonical_activity_sources;  -- same number
   SELECT count(*), sum(reps) FROM health.gym_workout_details;   -- unchanged
   SELECT source, count(*) FROM health.source_records GROUP BY 1; -- garmin/logger/hevy split = old data_source split
   SELECT tablename, tableowner FROM pg_tables WHERE schemaname='health' ORDER BY 1; -- all health_owner
   SET ROLE health_role; UPDATE health.source_records SET title = title; -- must fail: immutable
   RESET ROLE;
   SET ROLE coaching_role; SELECT 1 FROM health.source_records;          -- permission denied
   RESET ROLE;
   SET ROLE grafana_role; SELECT count(*) FROM health_analytics.activities; SELECT count(*) FROM health_analytics.gym_sets;
   RESET ROLE;
   ```
6. **Deploy the new image** (`docker compose up -d --build`). At startup the app mirrors the
   exercise catalogue into `health.exercises` (a no-op after the migration seeded it).
7. **Replay check (idempotency).** Start the app, then
   `POST /api/sources/sync/garmin?days=30` twice: the second response must report
   `activities_created: 0`. Re-push a logger workout (or re-sync the logger) and confirm
   `activities_created: 0`, `activities_skipped: 1`. Row counts in step 5 must not move except for
   genuinely new activities.
8. **Link Garmin onto gym activities** (the first run since #116 disabled it): `POST
   /api/sources/merge`. It returns the number linked; a second call returns 0. For each linked
   activity there must be two rows in `canonical_activity_sources` and both source records still
   present: `SELECT canonical_activity_id, array_agg(source) FROM health.canonical_activity_sources
   cs JOIN health.source_records s ON s.id = cs.source_record_id GROUP BY 1 HAVING count(*) > 1`.
9. **Smoke.** `GET /api/system/status` is 200; dashboard, history and plan pages load; log a test
   set from the logger and confirm the workout lands in `health.canonical_activities` and
   `health.gym_workout_details`, its source record in `health.source_records`, and its prescription
   in `coaching.prescription_fulfillments`. Check the Grafana dashboards still render.
10. **Keep the step 3 dump** until a few days of clean running.

**Rollback.** Stop the app, restore the step 3 dump into a fresh database, redeploy the previous
image. `alembic -c alembic_health.ini downgrade h184a1b2c3d4` exists and is tested, but it is lossy
for linked activities: a canonical activity that has both a Garmin and a gym source record returns
as `data_source = 'merged'` with the Garmin id, its Garmin raw payload dropped (the old model had
nowhere to keep it). Before step 8 nothing has been linked, so a downgrade then loses nothing.

## Details later tickets depend on

- #186 (module boundary) reads canonical activities and sets through the health module; the
  `athlete_id == user_id` convention and the bridge login are still its to retire.
- The `CanonicalActivityRecorded/Settled/Revised` events (#112) are not implemented here. The
  intake and linking functions return what an event dispatcher would need (`IntakeResult.created`,
  `LinkResult.linked`); `link_garmin_to_gym` is where a Garmin match settles a gym activity.
- `intake_source_record` is shaped to become the HTTP endpoint an n8n workflow calls (#106).
