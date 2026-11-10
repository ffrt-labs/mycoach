"""The schema-seam migrations (ticket #184) on SQLite: two independent histories.

SQLite has no schemas, so only the data-shaping half of the seam is observable
here: the prescription split, the athlete identity and the two version tables.
The schema/role half is checked on Postgres (test_schema_seam_postgres.py).
"""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_HEAD = "a7c41e9d2b56"  # coaching history, before the seam
H184 = "h184a1b2c3d4"  # health history at the seam, before source records (#185)


def _coaching_cfg(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _health_cfg(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic_health.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic_health"))
    return cfg


def _seed_legacy(db_path: Path) -> None:
    """Pre-seam data: one user, one logger workout with a prescription on one set."""
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO users (id, name, email, fitness_level, created_at, updated_at, "
            "email_daily_briefing, email_weekly_plan, email_post_workout, "
            "email_sleep_coaching, email_weekly_recap) "
            "VALUES (7, 'A', 'a@b.com', 'intermediate', ?, ?, 0, 0, 0, 0, 0)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO activities (id, user_id, sport, title, start_time, data_source, "
            "external_id, created_at) VALUES (1, 7, 'gym', 'Legs', ?, 'logger', 'uuid-1', ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO activities (id, user_id, sport, title, start_time, data_source, "
            "external_id, created_at) VALUES (2, 7, 'gym', 'Manual', ?, 'hevy', NULL, ?)",
            (now, now),
        )
        conn.executemany(
            "INSERT INTO gym_workout_details (activity_id, exercise_title, set_index, "
            "set_type, weight_kg, reps, prescribed_weight_kg, prescribed_reps) "
            "VALUES (?, ?, ?, 'normal', ?, ?, ?, ?)",
            [
                (1, "Squat", 1, 100, 5, 102.5, 5),
                (1, "Squat", 2, 100, 5, None, None),
                (1, "Lunge", 1, 40, 10, 42.5, 8),
                (2, "Curl", 1, 20, 12, None, None),
            ],
        )
        conn.execute(
            "INSERT INTO daily_health_snapshots (user_id, snapshot_date, data_source, "
            "created_at, updated_at) VALUES (7, '2024-06-10', 'garmin', ?, ?)",
            (now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _rows(db_path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


@pytest.fixture
def legacy_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "seam.db"
    command.upgrade(_coaching_cfg(db_path, monkeypatch), LEGACY_HEAD)
    _seed_legacy(db_path)
    return db_path


def test_the_prescription_moves_to_coaching_keyed_by_set(
    legacy_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command.upgrade(_coaching_cfg(legacy_db, monkeypatch), "head")
    command.upgrade(_health_cfg(legacy_db, monkeypatch), H184)

    assert _rows(
        legacy_db,
        "SELECT activity_external_id, exercise_title, set_index, prescribed_weight_kg, "
        "prescribed_reps FROM prescription_fulfillments ORDER BY exercise_title, set_index",
    ) == [("uuid-1", "Lunge", 1, 42.5, 8), ("uuid-1", "Squat", 1, 102.5, 5)]
    # What the plan called for no longer lives on the performed set.
    assert not {"prescribed_weight_kg", "prescribed_reps"} & _columns(
        legacy_db, "gym_workout_details"
    )
    # The performed facts survived untouched.
    assert _rows(legacy_db, "SELECT count(*), sum(reps) FROM gym_workout_details") == [(4, 32)]


def test_health_issues_the_athlete_and_coaching_stores_its_id(
    legacy_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command.upgrade(_coaching_cfg(legacy_db, monkeypatch), "head")
    command.upgrade(_health_cfg(legacy_db, monkeypatch), H184)

    assert _rows(legacy_db, "SELECT id FROM athletes") == [(7,)]
    assert _rows(legacy_db, "SELECT id, health_athlete_id FROM users") == [(7, 7)]
    for table in ("activities", "daily_health_snapshots"):
        cols = _columns(legacy_db, table)
        assert "athlete_id" in cols and "user_id" not in cols
    assert _rows(legacy_db, "SELECT athlete_id FROM activities ORDER BY id") == [(7,), (7,)]


def test_the_two_histories_keep_separate_version_tables(
    legacy_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command.upgrade(_coaching_cfg(legacy_db, monkeypatch), "head")
    command.upgrade(_health_cfg(legacy_db, monkeypatch), H184)

    coaching = _rows(legacy_db, "SELECT version_num FROM alembic_version")
    health = _rows(legacy_db, "SELECT version_num FROM alembic_version_health")
    assert len(coaching) == 1 and len(health) == 1
    assert coaching != health


def test_health_refuses_to_drop_prescriptions_coaching_has_not_taken(
    legacy_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Health must not run ahead of coaching: the drop would lose the prescriptions."""
    with pytest.raises(Exception, match="prescription_fulfillments"):
        command.upgrade(_health_cfg(legacy_db, monkeypatch), "head")
    assert {"prescribed_weight_kg", "prescribed_reps"} <= _columns(legacy_db, "gym_workout_details")


def test_a_prescription_with_no_external_id_blocks_the_drop(
    legacy_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A prescribed set whose workout has no external id cannot be keyed; refuse, don't lose it."""
    conn = sqlite3.connect(legacy_db)
    conn.execute("UPDATE gym_workout_details SET prescribed_reps = 9 WHERE activity_id = 2")
    conn.commit()
    conn.close()

    command.upgrade(_coaching_cfg(legacy_db, monkeypatch), "head")
    with pytest.raises(Exception, match="no external id"):
        command.upgrade(_health_cfg(legacy_db, monkeypatch), "head")


def test_both_histories_downgrade_cleanly(legacy_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coaching = _coaching_cfg(legacy_db, monkeypatch)
    health = _health_cfg(legacy_db, monkeypatch)
    command.upgrade(coaching, "head")
    command.upgrade(health, "head")

    command.downgrade(health, "base")
    command.downgrade(coaching, LEGACY_HEAD)

    assert {"prescribed_weight_kg", "prescribed_reps"} <= _columns(legacy_db, "gym_workout_details")
    assert "user_id" in _columns(legacy_db, "activities")
    # The prescriptions are carried back onto the performed set.
    assert _rows(
        legacy_db,
        "SELECT prescribed_weight_kg, prescribed_reps FROM gym_workout_details "
        "WHERE exercise_title = 'Squat' AND set_index = 1",
    ) == [(102.5, 5)]
