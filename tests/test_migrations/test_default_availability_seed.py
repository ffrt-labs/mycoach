"""Tests for the default_availability seed migration (a2b3c4d5e6f7)."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    # alembic/env.py always overrides sqlalchemy.url from mycoach.config.get_settings(),
    # so the DB target has to be steered via the same env var the app reads.
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _seed_user_and_availability(db_path: Path) -> None:
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
        # An earlier one-off reshuffle, followed by the actual standing pattern.
        rows = [
            (1, "2026-03-09", 0, "padel"),
            (1, "2026-07-13", 0, "gym"),
            (1, "2026-07-13", 1, "swimming"),
            (1, "2026-07-13", 2, "gym"),
            (1, "2026-07-13", 3, "swimming"),
            (1, "2026-07-13", 6, "running"),
        ]
        cur.executemany(
            "INSERT INTO weekly_availabilities (user_id, week_start, day_of_week, sport) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_seed_uses_most_recently_declared_week(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "seed.db"
    cfg = _alembic_config(db_path, monkeypatch)

    command.upgrade(cfg, "f6a7b8c9d0e1")
    _seed_user_and_availability(db_path)
    command.upgrade(cfg, "a2b3c4d5e6f7")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT day_of_week, sport FROM default_availability WHERE user_id = 1 "
            "ORDER BY day_of_week"
        ).fetchall()
    finally:
        conn.close()

    assert rows == [
        (0, "gym"),
        (1, "swimming"),
        (2, "gym"),
        (3, "swimming"),
        (6, "running"),
    ]


def test_seed_with_no_declared_weeks_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "empty.db"
    cfg = _alembic_config(db_path, monkeypatch)

    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM default_availability").fetchall()
    finally:
        conn.close()

    assert rows == []


def test_downgrade_drops_table_and_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "downgrade.db"
    cfg = _alembic_config(db_path, monkeypatch)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "f6a7b8c9d0e1")

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        columns = {row[1] for row in conn.execute("PRAGMA table_info(weekly_plans)").fetchall()}
    finally:
        conn.close()

    assert "default_availability" not in tables
    assert "availability_source" not in columns
