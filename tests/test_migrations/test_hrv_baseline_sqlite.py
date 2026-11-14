"""The HRV baseline band migration (h197a1b2c3d4) on SQLite (ticket #197).

Upgrades to the layout before it, seeds snapshots whose stored ``raw_data`` carries (or
lacks) Garmin's ``hrvSummary.baseline``, upgrades, and checks the backfill. The Postgres
half is test_hrv_baseline_postgres.py.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "h186a1b2c3d4"
H197 = "h197a1b2c3d4"
BAND_COLUMNS = ("hrv_baseline_low", "hrv_baseline_high", "hrv_baseline_low_upper")

# (snapshot_date, raw_data) -> the band the backfill must find.
SEEDED: list[tuple[str, str | None, tuple[float | None, float | None, float | None]]] = [
    (
        "2024-06-10",
        json.dumps(
            {
                "stats": {},
                "hrv": {
                    "hrvSummary": {
                        "weeklyAvg": 52,
                        "lastNightAvg": 48,
                        "baseline": {
                            "lowUpper": 43,
                            "balancedLow": 47,
                            "balancedUpper": 58,
                            "markerValue": 0.45,
                        },
                        "status": "BALANCED",
                    }
                },
            }
        ),
        (47.0, 58.0, 43.0),
    ),
    # The mapper also accepts an unwrapped summary.
    (
        "2024-06-11",
        json.dumps({"hrv": {"weeklyAvg": 50, "baseline": {
            "lowUpper": 40, "balancedLow": 45, "balancedUpper": 60}}}),
        (45.0, 60.0, 40.0),
    ),
    # Still building a baseline: Garmin sends null.
    (
        "2024-06-12",
        json.dumps({"hrv": {"hrvSummary": {"weeklyAvg": 50, "baseline": None}}}),
        (None, None, None),
    ),
    ("2024-06-13", json.dumps({"hrv": None}), (None, None, None)),
    ("2024-06-14", "not json", (None, None, None)),
    ("2024-06-15", None, (None, None, None)),
    ("2024-06-16", json.dumps({"hrv": {"hrvSummary": [{"odd": "shape"}]}}), (None, None, None)),
]  # fmt: skip


def _cfg(ini: str, directory: str, db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / ini))
    cfg.set_main_option("script_location", str(REPO_ROOT / directory))
    return cfg


def _health(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    return _cfg("alembic_health.ini", "alembic_health", db_path, monkeypatch)


def _rows(db_path: Path, sql: str) -> list[tuple]:  # type: ignore[type-arg]
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _columns(db_path: Path) -> set[str]:
    return {r[1] for r in _rows(db_path, "PRAGMA table_info(daily_health_snapshots)")}


@pytest.fixture
def before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "hrv.db"
    command.upgrade(_cfg("alembic.ini", "alembic", db_path, monkeypatch), "head")
    command.upgrade(_health(db_path, monkeypatch), BEFORE)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO athletes (id, created_at) VALUES (7, '2024-01-01 00:00:00')")
        for day, raw, _ in SEEDED:
            conn.execute(
                "INSERT INTO daily_health_snapshots "
                "(athlete_id, snapshot_date, data_source, raw_data, created_at) "
                "VALUES (7, ?, 'garmin', ?, '2024-06-10 09:00:00')",
                (day, raw),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_the_band_is_backfilled_from_stored_raw_data(
    before: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command.upgrade(_health(before, monkeypatch), H197)

    rows = _rows(
        before,
        f"SELECT snapshot_date, {', '.join(BAND_COLUMNS)} FROM daily_health_snapshots "
        "ORDER BY snapshot_date",
    )
    assert rows == [(day, *band) for day, _, band in SEEDED]


def test_downgrade_drops_the_band(before: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    command.upgrade(_health(before, monkeypatch), H197)
    command.downgrade(_health(before, monkeypatch), BEFORE)

    assert not set(BAND_COLUMNS) & _columns(before)
    assert len(_rows(before, "SELECT id FROM daily_health_snapshots")) == len(SEEDED)
