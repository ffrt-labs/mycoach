# mycoach-db backup

Ticket #161: `mycoach-db` (Postgres, holding real health/coaching history since
#107) had no backup mechanism at all. This closes the immediate gap — a
scheduled dump recoverable from, not a full homelab backup architecture.
Broader concerns (offsite/3-2-1, retention policy, other apps' volumes) are
out of scope; see #85 for that wider map.

## What runs

`mycoach-db-backup` (docker-compose.yml) is a long-lived container built from
the same `postgres:16` image as `mycoach-db` — matching client and server
major versions exactly, which `pg_dump`/`pg_restore` require, without tracking
the Postgres version in a second place (the app's own `Dockerfile`). It runs
`scripts/backup-mycoach-db.sh` as its entrypoint: a sleep loop rather than a
cron entry, since the postgres image has no cron daemon.

Once a day (`MYCOACH_BACKUP_TIME`, default `03:00`, container's local time):

1. `pg_dump -Fc` writes a timestamped custom-format dump to `/backups`
   (`mycoach-db-backups` volume), e.g. `mycoach-20260929-030000.dump`.
2. **Restore check.** The dump is restored into a scratch database
   (`mycoach_restore_check`, dropped either way) with `pg_restore`. A
   `pg_restore --list` alone was rejected — it validates the archive's table
   of contents, which a truncated or otherwise corrupt dump can still produce;
   only an actual restore catches that the dump doesn't come back.
3. Dumps older than `MYCOACH_BACKUP_RETENTION_DAYS` (default 14) are deleted.

Progress and failures are logged to the container's stdout
(`docker compose logs -f mycoach-db-backup`); a failed run logs and the loop
continues to the next scheduled attempt rather than exiting, since a crashed
backup container is a bigger outage than a missed one.

## Where dumps live

The `mycoach-db-backups` Docker volume, mounted at `/backups` in the backup
container. This is durable across `docker compose down`/`up` and container
recreation, but it is **not offsite** — a lost or corrupted host still loses
both `mycoach-db-data` and `mycoach-db-backups` together. Copying dumps
off-box is out of scope for #161 (see #85).

List current dumps:

```
docker compose exec mycoach-db-backup ls -lh /backups
```

Copy one out to the host:

```
docker compose cp mycoach-db-backup:/backups/mycoach-20260929-030000.dump ./
```

## Restoring

**Into a fresh/empty `mycoach-db`** (disaster recovery — the volume is gone or
corrupt):

```
docker compose up -d mycoach-db
docker compose cp ./mycoach-20260929-030000.dump mycoach-db-backup:/tmp/restore.dump
docker compose exec mycoach-db-backup pg_restore --no-owner --no-acl -d mycoach /tmp/restore.dump
```

**Into a scratch database first**, to inspect a dump without touching
production — this is exactly what the nightly restore check already does; do
the same manually for an older dump:

```
docker compose exec mycoach-db-backup sh -c \
  'dropdb --if-exists scratch && createdb scratch && pg_restore --no-owner --no-acl -d scratch /backups/mycoach-20260929-030000.dump'
```

`mycoach` and `mycoach-logger` should be stopped before restoring into the
live `mycoach` database — the app writes concurrently and a restore doesn't
coordinate with that.

## Verifying it's working

- `docker compose logs mycoach-db-backup | tail -50` — the loop logs both the
  next sleep and each run's outcome, including the restore check.
- `docker compose exec mycoach-db-backup ls -lh /backups` — a dump from within
  the last day, growing with the database, is the healthy state.
