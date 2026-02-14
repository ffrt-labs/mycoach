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


async def test_status_with_hevy_activity(client, setup_db):
    """Status shows 'ok' for hevy when hevy activities exist."""
    from tests.conftest import test_session

    async with test_session() as session:
        await _create_user(session)
        activity = Activity(
            user_id=1,
            sport="gym",
            title="Push Day",
            start_time=datetime(2024, 6, 10, 9, 0, 0),
            data_source="hevy",
            created_at=datetime(2024, 6, 10, 12, 0, 0),
        )
        session.add(activity)
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


async def test_status_merged_counts_for_both(client, setup_db):
    """Merged activities count toward both garmin and hevy status."""
    from tests.conftest import test_session

    async with test_session() as session:
        await _create_user(session)
        activity = Activity(
            user_id=1,
            sport="gym",
            title="Push Day",
            start_time=datetime(2024, 6, 10, 9, 0, 0),
            data_source="merged",
            created_at=datetime(2024, 6, 10, 12, 0, 0),
        )
        session.add(activity)
        await session.commit()

    resp = await client.get("/api/sources/status")
    assert resp.status_code == 200
    data = resp.json()

    garmin = next(s for s in data["sources"] if s["source_type"] == "garmin")
    hevy = next(s for s in data["sources"] if s["source_type"] == "hevy_csv")

    assert garmin["sync_status"] == "ok"
    assert hevy["sync_status"] == "ok"
