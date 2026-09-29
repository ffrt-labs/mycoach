#!/bin/bash
# Scheduled backup loop for mycoach-db (ticket #161). Runs as the entrypoint of
# the mycoach-db-backup service (docker-compose.yml): a long-lived container,
# not a cron job, because the postgres image has no cron daemon and adding one
# is more moving parts than a sleep loop for a single daily job.
#
# Each run: pg_dump to a timestamped custom-format file, a restore check that
# actually restores it into a scratch database (not just `pg_restore --list`,
# which can pass on a dump that fails to restore), then prune dumps older than
# RETENTION_DAYS. PGHOST/PGUSER/PGPASSWORD/PGDATABASE come from the
# environment (docker-compose.yml) so no credentials appear in argv/`ps`.
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-/backups}
RETENTION_DAYS=${RETENTION_DAYS:-14}
BACKUP_TIME=${BACKUP_TIME:-03:00}
RESTORE_CHECK_DB=${RESTORE_CHECK_DB:-mycoach_restore_check}

mkdir -p "$BACKUP_DIR"

log() {
  echo "[backup] $(date -Iseconds) $*"
}

run_backup() {
  # Explicit `if`/`return` at each stage rather than leaning on `set -e`:
  # this function is invoked as `run_backup || log ...` below, and bash
  # suspends -e's abort-on-error behaviour for every command *inside* a
  # function called on the non-final side of an `||` — so a failing pg_dump
  # would silently fall through into the restore-check stage on a missing
  # or partial file instead of stopping here.
  local ts dest
  ts=$(date +%Y%m%d-%H%M%S)
  dest="$BACKUP_DIR/mycoach-${ts}.dump"

  log "starting pg_dump -> $dest"
  if ! pg_dump -Fc -f "$dest"; then
    log "pg_dump FAILED"
    rm -f "$dest"
    return 1
  fi

  # A dump that fails to restore is worse than no dump: it looks like a
  # backup exists until the day it's needed. Restoring into a scratch
  # database (dropped either way) catches that a `pg_restore --list` alone
  # would miss, since a truncated or corrupt archive can still list its TOC.
  log "restore check: restoring into scratch database $RESTORE_CHECK_DB"
  dropdb --if-exists "$RESTORE_CHECK_DB"
  createdb "$RESTORE_CHECK_DB"
  if pg_restore --no-owner --no-acl -d "$RESTORE_CHECK_DB" "$dest"; then
    log "restore check OK ($(du -h "$dest" | cut -f1))"
  else
    log "restore check FAILED for $dest"
    dropdb --if-exists "$RESTORE_CHECK_DB"
    return 1
  fi
  dropdb --if-exists "$RESTORE_CHECK_DB"

  # Pruning is cleanup, not the backup itself — a failure here must not turn
  # a successful, verified dump into a reported "run FAILED".
  find "$BACKUP_DIR" -maxdepth 1 -name 'mycoach-*.dump' -mtime "+${RETENTION_DAYS}" -print -delete \
    | sed 's/^/[backup] pruned /' \
    || log "pruning old dumps failed (non-fatal)"
}

seconds_until_next_run() {
  local now next
  now=$(date +%s)
  next=$(date -d "today ${BACKUP_TIME}" +%s)
  if [ "$next" -le "$now" ]; then
    next=$(date -d "tomorrow ${BACKUP_TIME}" +%s)
  fi
  echo $((next - now))
}

log "backup loop starting — daily at ${BACKUP_TIME}, retention ${RETENTION_DAYS}d, dir $BACKUP_DIR"
while true; do
  sleep_seconds=$(seconds_until_next_run)
  log "sleeping ${sleep_seconds}s until next run"
  sleep "$sleep_seconds"
  run_backup || log "run FAILED"
done
