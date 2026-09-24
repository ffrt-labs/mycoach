# SQLite → Postgres cutover runbook

Engine swap for map #85 / ticket #107: a faithful copy, no model changes. Run on the
homelab host, in the deployed `mycoach` checkout.

## Preconditions

- Every cached/pending logger session is drained and synced (same as #104). Unsynced
  IndexedDB sessions arriving mid-migration are how gym history goes missing.
- `MYCOACH_DATABASE_PASSWORD` is set (it is a deploy secret) and `mycoach-db` is healthy:
  `docker compose ps mycoach-db`.

## Steps

1. **Back up SQLite.** With the app stopped (step 2), copy the file out of the volume:
   `docker compose run --rm --no-deps -v $PWD:/backup mycoach cp /data/mycoach.db /backup/mycoach.db.pre-postgres`
2. **Stop the app** so nothing writes during the copy: `docker compose stop mycoach mycoach-logger`.
3. **Copy.** The DB has no published port, so run the tool inside the app image, on the
   compose network, with the `/data` volume mounted:
   ```
   docker compose run --rm --no-deps mycoach python -m mycoach.tools.sqlite_to_postgres \
     --source sqlite+aiosqlite:////data/mycoach.db \
     --target "postgresql+asyncpg://mycoach:${MYCOACH_DATABASE_PASSWORD}@mycoach-db/mycoach"
   ```
   The tool builds the schema from the models, copies every table in FK order, resets serial
   sequences, stamps `alembic_version` at head, and prints a per-table row-count comparison.
   It exits non-zero on any mismatch and refuses a non-empty target. To retry after a failed
   run: `docker compose exec mycoach-db psql -U mycoach -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'`.
4. **Switch.** Set the `MYCOACH_RUNTIME_DB_URL` deploy secret to
   `postgresql+asyncpg://mycoach:<password>@mycoach-db/mycoach` and redeploy. Container start
   runs `alembic upgrade head`, a no-op since the copy is stamped at head.
5. **Verify.** Row counts matched in step 3; `GET /api/system/status` is 200; open the
   dashboard, history and plan pages; log a test set from the logger and confirm it lands.
6. **Keep the SQLite file** (and the step-1 backup) until a few days of clean running, then
   flip the compose default in a follow-up commit.

## Rollback

Clear `MYCOACH_RUNTIME_DB_URL` and redeploy. The app returns to the untouched SQLite file. Any
writes made on Postgres since the switch are not carried back.

## Caveat

Fresh Postgres databases are built from the models, not by replaying the migration history:
migration `743e264deef9` probes `sqlite_master`/`PRAGMA` and cannot run on Postgres. Future
migrations run on Postgres normally.
