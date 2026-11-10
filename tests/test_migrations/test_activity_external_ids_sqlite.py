"""The #186 revisions on SQLite: settlement columns, unique external ids, re-keyed links.

Builds the #185 layout in both histories (coaching ``c184a1b2c3d4``, health ``h185a1b2c3d4``),
seeds activities and the coaching rows that point at them by internal id, then runs the health
revision ``h186a1b2c3d4`` and the coaching revision ``c186a1b2c3d4`` in the cutover order. The
Postgres half (roles, schemas, the permission wall) is test_health_boundary_postgres.py.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
C184 = "c184a1b2c3d4"
H185 = "h185a1b2c3d4"
H186 = "h186a1b2c3d4"
C186 = "c186a1b2c3d4"
NOW = datetime(2024, 6, 10, 9, 0).isoformat(sep=" ")


def _cfg(ini: str, directory: str, db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / ini))
    cfg.set_main_option("script_location", str(REPO_ROOT / directory))
    return cfg


def _coaching(db: Path, mp: pytest.MonkeyPatch) -> Config:
    return _cfg("alembic.ini", "alembic", db, mp)


def _health(db: Path, mp: pytest.MonkeyPatch) -> Config:
    return _cfg("alembic_health.ini", "alembic_health", db, mp)


def _rows(db: Path, sql: str) -> list[tuple]:  # type: ignore[type-arg]
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _exec(db: Path, *statements: str) -> None:
    conn = sqlite3.connect(db)
    try:
        for s in statements:
            conn.execute(s)
        conn.commit()
    finally:
        conn.close()


def _columns(db: Path, table: str) -> set[str]:
    return {r[1] for r in _rows(db, f"PRAGMA table_info({table})")}


def _seed_activity(db: Path, source: str, external_id: str | None) -> None:
    """One canonical activity with its source record and link, as #185 left them."""
    sr = "g-1" if source == "garmin" else (external_id or "derived:abc")
    _exec(
        db,
        "INSERT INTO canonical_activities (athlete_id, sport, title, start_time, data_source, "
        f"external_id, created_at) VALUES (7, 'gym', 'T', '{NOW}', '{source}', "
        f"{'NULL' if external_id is None else repr(external_id)}, '{NOW}')",
        "INSERT INTO source_records (athlete_id, source, external_id, sport, start_time, title, "
        f"raw_payload, received_at) VALUES (7, '{source}', '{sr}', 'gym', '{NOW}', 'T', '{{}}', "
        f"'{NOW}')",
        "INSERT INTO canonical_activity_sources (canonical_activity_id, source_record_id) "
        "SELECT (SELECT max(id) FROM canonical_activities), (SELECT max(id) FROM source_records)",
    )


