"""Tests for the per-source health snapshot uniqueness migration (b3c4d5e6f7a8)."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """Build an Alembic config pointed at a throwaway sqlite db."""
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _seed_user_and_snapshot(db_path: Path) -> None:
    """Insert a user and a single garmin snapshot for 2026-06-10."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        now = datetime.utcnow().isoformat()
        cur.execute(
            "INSERT INTO users "
            "(id, name, email, fitness_level, created_at, updated_at, "
            "email_daily_briefing, email_weekly_plan, email_post_workout, "
            "email_sleep_coaching, email_weekly_recap) "
            "VALUES (1, 'A', 'a@b.com', 'intermediate', ?, ?, 0, 0, 0, 0, 0)",
            (now, now),
        )
        cur.execute(
            "INSERT INTO daily_health_snapshots "
            "(user_id, snapshot_date, data_source, created_at) "
            "VALUES (1, '2026-06-10', 'garmin', ?)",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()


def test_upgrade_preserves_existing_rows_and_allows_second_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrading keeps existing rows and lets a second source write the same date."""
    db_path = tmp_path / "upgrade.db"
    cfg = _alembic_config(db_path, monkeypatch)

    command.upgrade(cfg, "a2b3c4d5e6f7")
    _seed_user_and_snapshot(db_path)
    command.upgrade(cfg, "b3c4d5e6f7a8")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT user_id, snapshot_date, data_source FROM daily_health_snapshots"
        ).fetchall()
        assert rows == [(1, "2026-06-10", "garmin")]

        now = datetime.utcnow().isoformat()
        # Second source, same user+date: must succeed now.
        conn.execute(
            "INSERT INTO daily_health_snapshots "
            "(user_id, snapshot_date, data_source, created_at) "
            "VALUES (1, '2026-06-10', 'hevy', ?)",
            (now,),
        )
        conn.commit()

        rows = conn.execute(
            "SELECT data_source FROM daily_health_snapshots ORDER BY data_source"
        ).fetchall()
        assert rows == [("garmin",), ("hevy",)]

        # Same source, same user+date: must still conflict.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO daily_health_snapshots "
                "(user_id, snapshot_date, data_source, created_at) "
                "VALUES (1, '2026-06-10', 'garmin', ?)",
                (now,),
            )
    finally:
        conn.close()


def test_downgrade_restores_single_column_uniqueness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Downgrading restores the original snapshot_date-only uniqueness constraint."""
    db_path = tmp_path / "downgrade.db"
    cfg = _alembic_config(db_path, monkeypatch)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "a2b3c4d5e6f7")

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_health_snapshots)")}
        assert "data_source" in columns

        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO users "
            "(id, name, email, fitness_level, created_at, updated_at, "
            "email_daily_briefing, email_weekly_plan, email_post_workout, "
            "email_sleep_coaching, email_weekly_recap) "
            "VALUES (1, 'A', 'a@b.com', 'intermediate', ?, ?, 0, 0, 0, 0, 0)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO daily_health_snapshots "
            "(user_id, snapshot_date, data_source, created_at) "
            "VALUES (1, '2026-06-10', 'garmin', ?)",
            (now,),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO daily_health_snapshots "
                "(user_id, snapshot_date, data_source, created_at) "
                "VALUES (1, '2026-06-10', 'hevy', ?)",
                (now,),
            )
    finally:
        conn.close()
