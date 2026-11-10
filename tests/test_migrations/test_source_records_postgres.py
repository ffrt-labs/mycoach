"""The source-record migration (h185a1b2c3d4) on a real Postgres (ticket #185).

Skipped unless ``MYCOACH_TEST_PG_URL`` is set (see test_schema_seam_postgres.py, whose
pre-seam fixture and helpers this reuses). Each test starts from the #184 layout
(``h184a1b2c3d4``), seeds history, then upgrades: the cutover as the human will run it.
Covers what SQLite cannot: schema placement and owners, the immutability trigger, the
``health_analytics`` views staying additive and readable by ``grafana_role``, and the
grants on the new tables.
"""

import pytest
from alembic.config import Config

from alembic import command
from mycoach import db_roles
from tests.test_migrations.test_schema_seam_postgres import (  # noqa: F401  (fixtures)
    PG_URL,
    _as_role,
    _cfg,
    _rows,
    _run,
    _scalar,
    _sql,
    db_bootstrap,
    pg_db,
)

pytestmark = pytest.mark.skipif(not PG_URL, reason="MYCOACH_TEST_PG_URL not set")

H184 = "h184a1b2c3d4"
H185 = "h185a1b2c3d4"


def _health(url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    return _cfg("alembic_health.ini", "alembic_health", url, monkeypatch)


def _view_columns(url: str, view: str) -> dict[str, str]:
    return {
        r[0]: r[1]
        for r in _rows(
            url,
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_schema = 'health_analytics' AND table_name = '{view}'",
        )
    }


@pytest.fixture
def at_184(pg_db: str, monkeypatch: pytest.MonkeyPatch) -> str:  # noqa: F811
    """The database after #184's cutover, with a Garmin activity and a gym one that has sets."""
    _run(db_bootstrap.run(pg_db, finalize=False))
    command.upgrade(_cfg("alembic.ini", "alembic", pg_db, monkeypatch), "head")
    command.upgrade(_health(pg_db, monkeypatch), H184)
    _sql(
        pg_db,
        "INSERT INTO health.activities (athlete_id, sport, title, start_time, data_source, "
        "garmin_activity_id, raw_data, avg_hr, created_at) VALUES "
        "(7, 'swimming', 'Swim', '2024-06-10 07:00', 'garmin', 'g-100', "
        '\'{"activityId": 100, "x": "raw"}\', 140, now())',
    )
    return pg_db


@pytest.fixture
def migrated(at_184: str, monkeypatch: pytest.MonkeyPatch) -> str:
    command.upgrade(_health(at_184, monkeypatch), "head")
    return at_184


def test_backfill_counts_and_provenance(migrated: str) -> None:
    # seeded: 1 logger workout (2 sets) from the #184 fixture + 1 Garmin activity
    assert _scalar(migrated, "SELECT count(*) FROM health.canonical_activities") == 2
    assert _scalar(migrated, "SELECT count(*) FROM health.source_records") == 2
    assert _scalar(migrated, "SELECT count(*) FROM health.canonical_activity_sources") == 2
    assert _scalar(migrated, "SELECT count(*) FROM health.gym_workout_details") == 2
    assert _rows(
        migrated,
        "SELECT source, external_id, raw_payload->>'x', pg_typeof(raw_payload)::text "
        "FROM health.source_records WHERE source = 'garmin'",
    ) == [("garmin", "g-100", "raw", "jsonb")]
    assert (
        _scalar(
            migrated,
            "SELECT external_id FROM health.canonical_activities WHERE data_source = 'logger'",
        )
        == "uuid-1"
    )


def test_new_tables_belong_to_health_owner_and_the_runtime_role_can_do_dml(migrated: str) -> None:
    owners = _rows(
        migrated,
        "SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'health' "
        "AND tablename IN ('source_records', 'canonical_activity_sources', 'exercises', "
        "'canonical_activities') ORDER BY 1",
    )
    assert {o for _, o in owners} == {"health_owner"} and len(owners) == 4
    assert _as_role(migrated, "health_role", "SELECT count(*) FROM health.source_records") == [(2,)]
    _as_role(migrated, "health_role", "SELECT count(*) FROM health.exercises")
    _as_role(
        migrated,
        "health_role",
        "INSERT INTO health.source_records (athlete_id, source, external_id, sport, start_time, "
        "title, raw_payload, received_at) VALUES (7, 'hevy', 'h1', 'gym', now(), 'x', '{}', now())",
    )
    with pytest.raises(Exception, match="permission denied"):
        _as_role(migrated, "coaching_role", "SELECT * FROM health.source_records")
    with pytest.raises(Exception, match="permission denied|must be owner"):
        _as_role(migrated, "health_role", "DROP TABLE health.source_records")


def test_source_records_are_immutable_even_to_the_runtime_role(migrated: str) -> None:
    for statement in (
        "UPDATE health.source_records SET title = 'rewritten'",
        "DELETE FROM health.source_records",
    ):
        with pytest.raises(Exception, match="immutable"):
            _as_role(migrated, "health_role", statement)
    assert (
        _scalar(migrated, "SELECT count(*) FROM health.source_records WHERE title = 'rewritten'")
        == 0
    )
    assert _scalar(migrated, "SELECT count(*) FROM health.source_records") == 2


def test_identity_is_unique_per_athlete_and_source(migrated: str) -> None:
    with pytest.raises(Exception, match="uq_source_records_identity|duplicate key"):
        _sql(
            migrated,
            "INSERT INTO health.source_records (athlete_id, source, external_id, sport, "
            "start_time, title, raw_payload, received_at) VALUES "
            "(7, 'garmin', 'g-100', 'gym', now(), 'dup', '{}', now())",
        )


def test_performed_sets_reference_the_catalogue_mirror(migrated: str) -> None:
    assert _scalar(migrated, "SELECT count(*) FROM health.exercises") > 100
    with pytest.raises(Exception, match="foreign key|violates"):
        _sql(migrated, "UPDATE health.gym_workout_details SET exercise_id = 'not-a-real-id'")
    # NULL still means "custom exercise".
    _sql(migrated, "UPDATE health.gym_workout_details SET exercise_id = NULL")


def test_analytics_views_stay_readable_and_additive(
    at_184: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = {v: _view_columns(at_184, v) for v in ("daily_metrics", "activities", "gym_sets")}
    command.upgrade(_health(at_184, monkeypatch), "head")
    after = {v: _view_columns(at_184, v) for v in before}

    for view, columns in before.items():
        # additive-only (#113): every published column still there, same type
        assert columns.items() <= after[view].items(), view
    assert set(after["activities"]) - set(before["activities"]) == {"sources"}
    assert set(after["gym_sets"]) - set(before["gym_sets"]) == {"canonical_activity_id"}
    assert after["daily_metrics"] == before["daily_metrics"]

    definitions = " ".join(
        r[0]
        for r in _rows(
            at_184, "SELECT definition FROM pg_views WHERE schemaname = 'health_analytics'"
        )
    )
    assert "coaching." not in definitions and "public." not in definitions
    assert "garmin_activity_id" not in definitions and "raw_data" not in definitions
    assert _scalar(at_184, "SELECT count(*) FROM health_analytics.activities") == 2
    assert _scalar(at_184, "SELECT count(*) FROM health_analytics.gym_sets") == 2
    owners = _rows(
        at_184,
        "SELECT viewname, viewowner FROM pg_views WHERE schemaname = 'health_analytics' ORDER BY 1",
    )
    assert owners == [
        ("activities", "health_owner"),
        ("daily_metrics", "health_owner"),
        ("gym_sets", "health_owner"),
    ]


def test_grafana_reads_the_views_and_nothing_else(migrated: str) -> None:
    for view in ("activities", "gym_sets", "daily_metrics"):
        _as_role(migrated, "grafana_role", f"SELECT count(*) FROM health_analytics.{view}")
    for table in ("source_records", "canonical_activities", "exercises"):
        with pytest.raises(Exception, match="permission denied"):
            _as_role(migrated, "grafana_role", f"SELECT * FROM health.{table}")


def test_a_linked_activity_is_still_published_as_merged_and_lists_its_sources(
    migrated: str,
) -> None:
    """``merged`` is not a stored value, but Grafana panels filtering on it keep working."""
    _sql(
        migrated,
        "INSERT INTO health.source_records (id, athlete_id, source, external_id, sport, "
        "start_time, title, raw_payload, received_at) VALUES "
        "(900, 7, 'garmin', 'g-900', 'gym', now(), 'Strength', '{}', now())",
        "INSERT INTO health.canonical_activity_sources (canonical_activity_id, source_record_id) "
        "SELECT id, 900 FROM health.canonical_activities WHERE data_source = 'logger'",
    )
    assert _rows(
        migrated,
        "SELECT data_source, sources FROM health_analytics.activities WHERE title = 'Legs'",
    ) == [("merged", ["garmin", "logger"])]
    assert (
        _scalar(
            migrated, "SELECT data_source FROM health.canonical_activities WHERE title = 'Legs'"
        )
        == "logger"
    )


def test_it_refuses_a_merged_row_and_changes_nothing(
    at_184: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sql(at_184, "UPDATE health.activities SET data_source = 'merged' WHERE data_source = 'logger'")
    with pytest.raises(Exception, match="merged"):
        command.upgrade(_health(at_184, monkeypatch), "head")
    assert _scalar(at_184, "SELECT to_regclass('health.activities')::text") == "health.activities"
    assert _scalar(at_184, "SELECT version_num FROM health.alembic_version") == H184


def test_upgrade_downgrade_upgrade_round_trips(
    migrated: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    command.downgrade(_health(migrated, monkeypatch), H184)
    assert _scalar(migrated, "SELECT count(*) FROM health.activities") == 2
    assert (
        _scalar(
            migrated,
            "SELECT garmin_activity_id FROM health.activities WHERE data_source = 'garmin'",
        )
        == "g-100"
    )
    assert _scalar(migrated, "SELECT to_regclass('health.source_records')::text") is None
    assert _scalar(migrated, "SELECT count(*) FROM health_analytics.gym_sets") == 2
    command.upgrade(_health(migrated, monkeypatch), "head")
    assert _scalar(migrated, "SELECT count(*) FROM health.source_records") == 2
    assert db_roles.HEALTH.owner == _scalar(
        migrated, "SELECT tableowner FROM pg_tables WHERE tablename = 'source_records'"
    )
