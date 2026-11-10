"""The source-record / canonical-activity migration (h185a1b2c3d4) on SQLite (ticket #185).

Builds the layout after #184 (``h184a1b2c3d4``), seeds representative history, upgrades, and
checks the backfill: row counts, history preserved, provenance as source records, and that
replaying an intake against the migrated data is a no-op. The Postgres half (schemas, owners,
the immutability trigger, the analytics views) is test_source_records_postgres.py.
"""

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from mycoach.exercise_catalogue import load_exercise_catalogue

REPO_ROOT = Path(__file__).resolve().parents[2]
H184 = "h184a1b2c3d4"
H185 = "h185a1b2c3d4"
CATALOGUE_ID = load_exercise_catalogue()[0].id


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


def _exec(db_path: Path, *statements: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for s in statements:
            conn.execute(s)
        conn.commit()
    finally:
        conn.close()


def _columns(db_path: Path, table: str) -> set[str]:
    return {r[1] for r in _rows(db_path, f"PRAGMA table_info({table})")}


NOW = datetime(2024, 6, 10, 9, 0).isoformat(sep=" ")


def _seed_after_184(db_path: Path) -> None:
    """History as the #184 layout holds it: a logger workout, a Hevy one with no external id,
    and two Garmin activities carrying their raw payload."""
    _exec(
        db_path,
        "INSERT INTO users (id, name, email, fitness_level, created_at, updated_at, "
        "email_daily_briefing, email_weekly_plan, email_post_workout, "
        "email_sleep_coaching, email_weekly_recap, health_athlete_id) "
        f"VALUES (7, 'A', 'a@b.com', 'intermediate', '{NOW}', '{NOW}', 0, 0, 0, 0, 0, 7)",
        "INSERT INTO athletes (id, created_at) VALUES (7, '2024-01-01 00:00:00')",
        "INSERT INTO activities (id, athlete_id, sport, title, start_time, end_time, "
        "data_source, external_id, created_at) VALUES "
        f"(1, 7, 'gym', 'Legs', '{NOW}', '2024-06-10 10:00:00', 'logger', 'uuid-1', '{NOW}')",
        "INSERT INTO activities (id, athlete_id, sport, title, start_time, data_source, "
        f"external_id, created_at) VALUES (2, 7, 'gym', 'Manual', '{NOW}', 'hevy', NULL, '{NOW}')",
        "INSERT INTO activities (id, athlete_id, sport, title, start_time, data_source, "
        "garmin_activity_id, raw_data, avg_hr, created_at) VALUES "
        f"(3, 7, 'swimming', 'Swim', '{NOW}', 'garmin', 'g-100', "
        f"'{json.dumps({'activityId': 100, 'x': 'raw'})}', 140, '{NOW}')",
        "INSERT INTO activities (id, athlete_id, sport, title, start_time, data_source, "
        "garmin_activity_id, raw_data, created_at) VALUES "
        f"(4, 7, 'padel', 'Padel', '{NOW}', 'garmin', 'g-101', NULL, '{NOW}')",
        "INSERT INTO gym_workout_details (activity_id, exercise_id, exercise_title, set_index, "
        "set_type, weight_kg, reps) VALUES "
        f"(1, '{CATALOGUE_ID}', 'Known', 1, 'normal', 100, 5), "
        "(1, NULL, 'Squat', 1, 'normal', 100, 5), (1, NULL, 'Lunge', 1, 'normal', 40, 10), "
        "(2, NULL, 'Curl', 1, 'normal', 20, 12)",
    )


@pytest.fixture
def db_184(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "h185.db"
    command.upgrade(_cfg("alembic.ini", "alembic", db_path, monkeypatch), "head")
    command.upgrade(_health(db_path, monkeypatch), H184)
    _seed_after_184(db_path)
    return db_path


@pytest.fixture
def migrated(db_184: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    command.upgrade(_health(db_184, monkeypatch), "head")
    return db_184


def test_every_activity_gets_one_source_record_and_one_link(migrated: Path) -> None:
    assert _rows(migrated, "SELECT count(*) FROM canonical_activities") == [(4,)]
    assert _rows(migrated, "SELECT count(*) FROM source_records") == [(4,)]
    assert _rows(migrated, "SELECT count(*) FROM canonical_activity_sources") == [(4,)]
    assert "activities" not in {
        r[0] for r in _rows(migrated, "SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    # canonical activity id <-> source record: exactly one each
    assert _rows(
        migrated,
        "SELECT count(DISTINCT canonical_activity_id), count(DISTINCT source_record_id) "
        "FROM canonical_activity_sources",
    ) == [(4, 4)]


def test_provenance_moves_into_source_records(migrated: Path) -> None:
    by_source = {
        (r[0], r[1]): json.loads(r[2])
        for r in _rows(migrated, "SELECT source, external_id, raw_payload FROM source_records")
    }
    # Garmin: the raw payload it sent, kept as received.
    assert by_source[("garmin", "g-100")] == {"activityId": 100, "x": "raw"}
    assert by_source[("garmin", "g-101")] == {}
    # The logger keeps its own external id; its workout and sets are the payload.
    logger = by_source[("logger", "uuid-1")]
    assert logger["title"] == "Legs"
    assert sorted(s["exercise_title"] for s in logger["sets"]) == ["Known", "Lunge", "Squat"]
    # Hevy CSV had no external id: it gets the derived, stable identity the importer uses.
    hevy = [k for k in by_source if k[0] == "hevy"]
    assert len(hevy) == 1 and hevy[0][1].startswith("derived:")
    # The canonical activity carries forward the gym source's external id, unchanged.
    assert _rows(migrated, "SELECT external_id FROM canonical_activities WHERE id = 1") == [
        ("uuid-1",)
    ]
    assert _rows(migrated, "SELECT external_id FROM canonical_activities WHERE id = 2") == [(None,)]
    # The cache columns stay where the app reads them.
    assert _rows(migrated, "SELECT avg_hr FROM canonical_activities WHERE id = 3") == [(140,)]
    assert not {"garmin_activity_id", "raw_data"} & _columns(migrated, "canonical_activities")


def test_history_is_preserved_and_sets_attach_to_the_canonical_activity(migrated: Path) -> None:
    assert _rows(migrated, "SELECT count(*), sum(reps) FROM gym_workout_details") == [(4, 32)]
    assert "canonical_activity_id" in _columns(migrated, "gym_workout_details")
    assert _rows(
        migrated, "SELECT canonical_activity_id, count(*) FROM gym_workout_details GROUP BY 1"
    ) == [(1, 3), (2, 1)]
    # The old data_source values survive on the canonical row (never "merged").
    assert _rows(migrated, "SELECT id, data_source FROM canonical_activities ORDER BY id") == [
        (1, "logger"),
        (2, "hevy"),
        (3, "garmin"),
        (4, "garmin"),
    ]


def test_the_exercise_catalogue_is_mirrored_and_referenced(migrated: Path) -> None:
    assert _rows(migrated, "SELECT count(*) FROM exercises") == [(len(load_exercise_catalogue()),)]
    assert _rows(
        migrated, "SELECT title FROM exercises WHERE id = ?".replace("?", f"'{CATALOGUE_ID}'")
    )
    fks = _rows(migrated, "PRAGMA foreign_key_list(gym_workout_details)")
    assert {(r[2], r[3]) for r in fks} >= {("exercises", "exercise_id")}
    # NULL still means "custom exercise".
    assert _rows(
        migrated, "SELECT count(*) FROM gym_workout_details WHERE exercise_id IS NULL"
    ) == [(3,)]


def test_replaying_an_intake_against_migrated_history_is_a_no_op(
    migrated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from mycoach.database import SQLITE_SCHEMA_TRANSLATE_MAP
    from mycoach.sources.importer import import_workouts, source_external_id
    from mycoach.sources.workout_import import WorkoutImport

    # The Hevy row's derived identity is the one the app's importer would compute today.
    hevy_replay = WorkoutImport(
        title="Manual", start_time=datetime(2024, 6, 10, 9, 0), external_id=None
    )
    assert _rows(migrated, "SELECT external_id FROM source_records WHERE source = 'hevy'") == [
        (source_external_id(hevy_replay),)
    ]

    async def go() -> list[tuple[int, int]]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{migrated}").execution_options(
            schema_translate_map=SQLITE_SCHEMA_TRANSLATE_MAP
        )
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                logger_replay = WorkoutImport(
                    title="Legs", start_time=datetime(2024, 6, 10, 9, 0), external_id="uuid-1"
                )
                a = await import_workouts(session, 7, [logger_replay], source="logger")
                b = await import_workouts(session, 7, [hevy_replay], source="hevy")
                await session.commit()
                return [
                    (a.activities_created, a.activities_skipped),
                    (b.activities_created, b.activities_skipped),
                ]
        finally:
            await engine.dispose()

    assert asyncio.run(go()) == [(0, 1), (0, 1)]
    assert _rows(migrated, "SELECT count(*) FROM canonical_activities") == [(4,)]
    assert _rows(migrated, "SELECT count(*) FROM source_records") == [(4,)]


def test_it_refuses_rows_already_marked_merged(
    db_184: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#110: the provenance of a ``merged`` row was destroyed; do not invent it."""
    _exec(db_184, "UPDATE activities SET data_source = 'merged' WHERE id = 1")
    with pytest.raises(Exception, match="merged"):
        command.upgrade(_health(db_184, monkeypatch), "head")
    assert "activities" in {
        r[0] for r in _rows(db_184, "SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def test_it_refuses_an_exercise_id_the_catalogue_does_not_know(
    db_184: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _exec(
        db_184,
        "UPDATE gym_workout_details SET exercise_id = 'bogus-id' WHERE exercise_title = 'Squat'",
    )
    with pytest.raises(Exception, match="bogus-id"):
        command.upgrade(_health(db_184, monkeypatch), "head")


def test_downgrade_restores_the_pre_185_layout(
    migrated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command.downgrade(_health(migrated, monkeypatch), H184)

    assert _rows(migrated, "SELECT count(*) FROM activities") == [(4,)]
    assert {"garmin_activity_id", "raw_data"} <= _columns(migrated, "activities")
    assert _rows(migrated, "SELECT garmin_activity_id, raw_data FROM activities WHERE id = 3") == [
        ("g-100", json.dumps({"activityId": 100, "x": "raw"}))
    ]
    assert "activity_id" in _columns(migrated, "gym_workout_details")
    assert _rows(migrated, "SELECT count(*), sum(reps) FROM gym_workout_details") == [(4, 32)]
    tables = {r[0] for r in _rows(migrated, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not {"source_records", "canonical_activity_sources", "exercises"} & tables
    assert _rows(migrated, "SELECT version_num FROM alembic_version_health") == [(H184,)]
