"""The life context episodes migration (c220a1b2c3d4, #220) applies and rolls back on SQLite."""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
REVISION = "c220a1b2c3d4"
PREVIOUS = "c215a1b2c3d4"


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


def _tables(db_path: Path) -> set[str]:
    return {r[0] for r in _rows(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}


def test_upgrade_creates_the_episode_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "episodes.db"
    command.upgrade(_alembic_config(db, monkeypatch), REVISION)

    columns = {r[1] for r in _rows(db, "PRAGMA table_info(life_context_episodes)")}
    assert columns == {
        "id", "user_id", "kind", "started_on", "ended_on", "note", "severity",
        "created_at", "updated_at",
    }  # fmt: skip


def test_downgrade_drops_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "episodes.db"
    cfg = _alembic_config(db, monkeypatch)
    command.upgrade(cfg, REVISION)
    command.downgrade(cfg, PREVIOUS)

    assert "life_context_episodes" not in _tables(db)
    assert "planned_sessions" in _tables(db)
