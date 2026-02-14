"""Tests for weekly plan context helpers."""

from datetime import date, time

from mycoach.coaching.context import get_availability_for_week
from mycoach.models.availability import WeeklyAvailability
from mycoach.models.user import User
from tests.conftest import test_session


class TestGetAvailabilityForWeek:
    async def test_returns_slots(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            week = date(2024, 6, 10)
            session.add(
                WeeklyAvailability(
                    user_id=user.id,
                    week_start=week,
                    day_of_week=0,
                    start_time=time(7, 0),
                    duration_minutes=60,
                    preferred_sport="gym",
                )
            )
            session.add(
                WeeklyAvailability(
                    user_id=user.id,
                    week_start=week,
                    day_of_week=3,
                    start_time=time(18, 0),
                    duration_minutes=45,
                    preferred_sport="swimming",
                )
            )
            await session.commit()

            result = await get_availability_for_week(session, user.id, week)
            assert len(result) == 2
            assert result[0]["day_name"] == "Monday"
            assert result[0]["preferred_sport"] == "gym"
            assert result[1]["day_name"] == "Thursday"
            assert result[1]["duration_minutes"] == 45

    async def test_empty_when_no_slots(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            result = await get_availability_for_week(session, user.id, date(2024, 6, 10))
            assert result == []

    async def test_filters_by_week(self) -> None:
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            # Add slots for two different weeks
            for week in [date(2024, 6, 10), date(2024, 6, 17)]:
                session.add(
                    WeeklyAvailability(
                        user_id=user.id,
                        week_start=week,
                        day_of_week=0,
                        start_time=time(7, 0),
                        duration_minutes=60,
                        preferred_sport="gym",
                    )
                )
            await session.commit()

            result = await get_availability_for_week(session, user.id, date(2024, 6, 10))
            assert len(result) == 1
