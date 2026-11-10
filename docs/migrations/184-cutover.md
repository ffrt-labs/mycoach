# Health/coaching schema seam: audit, what was built, human cutover (map #85, ticket #184)

Implements the design decided in #108. Nothing here has been applied to the real database:
the code, migrations and tests are in the branch; steps 1-9 below are for a human on the
homelab host.

## 1. Audit: what #117 actually created

Derived from the Alembic history in this repo (`alembic/versions/`), not from the live
database, which the implementation work never touched. Verify against production with the
queries in [Pre-flight](#pre-flight) before the cutover.

| Question | Answer | Evidence |
| --- | --- | --- |
| Does `health` exist? | No | no migration creates it before this ticket |
| Does `coaching` exist? | No | same |
| Does `health_analytics` exist? | Yes, three views | `7cf0523de01b` (#117) |
| Owner/runtime roles (`health_owner`, `coaching_owner`, `health_role`, `coaching_role`)? | No | `7cf0523de01b`'s docstring says so: the split "hasn't shipped yet" |
| `grafana_role`? | Yes, `NOLOGIN`, no password; `USAGE` on `health_analytics` and `SELECT` on its tables; default privileges `FOR ROLE mycoach` | `7cf0523de01b` |
| What do the views read? | `public.daily_health_snapshots` (`daily_metrics`), `public.activities` (`activities`), `public.gym_workout_details` joined to `public.activities` (`gym_sets`). All in `public`, owned by the `mycoach` login. | `7cf0523de01b` view DDL |
| Does a view expose `prescribed_*`? | Yes, `gym_sets` exposes `prescribed_weight_kg` and `prescribed_reps` | same |
| Number of Alembic histories | One (`alembic/`, one `alembic_version` in `public`), chained through health- and coaching-side migrations | `7cf0523de01b` docstring |
| How was production Postgres built? | `Base.metadata.create_all` from the models of the day, stamped at that head, not by replaying migrations (`743e264deef9` cannot run on Postgres). Everything in `public`. | `docs/postgres-cutover.md` caveat, `tools/sqlite_to_postgres.py` |
| Tailnet port | `mycoach-db` publishes 5432 on `${MYCOACH_DB_TAILNET_IP}` | `docker-compose.yml` (#117) |

Also found while auditing: `7cf0523de01b` is not the coaching head. `a7c41e9d2b56` (prompt
provenance, #174) revises it and is the true pre-seam head; the new coaching revision chains off that.

## 2. What was built

**Roles and schemas** (`src/mycoach/db_roles.py`, `python -m mycoach.tools.db_bootstrap`):

- `health` and `coaching` schemas; `health_owner` / `coaching_owner` own them and run their
  migrations; `health_role` / `coaching_role` are DML-only (`SELECT, INSERT, UPDATE, DELETE`
  on tables, `USAGE, SELECT` on sequences; never `CREATE`/`ALTER`/`DROP`). All four are
  `NOLOGIN`; logins and passwords are a human step (step 5).
- `REVOKE ALL ON SCHEMA ... FROM PUBLIC` on both. `coaching_role` holds no grant on `health`,
  not even `USAGE`; `health_role` holds none on `coaching`. A cross-schema query fails with
  `permission denied for schema health`.

**Two Alembic histories**, each with its own version table:

| History | Directory / ini | Version table | Runs as | Revision |
| --- | --- | --- | --- | --- |
| coaching | `alembic/`, `alembic.ini` | `public.alembic_version` (legacy stamp, now owned by `coaching_owner`) | `coaching_owner` (`MYCOACH_COACHING_OWNER_DB_URL`) | `c184a1b2c3d4` |
| health | `alembic_health/`, `alembic_health.ini` | `health.alembic_version` (SQLite: `alembic_version_health`) | `health_owner` (`MYCOACH_HEALTH_OWNER_DB_URL`) | `h184a1b2c3d4` |

`python -m mycoach.tools.migrate` runs coaching, then health (the container CMD now does
this). The one-time cutover must run coaching first: health refuses to drop `prescribed_*`
until `coaching.prescription_fulfillments` holds every one of them, and refuses outright if
any prescribed set belongs to a workout with no external id (it cannot be keyed).

**`c184a1b2c3d4` (coaching):** moves the 13 coaching tables into `coaching`, drops the FKs from
`planned_sessions.activity_id` and `coaching_insights.activity_id` to `activities` (kept as
plain integers), creates `coaching.prescription_fulfillments`, copies every existing
`gym_workout_details.prescribed_*` into it, adds `users.health_athlete_id` (= `users.id`).

**`h184a1b2c3d4` (health):** moves `activities`, `gym_workout_details`,
`daily_health_snapshots`, `data_source_configs` into `health`, drops their FKs to `users`,
creates `health.athletes` (seeded from the distinct ids in use, so every id survives), renames
`user_id` to `athlete_id`, drops `gym_workout_details.prescribed_weight_kg/prescribed_reps`,
and rebuilds the three `health_analytics` views over `health.*` (owned by `health_owner`,
`grafana_role` re-granted).

**App:** `PrescriptionFulfillment` and `Athlete` models; every model names its schema
(SQLite engines translate the schemas away, so the test suite and local runs keep a single
flat database); the importer writes the fulfillment rows in the request that records the
workout (`coaching/prescriptions.py`); post-workout context, the weekly recap context and the
adherence scores read the prescription from there; creating the profile issues the athlete.

### Deviations from #108, and why

1. **The fulfillment key is `(activity_external_id, exercise_title, set_index)`, not
   `(activity_external_id, set_index)`.** The logger numbers sets from 1 within each exercise
   (`static/logger/app.js`, `set_index: i + 1`), so the two-column key collides across
   exercises of one workout. A workout that repeats the same exercise in two separate blocks
   still collides; the first block's prescription wins (idempotent insert).
2. **Runtime roles also get `DELETE`.** #108 listed `SELECT/INSERT/UPDATE`; the app deletes
   plans, insights, availability, routines and sport profiles (coaching) and, through the
   dormant merger and the cleanup tool, activities (health).
3. **One bridge login for the app, for now.** The unchanged app still joins health and coaching
   in SQL (`plan_linking`, `coaching/context.py`, the history page), which `coaching_role`
   alone cannot do by design. Until the in-process health module (#186) lets each side hold
   only its own role, the app connects as one login that is a member of both `health_role` and
   `coaching_role`. `coaching_role` itself still has zero grants on `health`; the bridge login
   is the thing to retire in #186. The permission error is verified against `coaching_role`
   directly (`tests/test_migrations/test_schema_seam_postgres.py`).
4. **`athlete_id` still equals the user id.** The health-side queries keep their pre-seam
   `Model.user_id` attribute (now mapped to the `athlete_id` column); `users.health_athlete_id`
   is stored but not yet used to translate. Creating the profile issues the athlete with the
   same id. #186 replaces this with the real mapping.
5. **`health_analytics.gym_sets` loses `prescribed_weight_kg` / `prescribed_reps`.** They are
   no longer observed facts and cannot be read from `health`. This is the one break of #113's
   additive-only rule; the views also gain `athlete_id` and keep `user_id`.
   Any Grafana panel using those two columns breaks (see pre-flight).
6. **`GymWorkoutDetailRead`/`Create` no longer carry `prescribed_*`** (the activities API stops
   exposing them; the wire format of the logger's workout POST is unchanged).
7. **The coaching version table stays in `public`** (the legacy stamp lives there), owned by
   `coaching_owner`; moving it would race the migration that records its own completion.
8. **Owner roles need `LOGIN`** to run migrations as themselves (step 5), so that objects they
   create get the default privileges for the runtime roles.

## 3. Verified

- SQLite (suite default): 1000+ tests, including `tests/test_migrations/test_schema_seam_sqlite.py`
  (both histories, the prescription copy, athlete seeding, refusal to run health first, refusal on
  unkeyable prescriptions, downgrade) and `test_migrate_tool.py`.
- Postgres 16 (set `MYCOACH_TEST_PG_URL` to a superuser URL; skipped otherwise):
  `tests/test_migrations/test_schema_seam_postgres.py` builds a pre-seam database, runs
  bootstrap and both histories, and checks table placement, ownership, version tables,
  `coaching_role` permission errors (direct and via a cross-schema join), DML-only runtime roles,
  no FK across the seam, the rebuilt views and `grafana_role`, the unchanged app's importer and
  context code through the bridge login, downgrade, and the fresh-install path.

Not verified: any run against the production database or a restored production dump.

## Pre-flight

Run against production (read-only) before touching anything:

```sql
-- Audit: confirm section 1.
SELECT nspname FROM pg_namespace WHERE nspname IN ('health','coaching','health_analytics');
SELECT rolname FROM pg_roles WHERE rolname IN
  ('health_owner','coaching_owner','health_role','coaching_role','grafana_role');
SELECT version_num FROM public.alembic_version;                -- expect a7c41e9d2b56
SELECT schemaname, count(*) FROM pg_tables
  WHERE schemaname NOT IN ('pg_catalog','information_schema') GROUP BY 1;

-- The drop is refused if either of these is non-zero for unkeyable rows.
SELECT count(*) FROM gym_workout_details d JOIN activities a ON a.id = d.activity_id
 WHERE (d.prescribed_weight_kg IS NOT NULL OR d.prescribed_reps IS NOT NULL)
   AND a.external_id IS NULL;                                   -- expect 0
```

If the last count is not 0, decide what to do with those rows before the cutover; the health
migration will refuse to run rather than lose them. Also check which Grafana panels read
`health_analytics.gym_sets.prescribed_*`.

## Human cutover checklist

Precondition (same as #104/#107): every cached/pending logger session is drained and synced.
Unsynced IndexedDB sessions arriving mid-migration are how gym history goes missing.

1. **Drain.** Confirm no pending sessions on the installed logger devices; then stop writes:
   `docker compose stop mycoach mycoach-logger`. Leave `mycoach-db` up.
2. **Back up and prove the backup.** Take an on-demand dump (the `mycoach-db-backup` sidecar,
   `docs/db-backup.md`) and confirm it restores into a scratch database.
3. **Pre-flight queries** above. Stop here on any surprise.
4. **Bootstrap roles and schemas** as the instance superuser (`mycoach`), from the new image:
   `docker compose run --rm --no-deps mycoach python -m mycoach.tools.db_bootstrap --url "postgresql+asyncpg://mycoach:${MYCOACH_DATABASE_PASSWORD}@mycoach-db/mycoach"`.
   (`--print` shows the SQL without running it.)
5. **Run both histories once, as the superuser** (the cutover moves tables the owners do not own
   yet), with the owner URLs empty so the app falls back to `MYCOACH_DB_URL`:
   `docker compose run --rm --no-deps -e MYCOACH_DB_URL="postgresql+asyncpg://mycoach:${MYCOACH_DATABASE_PASSWORD}@mycoach-db/mycoach" -e MYCOACH_COACHING_OWNER_DB_URL= -e MYCOACH_HEALTH_OWNER_DB_URL= mycoach python -m mycoach.tools.migrate`.
   It is one transaction per history; a refusal (missing fulfillments, unkeyable prescriptions)
   changes nothing.
6. **Create logins and set secrets** (never in a migration; follow `homelab-host/SECRETS.md`):
   ```sql
   ALTER ROLE health_owner   LOGIN PASSWORD '<secret>';
   ALTER ROLE coaching_owner LOGIN PASSWORD '<secret>';
   CREATE ROLE mycoach_app LOGIN PASSWORD '<secret>';
   GRANT health_role, coaching_role TO mycoach_app;   -- the interim bridge, see deviation 3
   ALTER ROLE grafana_role LOGIN PASSWORD '<secret>'; -- only if not already done for #117
   ```
   Set deploy secrets: `MYCOACH_RUNTIME_DB_URL` (login `mycoach_app`),
   `MYCOACH_COACHING_OWNER_DB_URL` (login `coaching_owner`), `MYCOACH_HEALTH_OWNER_DB_URL`
   (login `health_owner`), all `postgresql+asyncpg://...@mycoach-db/mycoach`.
7. **Deploy the new image** (`docker compose up -d --build`). Container start runs
   `python -m mycoach.tools.migrate` as the owners: a no-op now.
8. **Verify.**
   ```sql
   -- as superuser:
   SET ROLE coaching_role; SELECT * FROM health.activities;   -- permission denied for schema health
   RESET ROLE;
   SET ROLE health_role;   SELECT * FROM coaching.users;       -- permission denied for schema coaching
   RESET ROLE;
   SELECT count(*) FROM coaching.prescription_fulfillments;   -- equals the prescribed-set count from step 3
   ```
   Then `GET /api/system/status` is 200, the dashboard/history/plan pages load, log a test set from
   the logger with a plan and confirm both the set (`health.gym_workout_details`) and its
   prescription (`coaching.prescription_fulfillments`) land, and the Grafana dashboards still render
   (fix any panel that used `gym_sets.prescribed_*`).
9. **Keep the pre-cutover dump** until a few days of clean running.

Rollback: stop the app, restore the step-2 dump into a fresh database, redeploy the previous
image. (Alembic downgrades exist and are tested, health first then coaching, but a restore is
the safe path for production data.)

## Details later tickets depend on

- Roles: `health_owner`/`coaching_owner` (DDL, migrations), `health_role`/`coaching_role`
  (DML-only), `mycoach_app` (interim bridge login, member of both runtime roles), `grafana_role`
  (`health_analytics` only).
- Connection settings: `MYCOACH_DB_URL` (runtime), `MYCOACH_COACHING_OWNER_DB_URL`,
  `MYCOACH_HEALTH_OWNER_DB_URL` (`config.py`).
- #185 (source records/canonical activities) adds a health-history revision and must keep
  `health_analytics` views additive; #186 (module boundary) removes the bridge login and the
  `athlete_id == user_id` convention and replaces `planned_sessions.activity_id` /
  `coaching_insights.activity_id` (integers held by value) with external ids.
- `tools/sqlite_to_postgres.py` is the historical #107 engine-swap copy. It builds the schema
  from the current models and is not valid after #184; do not re-run it.
