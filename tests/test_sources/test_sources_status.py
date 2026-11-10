"""Tests for GET /api/sources/status endpoint."""

from datetime import date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.activity import Activity
from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.user import User

pytestmark = pytest.mark.asyncio


async def _create_user(session: AsyncSession) -> User:
    user = User(
        id=1,
        email="test@example.com",
        name="Test User",
        fitness_level="intermediate",
    )
    session.add(user)
    await session.flush()
    return user


async def test_status_no_data(client):
    """Status returns 'never' for both sources when no data exists."""
    resp = await client.get("/api/sources/status")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sources"]) == 2

    garmin = next(s for s in data["sources"] if s["source_type"] == "garmin")
    hevy = next(s for s in data["sources"] if s["source_type"] == "hevy_csv")

    assert garmin["sync_status"] == "never"
    assert garmin["last_sync_at"] is None
    assert garmin["enabled"] is True

    assert hevy["sync_status"] == "never"
    assert hevy["last_sync_at"] is None
    assert hevy["enabled"] is True


async def test_status_with_garmin_health(client, setup_db):
    """Status shows 'ok' for garmin when health snapshots exist."""
    from tests.conftest import test_session

    async with test_session() as session:
        await _create_user(session)
        snapshot = DailyHealthSnapshot(
            user_id=1,
            snapshot_date=date(2024, 6, 10),
            data_source="garmin",
            created_at=datetime(2024, 6, 10, 6, 0, 0),
        )
        session.add(snapshot)
        await session.commit()

    resp = await client.get("/api/sources/status")
    assert resp.status_code == 200
    data = resp.json()

    garmin = next(s for s in data["sources"] if s["source_type"] == "garmin")
    assert garmin["sync_status"] == "ok"
    assert garmin["last_sync_at"] is not None


async def _intake(session: AsyncSession, source: str, external_id: str) -> None:
    from mycoach.sources.intake import intake_source_record

    await intake_source_record(
        session,
        athlete_id=1,
        source=source,
        external_id=external_id,
        raw_payload={},
        canonical=Activity(
            user_id=1,
            sport="gym",
            title="Push Day",
            start_time=datetime(2024, 6, 10, 9, 0, 0),
            data_source=source,
        ),
    )


async def test_status_with_hevy_activity(client, setup_db):
    """Status shows 'ok' for hevy when a hevy source record exists."""
    from tests.conftest import test_session

    async with test_session() as session:
        await _create_user(session)
        await _intake(session, "hevy", "h1")
        await session.commit()

    resp = await client.get("/api/sources/status")
    assert resp.status_code == 200
    data = resp.json()

    hevy = next(s for s in data["sources"] if s["source_type"] == "hevy_csv")
    assert hevy["sync_status"] == "ok"
    assert hevy["last_sync_at"] is not None

    # Garmin should still be 'never'
    garmin = next(s for s in data["sources"] if s["source_type"] == "garmin")
    assert garmin["sync_status"] == "never"


async def test_status_reads_source_records_not_a_merged_value(client, setup_db):
    """A Garmin record linked onto a hevy activity still counts for both sources.

    Nothing takes the value "merged" any more: each source's status comes from its own
    source records (#185).
    """
    from mycoach.sources.reconcile import link_garmin_to_gym
    from tests.conftest import test_session

    async with test_session() as session:
        await _create_user(session)
        await _intake(session, "hevy", "h1")
        await _intake(session, "garmin", "g1")
        assert (await link_garmin_to_gym(session, 1)).linked == 1
        await session.commit()

    resp = await client.get("/api/sources/status")
    data = resp.json()

    garmin = next(s for s in data["sources"] if s["source_type"] == "garmin")
    hevy = next(s for s in data["sources"] if s["source_type"] == "hevy_csv")
    assert garmin["sync_status"] == "ok"
    assert hevy["sync_status"] == "ok"
