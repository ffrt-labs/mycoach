"""The module boundary on a real Postgres, as two separate logins (ticket #186).

Skipped unless ``MYCOACH_TEST_PG_URL`` is set. Coaching's code runs on a login that is a member of
``coaching_role`` only and health's on one that is a member of ``health_role`` only: the
``mycoach_app`` bridge login (a member of both) is gone. The app's flows must work on that, and
the coaching login must get a permission error the moment it names a health table.
"""

from datetime import date, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from mycoach import health
from mycoach.coaching.context import get_activity_with_details
from mycoach.coaching.workout_log import log_workouts
from mycoach.health import runtime as health_runtime
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.sources.workout_import import WorkoutImport, WorkoutSetImport
from tests.test_migrations.test_schema_seam_postgres import (  # noqa: F401  (fixtures)
    PG_URL,
    _cfg,
    _run,
    _sql,
    migrated,
    pg_db,
)

pytestmark = pytest.mark.skipif(not PG_URL, reason="MYCOACH_TEST_PG_URL not set")

HEALTH_LOGIN = "seam_health_login"
COACHING_LOGIN = "seam_coaching_login"


def _login_url(url: str, login: str) -> str:
    return make_url(url).set(username=login, password="x").render_as_string(hide_password=False)


@pytest.fixture
def logins(migrated: str) -> tuple[str, str]:  # noqa: F811
    """One login per runtime role, neither a member of the other's role."""
    for login, role in ((HEALTH_LOGIN, "health_role"), (COACHING_LOGIN, "coaching_role")):
        _sql(
            migrated,
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{login}') "
            f"THEN CREATE ROLE {login} LOGIN PASSWORD 'x'; END IF; END $$",
            f"GRANT {role} TO {login}",
        )
    return _login_url(migrated, HEALTH_LOGIN), _login_url(migrated, COACHING_LOGIN)


def test_each_login_holds_only_its_own_role(logins: tuple[str, str], migrated: str) -> None:  # noqa: F811
    from tests.test_migrations.test_schema_seam_postgres import _rows

    members = _rows(
        migrated,
        "SELECT r.rolname, g.rolname FROM pg_auth_members m "
        "JOIN pg_roles r ON r.oid = m.member JOIN pg_roles g ON g.oid = m.roleid "
        f"WHERE r.rolname IN ('{HEALTH_LOGIN}', '{COACHING_LOGIN}') ORDER BY 1",
    )
    assert members == [(COACHING_LOGIN, "coaching_role"), (HEALTH_LOGIN, "health_role")]


def test_the_coaching_login_gets_a_permission_error_on_every_health_table(
    logins: tuple[str, str],
) -> None:
    _, coaching_url = logins

    async def go() -> list[str]:
        engine = create_async_engine(coaching_url)
        refused = []
        try:
            for statement in (
                "SELECT * FROM health.canonical_activities",
                "SELECT * FROM health.source_records",
                "SELECT * FROM health.gym_workout_details",
                "SELECT * FROM health.daily_health_snapshots",
                "UPDATE health.canonical_activities SET notes = 'x'",
                "SELECT * FROM coaching.planned_sessions p "
                "JOIN health.canonical_activities a ON a.external_id = p.activity_external_id",
            ):
                async with engine.connect() as conn:
                    with pytest.raises(DBAPIError, match="permission denied"):
                        await conn.execute(text(statement))
                    refused.append(statement)
            async with engine.connect() as conn:  # its own schema still works
                await conn.execute(text("SELECT count(*) FROM coaching.users"))
        finally:
            await engine.dispose()
        return refused

    assert len(_run(go())) == 6


def test_the_health_login_gets_a_permission_error_on_coaching(logins: tuple[str, str]) -> None:
    health_url, _ = logins

    async def go() -> None:
        engine = create_async_engine(health_url)
        try:
            async with engine.connect() as conn:
                with pytest.raises(DBAPIError, match="permission denied"):
                    await conn.execute(text("SELECT * FROM coaching.users"))
        finally:
            await engine.dispose()

    _run(go())


