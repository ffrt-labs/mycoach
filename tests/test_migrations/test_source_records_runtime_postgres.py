"""The app's own write path on the migrated Postgres schema (ticket #185).

Skipped unless ``MYCOACH_TEST_PG_URL`` is set. Intake and Garmin linking run as
``health_role`` only (no coaching grant), the way the health side will once #186 drops the
bridge login, and replay cleanly.
"""

from datetime import datetime

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mycoach.models.activity import Activity
from mycoach.sources.intake import intake_source_record
from mycoach.sources.reconcile import link_garmin_to_gym
from tests.test_migrations.test_source_records_postgres import (  # noqa: F401  (fixtures)
    PG_URL,
    _health,
    _run,
    _scalar,
    at_184,
    migrated,  # noqa: F401
    pg_db,
)

pytestmark = pytest.mark.skipif(not PG_URL, reason="MYCOACH_TEST_PG_URL not set")


@pytest.fixture
def at_head(migrated: str, monkeypatch: pytest.MonkeyPatch) -> str:  # noqa: F811
    """The #185 layout carried to the head: the runtime path is the current one (#186)."""
    command.upgrade(_health(migrated, monkeypatch), "head")
    return migrated


def test_intake_and_linking_run_as_the_health_runtime_role_and_replay_cleanly(
    at_head: str,
) -> None:
    migrated = at_head  # the fixture name differs so it can build on ``migrated``  # noqa: F811
    # The #184 fixture's logger workout starts "now"; the Garmin record overlaps it.
    start = _scalar(
        migrated, "SELECT start_time FROM health.canonical_activities WHERE data_source = 'logger'"
    )
    assert isinstance(start, datetime)

    async def go() -> tuple[int, int, int]:
        engine = create_async_engine(migrated)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                await session.execute(text("SET LOCAL ROLE health_role"))
                first = await intake_source_record(
                    session,
                    athlete_id=7,
                    source="garmin",
                    external_id="g-200",
                    raw_payload={"activityId": 200},
                    canonical=Activity(
                        user_id=7, sport="gym", title="Legs", start_time=start, data_source="garmin"
                    ),
                )
                replay = await intake_source_record(
                    session,
                    athlete_id=7,
                    source="garmin",
                    external_id="g-200",
                    raw_payload={"activityId": 999},
                    canonical=Activity(
                        user_id=7, sport="gym", title="x", start_time=start, data_source="garmin"
                    ),
                )
                assert replay.created is False
                assert replay.source_record_id == first.source_record_id
                linked = (await link_garmin_to_gym(session, 7)).linked
                again = (await link_garmin_to_gym(session, 7)).linked
                await session.commit()
                return linked, again, first.canonical_activity_id
        finally:
            await engine.dispose()

    linked, again, garmin_canonical = _run(go())
    assert (linked, again) == (1, 0)
    # The Garmin-only canonical activity is gone; both source records hang off the logger one.
    assert (
        _scalar(
            migrated,
            f"SELECT count(*) FROM health.canonical_activities WHERE id = {garmin_canonical}",
        )
        == 0
    )
    assert (
        _scalar(
            migrated,
            "SELECT count(*) FROM health.canonical_activity_sources cs "
            "JOIN health.canonical_activities a ON a.id = cs.canonical_activity_id "
            "WHERE a.data_source = 'logger'",
        )
        == 2
    )
    assert _scalar(migrated, "SELECT count(*) FROM health.source_records") == 3
    assert (
        _scalar(
            migrated,
            "SELECT raw_payload->>'activityId' FROM health.source_records "
            "WHERE external_id = 'g-200'",
        )
        == "200"
    )
