"""The body measurements migration (h198a1b2c3d4) on SQLite (ticket #198)."""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from mycoach.tools import migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
H197 = "h197a1b2c3d4"  # the revision before h198
H198 = "h198a1b2c3d4"


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


def _migrated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "body.db"
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db}")
    migrate.main([])
    return db


def test_upgrade_adds_the_body_measurements_table_and_height(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _migrated(tmp_path, monkeypatch)

    assert _columns(db, "body_measurements") == {
        "id",
        "athlete_id",
        "measured_at",
        "weight_kg",
        "waist_cm",
        "note",
        "source",
        "created_at",
    }
    assert "height_cm" in _columns(db, "athletes")
    _exec(
        db,
        "INSERT INTO body_measurements (athlete_id, measured_at, weight_kg, source, created_at) "
        "VALUES (1, '2026-11-10 07:00:00', 82.4, 'manual', '2026-11-10 07:00:00')",
    )


def test_a_row_without_weight_or_waist_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _migrated(tmp_path, monkeypatch)

    with pytest.raises(sqlite3.IntegrityError):
        _exec(
            db,
            "INSERT INTO body_measurements (athlete_id, measured_at, source, created_at) "
            "VALUES (1, '2026-11-10 07:00:00', 'manual', '2026-11-10 07:00:00')",
        )


def test_downgrade_removes_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _migrated(tmp_path, monkeypatch)
    cfg = _health(db, monkeypatch)

    command.downgrade(cfg, H197)

    assert _columns(db, "body_measurements") == set()
    assert "height_cm" not in _columns(db, "athletes")
    command.upgrade(cfg, H198)
    assert "height_cm" in _columns(db, "athletes")
