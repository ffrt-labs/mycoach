"""The Garmin weight-push columns migration (h199a1b2c3d4) on SQLite (ticket #199)."""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from mycoach.tools import migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
H198 = "h198a1b2c3d4"
H199 = "h199a1b2c3d4"
PUSH_COLUMNS = {
    "garmin_push_status",
    "garmin_weigh_in_id",
    "garmin_push_error",
    "garmin_push_attempts",
}


def _health(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic_health.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic_health"))
    return cfg


def _exec(db_path: Path, sql: str) -> list[tuple]:  # type: ignore[type-arg]
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(sql).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _columns(db_path: Path, table: str) -> set[str]:
    return {r[1] for r in _exec(db_path, f"PRAGMA table_info({table})")}


def test_upgrade_adds_the_push_columns_and_queues_existing_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "push.db"
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db}")
    migrate.main([])
    cfg = _health(db, monkeypatch)
    command.downgrade(cfg, H198)
    assert not PUSH_COLUMNS & _columns(db, "body_measurements")
    for weight, waist in (("82.4", "NULL"), ("NULL", "91.0")):
        _exec(
            db,
            "INSERT INTO body_measurements "
            "(athlete_id, measured_at, weight_kg, waist_cm, source, created_at) "
            f"VALUES (1, '2026-11-10 07:00:00', {weight}, {waist}, 'manual', "
            "'2026-11-10 07:00:00')",
        )

    command.upgrade(cfg, H199)

    assert _columns(db, "body_measurements") >= PUSH_COLUMNS
    rows = _exec(
        db,
        "SELECT weight_kg, garmin_push_status, garmin_push_attempts "
        "FROM body_measurements ORDER BY id",
    )
    # A weight already logged is owed to Garmin; a waist-only reading never goes there.
    assert rows == [(82.4, "pending", 0), (None, None, 0)]


def test_downgrade_removes_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "push.db"
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db}")
    migrate.main([])
    cfg = _health(db, monkeypatch)

    command.downgrade(cfg, H198)

    assert not PUSH_COLUMNS & _columns(db, "body_measurements")
    command.upgrade(cfg, H199)
    assert _columns(db, "body_measurements") >= PUSH_COLUMNS
