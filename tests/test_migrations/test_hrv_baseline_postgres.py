"""The HRV baseline band migration (h197a1b2c3d4) on a real Postgres (ticket #197).

Skipped unless ``MYCOACH_TEST_PG_URL`` is set (see test_schema_seam_postgres.py, whose
pre-seam fixture and helpers this reuses). Starts from the layout before the revision,
seeds snapshots with and without a band in ``raw_data``, then upgrades.
"""

import json

import pytest
from alembic.config import Config

from alembic import command
from tests.test_migrations.test_schema_seam_postgres import (  # noqa: F401  (fixtures)
    PG_URL,
    _as_role,
    _cfg,
    _rows,
    _run,
    _sql,
    db_bootstrap,
    pg_db,
)

pytestmark = pytest.mark.skipif(not PG_URL, reason="MYCOACH_TEST_PG_URL not set")

BEFORE = "h186a1b2c3d4"
H197 = "h197a1b2c3d4"
WITH_BAND = json.dumps(
    {"hrv": {"hrvSummary": {"weeklyAvg": 52, "baseline": {
        "lowUpper": 43, "balancedLow": 47, "balancedUpper": 58, "markerValue": 0.45}}}}
)  # fmt: skip
WITHOUT_BAND = json.dumps({"hrv": {"hrvSummary": {"weeklyAvg": 52, "baseline": None}}})


def _health(url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    return _cfg("alembic_health.ini", "alembic_health", url, monkeypatch)


@pytest.fixture
def before(pg_db: str, monkeypatch: pytest.MonkeyPatch) -> str:  # noqa: F811
    _run(db_bootstrap.run(pg_db, finalize=False))
    command.upgrade(_cfg("alembic.ini", "alembic", pg_db, monkeypatch), "head")
    command.upgrade(_health(pg_db, monkeypatch), BEFORE)
    # The legacy fixture already holds a 2024-06-10 snapshot with no raw data.
    _sql(
        pg_db,
        "INSERT INTO health.daily_health_snapshots (athlete_id, snapshot_date, data_source, "
        f"raw_data, created_at) VALUES (7, '2024-06-11', 'garmin', '{WITH_BAND}', now()), "
        f"(7, '2024-06-12', 'garmin', '{WITHOUT_BAND}', now()), "
        "(7, '2024-06-13', 'garmin', 'not json', now())",
    )
    return pg_db


def test_the_band_is_backfilled_from_stored_raw_data(
    before: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    command.upgrade(_health(before, monkeypatch), H197)

    assert _rows(
        before,
        "SELECT snapshot_date::text, hrv_baseline_low, hrv_baseline_high, hrv_baseline_low_upper "
        "FROM health.daily_health_snapshots ORDER BY snapshot_date",
    ) == [
        ("2024-06-10", None, None, None),
        ("2024-06-11", 47.0, 58.0, 43.0),
        ("2024-06-12", None, None, None),
        ("2024-06-13", None, None, None),
    ]
    assert _as_role(
        before,
        "health_role",
        "SELECT count(hrv_baseline_low) FROM health.daily_health_snapshots",
    ) == [(1,)]


def test_downgrade_drops_the_band(before: str, monkeypatch: pytest.MonkeyPatch) -> None:
    command.upgrade(_health(before, monkeypatch), H197)
    command.downgrade(_health(before, monkeypatch), BEFORE)

    assert (
        _rows(
            before,
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'health' "
            "AND table_name = 'daily_health_snapshots' AND column_name LIKE 'hrv_baseline%'",
        )
        == []
    )
