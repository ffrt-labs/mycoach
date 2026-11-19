"""The travel columns migration (c221a1b2c3d4, #221) applies and rolls back on SQLite."""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
REVISION = "c221a1b2c3d4"
PREVIOUS = "c220a1b2c3d4"
TRAVEL_COLUMNS = {"equipment", "tz_shift_hours", "no_training"}


def _alembic_config(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(life_context_episodes)")}
    finally:
        conn.close()


def test_upgrade_adds_the_travel_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "travel.db"
    command.upgrade(_alembic_config(db, monkeypatch), REVISION)

    assert _columns(db) >= TRAVEL_COLUMNS


def test_downgrade_drops_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "travel.db"
    cfg = _alembic_config(db, monkeypatch)
    command.upgrade(cfg, REVISION)
    command.downgrade(cfg, PREVIOUS)

    assert not TRAVEL_COLUMNS & _columns(db)
    assert "severity" in _columns(db)
