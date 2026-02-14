"""Tests for health snapshot API endpoints."""

from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.user import User
from tests.conftest import test_session


async def _create_user(session: AsyncSession) -> User:
    user = User(id=1, name="Test User", email="test@example.com")
    session.add(user)
    await session.commit()
    return user


async def _create_snapshot(
    session: AsyncSession, snapshot_date: date, **kwargs: object
) -> DailyHealthSnapshot:
    snapshot = DailyHealthSnapshot(
        user_id=1,
        snapshot_date=snapshot_date,
        resting_hr=kwargs.get("resting_hr", 55),
        sleep_score=kwargs.get("sleep_score", 82),
        body_battery_high=kwargs.get("body_battery_high", 90),
        avg_stress=kwargs.get("avg_stress", 30),
        steps=kwargs.get("steps", 8000),
        data_source="garmin",
    )
    session.add(snapshot)
    await session.commit()
    return snapshot


# ── GET /api/health/today ───────────────────────────────────────────


async def test_get_today_health(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)
        await _create_snapshot(session, date.today(), resting_hr=52)

    response = await client.get("/api/health/today")
    assert response.status_code == 200
    data = response.json()
    assert data["snapshot_date"] == date.today().isoformat()
    assert data["resting_hr"] == 52


async def test_get_today_health_not_found(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    response = await client.get("/api/health/today")
    assert response.status_code == 404


# ── GET /api/health/{date} ──────────────────────────────────────────


async def test_get_health_by_date(client: AsyncClient) -> None:
    target_date = date(2024, 6, 10)
    async with test_session() as session:
        await _create_user(session)
        await _create_snapshot(session, target_date, sleep_score=90)

    response = await client.get("/api/health/2024-06-10")
    assert response.status_code == 200
    data = response.json()
    assert data["snapshot_date"] == "2024-06-10"
    assert data["sleep_score"] == 90


async def test_get_health_by_date_not_found(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    response = await client.get("/api/health/2024-01-01")
    assert response.status_code == 404


# ── GET /api/health/trends ──────────────────────────────────────────


async def test_get_health_trends(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)
        today = date.today()
        for i in range(5):
            await _create_snapshot(
                session,
                today - timedelta(days=i),
                resting_hr=55 + i,
            )

    response = await client.get("/api/health/trends?days=7")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 5
    # Should be ordered most recent first
    assert data[0]["snapshot_date"] == today.isoformat()


async def test_get_health_trends_empty(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    response = await client.get("/api/health/trends?days=30")
    assert response.status_code == 200
    assert response.json() == []


async def test_get_health_trends_respects_days(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)
        today = date.today()
        # Create snapshots spanning 10 days
        for i in range(10):
            await _create_snapshot(session, today - timedelta(days=i))

    response = await client.get("/api/health/trends?days=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 6  # today + 5 days back
