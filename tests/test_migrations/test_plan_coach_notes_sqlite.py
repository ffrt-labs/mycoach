"""The plan Coach notes migration (c243a1b2c3d4, #243) applies and rolls back on SQLite.

Older plans keep null Coach notes: there is no backfill.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
REVISION = "c243a1b2c3d4"
PREVIOUS = "c221a1b2c3d4"


def _alembic_config(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _rows(db_path: Path, sql: str) -> list[tuple]:  # type: ignore[type-arg]
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _columns(db_path: Path) -> set[str]:
    return {r[1] for r in _rows(db_path, "PRAGMA table_info(weekly_plans)")}


@pytest.fixture
def before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "coach_notes.db"
    command.upgrade(_alembic_config(db_path, monkeypatch), PREVIOUS)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO users "
            "(id, name, email, fitness_level, created_at, updated_at, "
            "email_daily_briefing, email_weekly_plan, email_post_workout, "
            "email_sleep_coaching, email_weekly_recap) "
            "VALUES (1, 'A', 'a@b.com', 'intermediate', '2026-11-02', '2026-11-02', "
            "0, 0, 0, 0, 0)"
        )
        conn.execute(
            "INSERT INTO weekly_plans (id, user_id, week_start, prompt_version, status, "
            "created_at) VALUES (1, 1, '2026-11-02', 'v3', 'active', '2026-11-01')"
        )
        conn.execute(
            "INSERT INTO planned_sessions "
            "(plan_id, day_of_week, sport, title, completed, track) "
            "VALUES (1, 0, 'running', 'Easy Run', 0, 'cardio')"
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_upgrade_adds_null_coach_notes_to_existing_plans(
    before: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command.upgrade(_alembic_config(before, monkeypatch), REVISION)

    assert "coach_notes" in _columns(before)
    assert _rows(before, "SELECT id, coach_notes FROM weekly_plans") == [(1, None)]


def test_downgrade_drops_the_coach_notes(before: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _alembic_config(before, monkeypatch)
    command.upgrade(cfg, REVISION)
    command.downgrade(cfg, PREVIOUS)

    assert "coach_notes" not in _columns(before)
    assert len(_rows(before, "SELECT id FROM weekly_plans")) == 1
