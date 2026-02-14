"""Tests for coaching context data loader."""

from datetime import date, datetime, timedelta

from mycoach.coaching.context import (
    get_health_trends,
    get_recent_activities,
    get_today_health,
)
from mycoach.models.activity import Activity
from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.user import User
from tests.conftest import test_session


async def _create_user(session: object) -> int:
    """Helper to create a test user."""
    user = User(email="test@example.com", name="Test User", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]
    await session.refresh(user)  # type: ignore[union-attr]
    return user.id


class TestGetTodayHealth:
    async def test_returns_data(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            snapshot = DailyHealthSnapshot(
                user_id=user_id,
                snapshot_date=today,
                resting_hr=55,
                sleep_score=82,
                body_battery_high=80,
            )
            session.add(snapshot)
            await session.commit()

            result = await get_today_health(session, user_id, today)
            assert result["resting_hr"] == 55
            assert result["sleep_score"] == 82

    async def test_returns_empty_when_no_data(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            result = await get_today_health(session, user_id, date(2024, 6, 10))
            assert result == {}


class TestGetHealthTrends:
    async def test_returns_recent_days(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            for i in range(1, 4):
                d = today - timedelta(days=i)
                session.add(
                    DailyHealthSnapshot(user_id=user_id, snapshot_date=d, resting_hr=55 + i)
                )
            await session.commit()

            result = await get_health_trends(session, user_id, days=3, today=today)
            assert len(result) == 3
            # Most recent first
            assert result[0]["resting_hr"] == 56

    async def test_excludes_today(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            session.add(DailyHealthSnapshot(user_id=user_id, snapshot_date=today, resting_hr=55))
            await session.commit()

            result = await get_health_trends(session, user_id, days=3, today=today)
            assert len(result) == 0


class TestGetRecentActivities:
    async def test_returns_recent(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 10)
            session.add(
                Activity(
                    user_id=user_id,
                    sport="gym",
                    title="Push Day",
                    start_time=datetime(2024, 6, 9, 9, 0),
                    duration_minutes=60,
                    data_source="hevy",
                )
            )
            await session.commit()

            result = await get_recent_activities(session, user_id, days=3, today=today)
            assert len(result) == 1
            assert result[0]["title"] == "Push Day"

    async def test_empty_when_no_activities(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            result = await get_recent_activities(session, user_id, days=3, today=date(2024, 6, 10))
            assert result == []
