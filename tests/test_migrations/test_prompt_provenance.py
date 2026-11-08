"""The prompt provenance migration (a7c41e9d2b56, #174) applies and rolls back."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
REVISION = "a7c41e9d2b56"
PREVIOUS = "7cf0523de01b"


def _alembic_config(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _columns(db_path: Path, table: str) -> dict[str, str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1]: row[2] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _seed(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
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
            "INSERT INTO coaching_insights "
            "(user_id, insight_date, insight_type, content, prompt_version, created_at) "
            "VALUES (1, '2026-11-02', 'daily_briefing', '{}', 'v2', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO weekly_plans (user_id, week_start, prompt_version, status, created_at) "
            "VALUES (1, '2026-11-02', 'v2', 'active', ?)",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()


def test_upgrade_widens_the_insight_version_and_adds_plan_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "upgrade.db"
    cfg = _alembic_config(db_path, monkeypatch)
    command.upgrade(cfg, PREVIOUS)
    _seed(db_path)

    command.upgrade(cfg, REVISION)

    assert _columns(db_path, "coaching_insights")["prompt_version"] == "VARCHAR(20)"
    plans = _columns(db_path, "weekly_plans")
    assert plans["gym_prompt_version"] == "VARCHAR(20)"
    assert plans["cardio_prompt_version"] == "VARCHAR(20)"
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT prompt_version FROM coaching_insights").fetchall() == [("v2",)]
        assert conn.execute(
            "SELECT gym_prompt_version, cardio_prompt_version FROM weekly_plans"
        ).fetchall() == [(None, None)]
    finally:
        conn.close()


def test_downgrade_restores_the_previous_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "downgrade.db"
    cfg = _alembic_config(db_path, monkeypatch)
    command.upgrade(cfg, REVISION)
    _seed(db_path)

    command.downgrade(cfg, PREVIOUS)

    assert _columns(db_path, "coaching_insights")["prompt_version"] == "VARCHAR(10)"
    plans = _columns(db_path, "weekly_plans")
    assert "gym_prompt_version" not in plans
    assert "cardio_prompt_version" not in plans
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT prompt_version FROM coaching_insights").fetchall() == [("v2",)]
    finally:
        conn.close()
