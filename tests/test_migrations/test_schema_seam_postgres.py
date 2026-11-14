"""The health/coaching schema seam on a real Postgres (ticket #184).

Skipped unless ``MYCOACH_TEST_PG_URL`` points at a Postgres the tests may create databases
and roles in, as a superuser, e.g.
``postgresql+asyncpg://postgres@127.0.0.1:5432/postgres``. Each test builds its own
throwaway database, shaped like production before the seam (every table in ``public``,
``user_id`` columns, ``prescribed_*`` on the performed sets, the #117 analytics views), and
runs the real bootstrap and both Alembic histories over it.

Roles are cluster-wide, so the tests create-if-missing and never drop the seam roles.
"""

import asyncio
import importlib.util
import os
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from alembic import command
from mycoach import db_roles
from mycoach.database import Base
from mycoach.tools import db_bootstrap

PG_URL = os.environ.get("MYCOACH_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="MYCOACH_TEST_PG_URL not set")

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_HEAD = "a7c41e9d2b56"
T = TypeVar("T")


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


async def _with_conn(url: str, fn: Callable[[AsyncConnection], Awaitable[T]]) -> T:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            return await fn(conn)
    finally:
        await engine.dispose()


def _sql(url: str, *statements: str) -> None:
    async def go(conn: AsyncConnection) -> None:
        for s in statements:
            await conn.execute(text(s))

    _run(_with_conn(url, go))


def _scalar(url: str, statement: str) -> Any:
    async def go(conn: AsyncConnection) -> Any:
        return (await conn.execute(text(statement))).scalar()

    return _run(_with_conn(url, go))


def _rows(url: str, statement: str) -> list[tuple[Any, ...]]:
    async def go(conn: AsyncConnection) -> list[tuple[Any, ...]]:
        return [tuple(r) for r in (await conn.execute(text(statement))).all()]

    return _run(_with_conn(url, go))


def _as_role(url: str, role: str, statement: str) -> Any:
    """Run ``statement`` with the privileges of ``role`` (SET LOCAL ROLE)."""

    async def go(conn: AsyncConnection) -> Any:
        await conn.execute(text(f"SET LOCAL ROLE {role}"))
        result = await conn.execute(text(statement))
        return result.all() if result.returns_rows else None

    return _run(_with_conn(url, go))


def _legacy_layout(conn_sync: Any) -> None:
    """The production layout before the seam: everything in ``public``."""
    Base.metadata.create_all(
        conn_sync.execution_options(schema_translate_map={"health": "public", "coaching": "public"})
    )
    # The models describe the post-#185 layout; production before the seam had the
    # pre-#185 one (``activities`` with provenance columns, no source records or exercises).
    for statement in (
        "DROP TABLE public.canonical_activity_sources",
        "DROP TABLE public.source_records",
        "ALTER TABLE public.gym_workout_details DROP CONSTRAINT gym_workout_details_exercise_id_fkey",
        "DROP TABLE public.exercises",
        "ALTER TABLE public.canonical_activities RENAME TO activities",
        "ALTER TABLE public.gym_workout_details RENAME COLUMN canonical_activity_id TO activity_id",
        "ALTER TABLE public.activities ADD COLUMN garmin_activity_id VARCHAR(100) UNIQUE, "
        "ADD COLUMN raw_data TEXT",
        # ... and before #186: internal ids held by value, no settlement, nullable external id.
        "DROP INDEX public.uq_canonical_activities_athlete_external_id",
        "ALTER TABLE public.activities DROP COLUMN settled_at, DROP COLUMN settlement_reason",
        "ALTER TABLE public.activities ALTER COLUMN external_id DROP NOT NULL",
        "ALTER TABLE public.planned_sessions DROP COLUMN activity_external_id, "
        "ADD COLUMN activity_id INTEGER",
        "ALTER TABLE public.coaching_insights DROP COLUMN activity_external_id, "
        "DROP COLUMN revision, DROP COLUMN source_event_id, ADD COLUMN activity_id INTEGER",
    ):
        conn_sync.execute(text(statement))
    for table in ("activities", "daily_health_snapshots", "data_source_configs"):
        conn_sync.execute(text(f"ALTER TABLE public.{table} RENAME COLUMN athlete_id TO user_id"))
        conn_sync.execute(
            text(
                f"ALTER TABLE public.{table} ADD CONSTRAINT {table}_user_id_fkey "
                "FOREIGN KEY (user_id) REFERENCES public.users (id)"
            )
        )
    conn_sync.execute(
        text(
            "ALTER TABLE public.planned_sessions ADD CONSTRAINT planned_sessions_activity_id_fkey "
            "FOREIGN KEY (activity_id) REFERENCES public.activities (id)"
        )
    )
    conn_sync.execute(
        text(
            "ALTER TABLE public.coaching_insights ADD CONSTRAINT "
            "coaching_insights_activity_id_fkey "
            "FOREIGN KEY (activity_id) REFERENCES public.activities (id)"
        )
    )
    conn_sync.execute(
        text(
            "ALTER TABLE public.gym_workout_details "
            "ADD COLUMN prescribed_weight_kg DOUBLE PRECISION, ADD COLUMN prescribed_reps INTEGER"
        )
    )
    conn_sync.execute(text("DROP TABLE public.athletes"))
    conn_sync.execute(text("DROP TABLE public.prescription_fulfillments"))
    conn_sync.execute(text("ALTER TABLE public.users DROP COLUMN health_athlete_id"))
    conn_sync.execute(
        text("CREATE TABLE public.alembic_version (version_num varchar(32) NOT NULL PRIMARY KEY)")
    )
    conn_sync.execute(text(f"INSERT INTO public.alembic_version VALUES ('{LEGACY_HEAD}')"))
    # The #117 analytics schema, built by that migration's own upgrade().
    spec = importlib.util.spec_from_file_location(
        "analytics_117",
        REPO_ROOT / "alembic" / "versions" / "7cf0523de01b_add_health_analytics_schema_and_.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(conn_sync)):
        module.upgrade()


def _seed_legacy(url: str) -> None:
    _sql(
        url,
        "INSERT INTO public.users (id, name, email, fitness_level, created_at, updated_at, "
        "email_daily_briefing, email_weekly_plan, email_post_workout, email_sleep_coaching, "
        "email_weekly_recap) VALUES (7, 'A', 'a@b.com', 'intermediate', now(), now(), "
        "true, true, true, true, true)",
        "INSERT INTO public.activities (user_id, sport, title, start_time, data_source, "
        "external_id, created_at) VALUES (7, 'gym', 'Legs', now(), 'logger', 'uuid-1', now())",
        "INSERT INTO public.gym_workout_details (activity_id, exercise_title, set_index, "
        "set_type, weight_kg, reps, prescribed_weight_kg, prescribed_reps) VALUES "
        "(1, 'Squat', 1, 'normal', 100, 5, 102.5, 5), (1, 'Squat', 2, 'normal', 100, 5, NULL, NULL)",
        "INSERT INTO public.daily_health_snapshots (user_id, snapshot_date, data_source, "
        "created_at, updated_at) VALUES (7, '2024-06-10', 'garmin', now(), now())",
    )


@pytest.fixture
def pg_db() -> Iterator[str]:
    """A throwaway database in the pre-seam layout, with the two migration histories pointed at it."""
    assert PG_URL
    admin = PG_URL
    name = f"seam_{uuid.uuid4().hex[:10]}"

    async def admin_exec(statement: str) -> None:
        engine = create_async_engine(admin, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(statement))
        finally:
            await engine.dispose()

    _run(admin_exec(f"CREATE DATABASE {name}"))
    url = make_url(admin).set(database=name).render_as_string(hide_password=False)
    try:
        # 7cf0523de01b grants default privileges FOR ROLE mycoach and creates grafana_role
        # unconditionally; mirror the production roles it assumes.
        _sql(
            url,
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mycoach') "
            "THEN CREATE ROLE mycoach NOLOGIN; END IF; END $$",
            "DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_role') "
            "THEN DROP OWNED BY grafana_role; DROP ROLE grafana_role; END IF; END $$",
        )

        async def build(conn: AsyncConnection) -> None:
            await conn.run_sync(_legacy_layout)

        _run(_with_conn(url, build))
        _seed_legacy(url)
        yield url
    finally:
        _run(admin_exec(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))


def _cfg(ini: str, script_dir: str, url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("MYCOACH_DB_URL", url)
    cfg = Config(str(REPO_ROOT / ini))
    cfg.set_main_option("script_location", str(REPO_ROOT / script_dir))
    return cfg


@pytest.fixture
def migrated(pg_db: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The legacy database after bootstrap and both histories: the layout after the cutover."""
    _run(db_bootstrap.run(pg_db, finalize=False))
    command.upgrade(_cfg("alembic.ini", "alembic", pg_db, monkeypatch), "head")
    command.upgrade(_cfg("alembic_health.ini", "alembic_health", pg_db, monkeypatch), "head")
    return pg_db


COACHING_TABLES = {
    "users", "workout_routines", "routine_days", "routine_exercises", "weekly_plans",
    "planned_sessions", "coaching_insights", "mesocycle_configs", "prompt_logs",
    "sport_profiles", "weekly_availabilities", "default_availability", "job_runs",
    "prescription_fulfillments",
}  # fmt: skip
HEALTH_TABLES = {
    "athletes", "canonical_activities", "gym_workout_details", "daily_health_snapshots",
    "data_source_configs", "source_records", "canonical_activity_sources", "exercises",
}  # fmt: skip


def _tables(url: str, schema: str) -> set[str]:
    return {
        r[0]
        for r in _rows(
            url,
            f"SELECT tablename FROM pg_tables WHERE schemaname = '{schema}' "
            "AND tablename NOT LIKE 'alembic_version%'",
        )
    }


def test_tables_land_in_their_schemas_owned_by_their_owner(migrated: str) -> None:
    assert _tables(migrated, "coaching") == COACHING_TABLES
    assert _tables(migrated, "health") == HEALTH_TABLES
    assert _tables(migrated, "public") == set()
    owners = _rows(
        migrated,
        "SELECT schemaname, tableowner FROM pg_tables WHERE schemaname IN ('health','coaching') "
        "GROUP BY 1, 2",
    )
    assert sorted(owners) == [("coaching", "coaching_owner"), ("health", "health_owner")]


def test_two_histories_each_have_their_own_version_table(migrated: str) -> None:
    assert _scalar(migrated, "SELECT version_num FROM public.alembic_version") == "c187a1b2c3d4"
    assert _scalar(migrated, "SELECT version_num FROM health.alembic_version") == "h186a1b2c3d4"


def test_coaching_role_gets_a_permission_error_against_health(migrated: str) -> None:
    assert (
        _scalar(migrated, "SELECT has_schema_privilege('coaching_role', 'health', 'USAGE')")
        is False
    )
    for statement in (
        "SELECT * FROM health.canonical_activities",
        "SELECT * FROM health.athletes",
        "SELECT * FROM health.gym_workout_details",
        "SELECT * FROM health.source_records",
        "SELECT * FROM health.daily_health_snapshots",
        # the cross-schema join the seam exists to make fail
        "SELECT * FROM coaching.planned_sessions p JOIN health.canonical_activities a ON a.external_id = p.activity_external_id",
    ):
        with pytest.raises(Exception) as err:
            _as_role(migrated, "coaching_role", statement)
        assert "permission denied" in str(err.value)


def test_health_role_holds_nothing_on_coaching(migrated: str) -> None:
    assert (
        _scalar(migrated, "SELECT has_schema_privilege('health_role', 'coaching', 'USAGE')")
        is False
    )
    with pytest.raises(Exception, match="permission denied"):
        _as_role(migrated, "health_role", "SELECT * FROM coaching.users")


def test_runtime_roles_do_dml_but_never_ddl(migrated: str) -> None:
    assert _as_role(
        migrated, "health_role", "SELECT count(*) FROM health.canonical_activities"
    ) == [(1,)]
    _as_role(
        migrated, "health_role", "UPDATE health.canonical_activities SET notes = 'x' WHERE id = 1"
    )
    _as_role(migrated, "coaching_role", "SELECT count(*) FROM coaching.users")
    for role, statement in (
        ("health_role", "CREATE TABLE health.evil (id int)"),
        ("health_role", "ALTER TABLE health.canonical_activities ADD COLUMN evil int"),
        ("health_role", "DROP TABLE health.canonical_activities"),
        ("coaching_role", "CREATE TABLE coaching.evil (id int)"),
        ("coaching_role", "DROP TABLE coaching.users"),
        ("health_role", "UPDATE health.alembic_version SET version_num = 'x'"),
    ):
        with pytest.raises(Exception, match="permission denied|must be owner"):
            _as_role(migrated, role, statement)


def test_data_survives_and_the_prescription_moved_to_coaching(migrated: str) -> None:
    assert _rows(
        migrated,
        "SELECT activity_external_id, exercise_title, set_index, prescribed_weight_kg, "
        "prescribed_reps FROM coaching.prescription_fulfillments",
    ) == [("uuid-1", "Squat", 1, 102.5, 5)]
    assert _rows(migrated, "SELECT id FROM health.athletes") == [(7,)]
    assert _rows(migrated, "SELECT id, health_athlete_id FROM coaching.users") == [(7, 7)]
    assert _rows(migrated, "SELECT athlete_id FROM health.canonical_activities") == [(7,)]
    assert _scalar(migrated, "SELECT count(*) FROM health.gym_workout_details") == 2
    cols = {
        r[0]
        for r in _rows(
            migrated,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'health' AND table_name = 'gym_workout_details'",
        )
    }
    assert not {"prescribed_weight_kg", "prescribed_reps"} & cols


def test_no_foreign_key_crosses_the_seam(migrated: str) -> None:
    crossing = _rows(
        migrated,
        "SELECT conrelid::regclass::text, confrelid::regclass::text FROM pg_constraint c "
        "WHERE contype = 'f' AND "
        "(conrelid::regclass::text LIKE 'health.%' AND confrelid::regclass::text NOT LIKE 'health.%' "
        " OR conrelid::regclass::text LIKE 'coaching.%' AND confrelid::regclass::text "
        "NOT LIKE 'coaching.%')",
    )
    assert crossing == []


def test_analytics_views_read_health_and_grafana_still_reads_only_them(migrated: str) -> None:
    views = _rows(
        migrated,
        "SELECT viewname, viewowner FROM pg_views WHERE schemaname = 'health_analytics' ORDER BY 1",
    )
    assert views == [
        ("activities", "health_owner"),
        ("daily_metrics", "health_owner"),
        ("gym_sets", "health_owner"),
    ]
    # No view reads a public (pre-seam) or coaching table any more.
    definitions = " ".join(
        r[0]
        for r in _rows(
            migrated, "SELECT definition FROM pg_views WHERE schemaname = 'health_analytics'"
        )
    )
    assert "public." not in definitions and "coaching." not in definitions
    # Additive: the old user_id column is still published next to athlete_id.
    assert _rows(migrated, "SELECT athlete_id, user_id FROM health_analytics.activities") == [
        (7, 7)
    ]
    gym_cols = {
        r[0]
        for r in _rows(
            migrated,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'health_analytics' AND table_name = 'gym_sets'",
        )
    }
    assert "prescribed_weight_kg" not in gym_cols
    assert _as_role(migrated, "grafana_role", "SELECT count(*) FROM health_analytics.gym_sets") == [
        (2,)
    ]
    with pytest.raises(Exception, match="permission denied"):
        _as_role(migrated, "grafana_role", "SELECT * FROM health.canonical_activities")


def test_health_refuses_to_run_ahead_of_coaching(
    pg_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(db_bootstrap.run(pg_db, finalize=False))
    with pytest.raises(Exception, match="prescription_fulfillments"):
        command.upgrade(_cfg("alembic_health.ini", "alembic_health", pg_db, monkeypatch), "head")
    # Nothing was dropped or moved.
    assert (
        _scalar(
            pg_db,
            "SELECT count(*) FROM public.gym_workout_details WHERE prescribed_reps IS NOT NULL",
        )
        == 1
    )


def test_coaching_refuses_to_run_before_bootstrap(
    pg_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(Exception, match="db_bootstrap"):
        command.upgrade(_cfg("alembic.ini", "alembic", pg_db, monkeypatch), "head")
    # Nothing moved, and the stamp is still the pre-seam head.
    assert _scalar(pg_db, "SELECT to_regclass('public.users') IS NOT NULL") is True
    assert _scalar(pg_db, "SELECT version_num FROM public.alembic_version") == LEGACY_HEAD


def test_downgrade_round_trips_on_postgres(migrated: str, monkeypatch: pytest.MonkeyPatch) -> None:
    command.downgrade(_cfg("alembic_health.ini", "alembic_health", migrated, monkeypatch), "base")
    command.downgrade(_cfg("alembic.ini", "alembic", migrated, monkeypatch), LEGACY_HEAD)

    assert _tables(migrated, "public") >= COACHING_TABLES - {"prescription_fulfillments"}
    assert _rows(
        migrated,
        "SELECT prescribed_weight_kg, prescribed_reps FROM public.gym_workout_details "
        "WHERE set_index = 1",
    ) == [(102.5, 5)]
    assert _rows(migrated, "SELECT user_id FROM public.activities") == [(7,)]
    assert _scalar(migrated, "SELECT count(*) FROM health_analytics.gym_sets") == 2


def test_bootstrap_is_idempotent_and_prints_reviewable_sql(pg_db: str) -> None:
    _run(db_bootstrap.run(pg_db, finalize=False))
    _run(db_bootstrap.run(pg_db, finalize=True))
    assert db_roles.bootstrap_statements()
    assert (
        _scalar(pg_db, "SELECT has_schema_privilege('coaching_role', 'health', 'USAGE')") is False
    )


def test_a_fresh_database_built_from_the_models_gets_the_same_seam(pg_db: str) -> None:
    """The fresh-install path (no migration replay on Postgres, docs/postgres-cutover.md):
    bootstrap, ``create_all``, ``--finalize`` gives the same schemas and grants."""
    _sql(
        pg_db,
        "DROP SCHEMA health_analytics CASCADE",
        "DROP SCHEMA public CASCADE",
        "CREATE SCHEMA public",
    )
    _run(db_bootstrap.run(pg_db, finalize=False))

    async def build(conn: AsyncConnection) -> None:
        await conn.run_sync(Base.metadata.create_all)

    _run(_with_conn(pg_db, build))
    _run(db_bootstrap.run(pg_db, finalize=True))

    assert _tables(pg_db, "health") == HEALTH_TABLES
    assert _tables(pg_db, "coaching") == COACHING_TABLES
    assert _as_role(pg_db, "health_role", "SELECT count(*) FROM health.canonical_activities") == [
        (0,)
    ]
    with pytest.raises(Exception, match="permission denied"):
        _as_role(pg_db, "coaching_role", "SELECT count(*) FROM health.canonical_activities")
