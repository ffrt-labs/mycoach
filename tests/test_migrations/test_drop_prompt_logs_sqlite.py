"""The prompt_logs retirement migration (c180a1b2c3d4, #180) applies and rolls back on SQLite.

Langfuse is the record of coaching calls; the rows were dumped before the drop.
Downgrade brings back an empty table of the same shape.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
REVISION = "c180a1b2c3d4"
PREVIOUS = "c243a1b2c3d4"

COLUMNS = [
    ("id", "INTEGER", 1),
    ("prompt_type", "VARCHAR(50)", 1),
    ("prompt_version", "VARCHAR(10)", 1),
    ("model", "VARCHAR(100)", 1),
    ("input_tokens", "INTEGER", 0),
    ("output_tokens", "INTEGER", 0),
    ("latency_ms", "INTEGER", 0),
    ("estimated_cost_usd", "FLOAT", 0),
    ("prompt_text", "TEXT", 0),
    ("response_text", "TEXT", 0),
    ("success", "BOOLEAN", 1),
    ("error", "TEXT", 0),
    ("created_at", "DATETIME", 1),
]


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


@pytest.fixture
def before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "prompt_logs.db"
    command.upgrade(_alembic_config(db_path, monkeypatch), PREVIOUS)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO prompt_logs (prompt_type, prompt_version, model, success, created_at) "
            "VALUES ('daily_briefing', 'lf:3', 'm', 1, '2026-11-20')"
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_upgrade_drops_the_table(before: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    command.upgrade(_alembic_config(before, monkeypatch), REVISION)

    assert "prompt_logs" not in _tables(before)


def test_downgrade_recreates_an_empty_table_of_the_same_shape(
    before: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _alembic_config(before, monkeypatch)
    command.upgrade(cfg, REVISION)
    command.downgrade(cfg, PREVIOUS)

    columns = [(r[1], r[2], r[3]) for r in _rows(before, "PRAGMA table_info(prompt_logs)")]
    assert columns == COLUMNS
    assert _rows(before, "SELECT id FROM prompt_logs") == []
