"""Tests for sleep coaching context queries."""

from datetime import date

from mycoach.coaching.context import get_sleep_trends
from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.user import User
from tests.conftest import test_session


async def _create_user(session: object) -> int:
    user = User(email="test@example.com", name="Test User", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]
    await session.refresh(user)  # type: ignore[union-attr]
    return user.id


class TestGetSleepTrends:
    async def test_returns_sleep_data(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 14)

            session.add(
                DailyHealthSnapshot(
                    user_id=user_id,
                    snapshot_date=date(2024, 6, 13),
                    sleep_duration_minutes=450,
                    sleep_score=85,
                    sleep_deep_minutes=90,
                    sleep_rem_minutes=100,
                    resting_hr=52,
                    hrv_status=48.0,
                    body_battery_high=85,
                )
            )
            await session.commit()

            trends = await get_sleep_trends(session, user_id, days=14, today=today)
            assert len(trends) == 1
            assert trends[0]["sleep_duration_minutes"] == 450
            assert trends[0]["sleep_score"] == 85
            assert trends[0]["resting_hr"] == 52

    async def test_returns_empty_when_no_data(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            trends = await get_sleep_trends(session, user_id, days=14, today=date(2024, 6, 14))
            assert trends == []

    async def test_respects_date_range(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 14)

            # Within 14-day range
            session.add(
                DailyHealthSnapshot(
                    user_id=user_id,
                    snapshot_date=date(2024, 6, 5),
                    sleep_score=80,
                )
            )
            # Outside 14-day range
            session.add(
                DailyHealthSnapshot(
                    user_id=user_id,
                    snapshot_date=date(2024, 5, 20),
                    sleep_score=70,
                )
            )
            await session.commit()

            trends = await get_sleep_trends(session, user_id, days=14, today=today)
            assert len(trends) == 1
            assert trends[0]["sleep_score"] == 80

    async def test_ordered_by_date_desc(self) -> None:
        async with test_session() as session:
            user_id = await _create_user(session)
            today = date(2024, 6, 14)

            for day_offset in [1, 3, 5]:
                session.add(
                    DailyHealthSnapshot(
                        user_id=user_id,
                        snapshot_date=date(2024, 6, 14 - day_offset),
                        sleep_score=80 + day_offset,
                    )
                )
            await session.commit()

            trends = await get_sleep_trends(session, user_id, days=14, today=today)
            assert len(trends) == 3
            # Most recent first
            assert trends[0]["snapshot_date"] == "2024-06-13"
            assert trends[2]["snapshot_date"] == "2024-06-09"