@pytest.fixture
def at_185(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "h186.db"
    command.upgrade(_coaching(db, monkeypatch), C184)
    command.upgrade(_health(db, monkeypatch), H185)
    _exec(
        db,
        "INSERT INTO athletes (id, created_at) VALUES (7, '2024-01-01 00:00:00')",
        "INSERT INTO users (id, name, email, fitness_level, created_at, updated_at, "
        "email_daily_briefing, email_weekly_plan, email_post_workout, "
        "email_sleep_coaching, email_weekly_recap, health_athlete_id) "
        f"VALUES (7, 'A', 'a@b.com', 'intermediate', '{NOW}', '{NOW}', 0, 0, 0, 0, 0, 7)",
        "INSERT INTO weekly_plans (id, user_id, week_start, prompt_version, status, created_at) "
        f"VALUES (1, 7, '2024-06-10', 'v1', 'active', '{NOW}')",
    )
    return db


def _plan_row(db: Path, activity_id: int | None, planned_id: int = 1) -> None:
    _exec(
        db,
        "INSERT INTO planned_sessions (id, plan_id, day_of_week, sport, title, completed, track, "
        f"activity_id) VALUES ({planned_id}, 1, 0, 'gym', 'Push', 1, 'gym', "
        f"{'NULL' if activity_id is None else activity_id})",
    )


def _insight_row(db: Path, activity_id: int | None, insight_id: int = 1) -> None:
    _exec(
        db,
        "INSERT INTO coaching_insights (id, user_id, insight_date, insight_type, content, "
        "prompt_version, activity_id, created_at) VALUES "
        f"({insight_id}, 7, '2024-06-10', 'post_workout', '{{}}', 'v1', "
        f"{'NULL' if activity_id is None else activity_id}, '{NOW}')",
    )


def test_existing_activities_are_settled_and_every_one_has_an_external_id(
    at_185: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_activity(at_185, "logger", "uuid-1")
    _seed_activity(at_185, "hevy", None)  # a Hevy CSV row: its source record holds the derived id
    _seed_activity(at_185, "garmin", None)
    command.upgrade(_health(at_185, monkeypatch), H186)

    assert _rows(
        at_185, "SELECT external_id FROM canonical_activities ORDER BY id"
    ) == [("uuid-1",), ("derived:abc",), ("g-1",)]
    # nothing re-fires final coaching for history
    assert _rows(
        at_185,
        "SELECT DISTINCT settlement_reason, settled_at IS NOT NULL FROM canonical_activities",
    ) == [("reconciled", 1)]


def test_external_ids_are_unique_per_athlete(
    at_185: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_activity(at_185, "logger", "uuid-1")
    command.upgrade(_health(at_185, monkeypatch), H186)

    with pytest.raises(sqlite3.IntegrityError):
        _seed_activity(at_185, "logger", "uuid-1")


def test_it_refuses_two_activities_sharing_an_external_id(
    at_185: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_activity(at_185, "logger", "dup")
    _seed_activity(at_185, "hevy", "dup")

    with pytest.raises(Exception, match="shared by several activities"):
        command.upgrade(_health(at_185, monkeypatch), H186)

    assert "settled_at" not in _columns(at_185, "canonical_activities")
    assert _rows(at_185, "SELECT version_num FROM alembic_version_health") == [(H185,)]


def test_planned_sessions_and_insights_are_re_keyed_by_external_id(
    at_185: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_activity(at_185, "logger", "uuid-1")
    _seed_activity(at_185, "garmin", None)
    _plan_row(at_185, 1, planned_id=1)
    _plan_row(at_185, None, planned_id=2)
    _insight_row(at_185, 2, insight_id=1)
    _insight_row(at_185, None, insight_id=2)

    command.upgrade(_health(at_185, monkeypatch), H186)
    command.upgrade(_coaching(at_185, monkeypatch), C186)

    assert _rows(
        at_185, "SELECT id, activity_external_id FROM planned_sessions ORDER BY id"
    ) == [(1, "uuid-1"), (2, None)]
    assert _rows(
        at_185, "SELECT id, activity_external_id, revision, source_event_id "
        "FROM coaching_insights ORDER BY id"
    ) == [(1, "g-1", 1, None), (2, None, 1, None)]
    # the internal id no longer exists on the coaching side
    assert "activity_id" not in _columns(at_185, "planned_sessions")
    assert "activity_id" not in _columns(at_185, "coaching_insights")


def test_it_refuses_a_link_that_no_longer_resolves(
    at_185: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_activity(at_185, "logger", "uuid-1")
    _plan_row(at_185, 999)  # an old reconciliation deleted that Garmin-only row
    command.upgrade(_health(at_185, monkeypatch), H186)

    with pytest.raises(Exception, match="do not resolve"):
        command.upgrade(_coaching(at_185, monkeypatch), C186)

    assert "activity_id" in _columns(at_185, "planned_sessions")
    assert _rows(at_185, "SELECT version_num FROM alembic_version") == [(C184,)]


def test_it_refuses_to_re_key_before_the_health_revision_ran(
    at_185: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_activity(at_185, "hevy", None)  # no external id until h186 fills it
    _plan_row(at_185, 1)

    with pytest.raises(Exception, match="h186a1b2c3d4"):
        command.upgrade(_coaching(at_185, monkeypatch), C186)


def test_downgrade_restores_the_internal_ids(
    at_185: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_activity(at_185, "logger", "uuid-1")
    _plan_row(at_185, 1)
    command.upgrade(_health(at_185, monkeypatch), H186)
    command.upgrade(_coaching(at_185, monkeypatch), C186)
    _exec(
        at_185,
        "INSERT INTO coaching_insights (id, user_id, insight_date, insight_type, content, "
        "prompt_version, activity_external_id, revision, created_at) VALUES "
        f"(9, 7, '2024-06-10', 'post_workout', '{{}}', 'v1', 'uuid-1', 2, '{NOW}')",
    )

    command.downgrade(_coaching(at_185, monkeypatch), C184)
    command.downgrade(_health(at_185, monkeypatch), H185)

    assert _rows(at_185, "SELECT activity_id FROM planned_sessions") == [(1,)]
    assert "activity_external_id" not in _columns(at_185, "planned_sessions")
    assert "settled_at" not in _columns(at_185, "canonical_activities")
    # a revision beyond the first has no place in the old shape
    assert _rows(at_185, "SELECT count(*) FROM coaching_insights WHERE id = 9") == [(0,)]