def test_the_app_flows_work_on_two_separate_logins(
    logins: tuple[str, str],
    migrated: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logger push with a plan claim, then the post-workout read: coaching never touches health
    SQL, health never touches coaching."""
    health_url, coaching_url = logins

    async def go() -> None:
        health_engine = create_async_engine(health_url)
        coaching_engine = create_async_engine(coaching_url)
        coaching_sessions = async_sessionmaker(coaching_engine, expire_on_commit=False)
        previous = health_runtime._factory  # restored afterwards
        health_runtime.configure(async_sessionmaker(health_engine, expire_on_commit=False))
        monkeypatch.setattr("mycoach.database.async_session", coaching_sessions)
        try:
            async with coaching_sessions() as session:
                plan = WeeklyPlan(
                    user_id=7, week_start=date(2024, 6, 10), status="active", summary="p"
                )
                session.add(plan)
                await session.flush()
                planned = PlannedSession(
                    plan_id=plan.id, day_of_week=1, sport="gym", title="Push", track="gym"
                )
                session.add(planned)
                await session.commit()
                planned_id = planned.id

                result = await log_workouts(
                    session,
                    7,
                    [
                        WorkoutImport(
                            title="Push",
                            start_time=datetime(2024, 6, 11, 9, 0),
                            external_id="uuid-2",
                            planned_session_id=planned_id,
                            sets=[
                                WorkoutSetImport(
                                    exercise_title="Bench Press",
                                    set_index=1,
                                    weight_kg=80,
                                    reps=8,
                                    prescribed_weight_kg=82.5,
                                    prescribed_reps=6,
                                )
                            ],
                        )
                    ],
                    source="logger",
                )
                assert result.activities_created == 1
                assert not result.errors

                # coaching's claim landed in coaching, keyed by the external id
                claimed = (
                    await session.execute(
                        select(PlannedSession.activity_external_id, PlannedSession.completed)
                        .where(PlannedSession.id == planned_id)
                        .execution_options(populate_existing=True)
                    )
                ).one()
                assert tuple(claimed) == ("uuid-2", True)

                # the post-workout read: health's facts merged with coaching's prescription
                activity, details = await get_activity_with_details(session, "uuid-2", 7)
                assert activity["external_id"] == "uuid-2"
                assert details[0]["weight_kg"] == 80
                assert details[0]["prescribed_weight_kg"] == 82.5

            # health's side: recorded exactly once, nothing coaching-shaped on it
            fetched = await health.get_activity(7, "uuid-2")
            assert fetched is not None and "planned_session_id" not in fetched
        finally:
            health_runtime.configure(previous)
            await health_engine.dispose()
            await coaching_engine.dispose()

    _run(go())


def test_the_migrations_leave_every_external_id_unique_and_settled(
    migrated: str,  # noqa: F811
) -> None:
    from tests.test_migrations.test_schema_seam_postgres import _rows, _scalar

    assert _scalar(
        migrated, "SELECT count(*) FROM health.canonical_activities WHERE external_id IS NULL"
    ) == 0
    assert _rows(
        migrated, "SELECT DISTINCT settlement_reason FROM health.canonical_activities"
    ) == [("reconciled",)]
    with pytest.raises(Exception, match="uq_canonical_activities_athlete_external_id"):
        _sql(
            migrated,
            "INSERT INTO health.canonical_activities (athlete_id, sport, title, start_time, "
            "data_source, external_id, created_at) VALUES "
            "(7, 'gym', 'dup', now(), 'logger', 'uuid-1', now())",
        )


def test_the_analytics_views_are_unchanged_by_186(
    migrated: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The #113 additive-only rule: a view's published columns survive #186 untouched."""
    from tests.test_migrations.test_schema_seam_postgres import _rows

    def columns() -> dict[str, list[tuple[str, str]]]:
        return {
            v: _rows(
                migrated,
                "SELECT column_name, data_type FROM information_schema.columns "
                f"WHERE table_schema = 'health_analytics' AND table_name = '{v}' "
                "ORDER BY ordinal_position",
            )
            for v in ("daily_metrics", "activities", "gym_sets")
        }

    after = columns()
    command.downgrade(_cfg("alembic.ini", "alembic", migrated, monkeypatch), "c184a1b2c3d4")
    health_cfg = _cfg("alembic_health.ini", "alembic_health", migrated, monkeypatch)
    command.downgrade(health_cfg, "h185a1b2c3d4")
    before = columns()
    assert after == before
    for view in ("daily_metrics", "activities", "gym_sets"):
        _rows(migrated, f"SELECT * FROM health_analytics.{view} LIMIT 1")


def test_the_migrate_tool_rekeys_a_legacy_database_in_one_run(
    pg_db: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cutover path: a pre-seam database with a plan linked to an activity by internal id
    goes through bootstrap and ``mycoach.tools.migrate`` (coaching to c184, health, the rest of
    coaching) and comes out keyed by the activity's external id."""
    from mycoach.tools import db_bootstrap, migrate
    from tests.test_migrations.test_schema_seam_postgres import _scalar

    _run(db_bootstrap.run(pg_db, finalize=False))
    _sql(
        pg_db,
        "INSERT INTO public.weekly_plans (id, user_id, week_start, prompt_version, status, "
        "created_at) VALUES (1, 7, '2024-06-10', 'v1', 'active', now())",
        "INSERT INTO public.planned_sessions (id, plan_id, day_of_week, sport, title, completed, "
        "track, activity_id) VALUES (1, 1, 0, 'gym', 'Push', true, 'gym', 1)",
        "INSERT INTO public.coaching_insights (id, user_id, insight_date, insight_type, content, "
        "prompt_version, activity_id, created_at) VALUES "
        "(1, 7, '2024-06-10', 'post_workout', '{}', 'v1', 1, now())",
    )
    monkeypatch.setenv("MYCOACH_DB_URL", pg_db)
    monkeypatch.setenv("MYCOACH_COACHING_OWNER_DB_URL", "")
    monkeypatch.setenv("MYCOACH_HEALTH_OWNER_DB_URL", "")

    assert migrate.main([]) == 0

    assert _scalar(pg_db, "SELECT activity_external_id FROM coaching.planned_sessions") == "uuid-1"
    assert _scalar(pg_db, "SELECT activity_external_id FROM coaching.coaching_insights") == "uuid-1"
    assert _scalar(pg_db, "SELECT version_num FROM public.alembic_version") == "c186a1b2c3d4"
    assert _scalar(pg_db, "SELECT version_num FROM health.alembic_version") == "h186a1b2c3d4"
