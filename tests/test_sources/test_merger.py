"""Tests for Garmin + Hevy data merging logic."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.user import User
from mycoach.sources.merger import merge_garmin_hevy


@pytest.fixture
async def user(setup_db: None) -> User:
    """Create a test user in the database."""
    from tests.conftest import test_session

    async with test_session() as session:
        user = User(id=1, name="Test User", email="test@example.com")
        session.add(user)
        await session.commit()
        return user


def _make_hevy_activity(
    user_id: int, title: str, start: datetime, end: datetime | None = None
) -> Activity:
    return Activity(
        user_id=user_id,
        sport="gym",
        title=title,
        start_time=start,
        end_time=end or start + timedelta(hours=1),
        duration_minutes=60,
        data_source="hevy",
    )


def _make_garmin_activity(
    user_id: int,
    garmin_id: str,
    start: datetime,
    end: datetime | None = None,
    sport: str = "gym",
) -> Activity:
    return Activity(
        user_id=user_id,
        sport=sport,
        title="Strength Training",
        start_time=start,
        end_time=end or start + timedelta(hours=1),
        duration_minutes=60,
        avg_hr=135,
        max_hr=165,
        calories=450,
        hr_zones='[{"zone": 1, "minutes": 10}]',
        training_effect_aerobic=3.2,
        training_effect_anaerobic=1.5,
        data_source="garmin",
        garmin_activity_id=garmin_id,
    )


class TestMergeGarminHevy:
    @pytest.mark.asyncio
    async def test_merge_overlapping_gym_activities(self, user: User) -> None:
        from tests.conftest import test_session

        start = datetime(2024, 6, 10, 9, 0)

        async with test_session() as session:
            hevy = _make_hevy_activity(user.id, "Push Day", start)
            session.add(hevy)
            await session.flush()

            # Add gym details to Hevy activity
            detail = GymWorkoutDetail(
                activity_id=hevy.id,
                exercise_title="Bench Press",
                set_index=1,
                set_type="normal",
                weight_kg=84.0,
                reps=8,
                rpe=7,
            )
            session.add(detail)

            garmin = _make_garmin_activity(user.id, "g123", start + timedelta(minutes=5))
            session.add(garmin)
            await session.commit()

        async with test_session() as session:
            result = await merge_garmin_hevy(session, user.id)
            await session.commit()

        assert result.merged == 1

        # Verify the merged activity
        async with test_session() as session:
            activities = (await session.execute(select(Activity))).scalars().all()
            assert len(activities) == 1

            merged = activities[0]
            assert merged.data_source == "merged"
            assert merged.title == "Push Day"  # Hevy title preserved
            assert merged.avg_hr == 135  # Garmin HR data
            assert merged.max_hr == 165
            assert merged.calories == 450
            assert merged.garmin_activity_id == "g123"
            assert merged.training_effect_aerobic == 3.2

            # Gym details still attached
            details = (await session.execute(select(GymWorkoutDetail))).scalars().all()
            assert len(details) == 1
            assert details[0].activity_id == merged.id

    @pytest.mark.asyncio
    async def test_no_merge_when_no_overlap(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            hevy = _make_hevy_activity(user.id, "Push Day", datetime(2024, 6, 10, 9, 0))
            session.add(hevy)

            # Garmin activity 3 hours later — no overlap
            garmin = _make_garmin_activity(user.id, "g123", datetime(2024, 6, 10, 14, 0))
            session.add(garmin)
            await session.commit()

        async with test_session() as session:
            result = await merge_garmin_hevy(session, user.id)
            await session.commit()

        assert result.merged == 0

        async with test_session() as session:
            activities = (await session.execute(select(Activity))).scalars().all()
            assert len(activities) == 2  # Both still separate

    @pytest.mark.asyncio
    async def test_no_merge_non_gym_garmin(self, user: User) -> None:
        """Garmin cardio activity should not merge with Hevy gym activity."""
        from tests.conftest import test_session

        start = datetime(2024, 6, 10, 9, 0)

        async with test_session() as session:
            hevy = _make_hevy_activity(user.id, "Push Day", start)
            session.add(hevy)

            garmin = _make_garmin_activity(user.id, "g123", start, sport="cardio")
            session.add(garmin)
            await session.commit()

        async with test_session() as session:
            result = await merge_garmin_hevy(session, user.id)
            await session.commit()

        assert result.merged == 0

    @pytest.mark.asyncio
    async def test_no_double_merge(self, user: User) -> None:
        """Already-merged activities should not be re-merged."""
        from tests.conftest import test_session

        start = datetime(2024, 6, 10, 9, 0)

        async with test_session() as session:
            hevy = _make_hevy_activity(user.id, "Push Day", start)
            garmin = _make_garmin_activity(user.id, "g123", start)
            session.add_all([hevy, garmin])
            await session.commit()

        # First merge
        async with test_session() as session:
            result1 = await merge_garmin_hevy(session, user.id)
            await session.commit()
        assert result1.merged == 1

        # Second merge — nothing to do
        async with test_session() as session:
            result2 = await merge_garmin_hevy(session, user.id)
            await session.commit()
        assert result2.merged == 0

    @pytest.mark.asyncio
    async def test_merge_with_time_tolerance(self, user: User) -> None:
        """Activities within 30-min tolerance should still merge."""
        from tests.conftest import test_session

        hevy_start = datetime(2024, 6, 10, 9, 0)
        hevy_end = datetime(2024, 6, 10, 10, 15)
        # Garmin starts 20 minutes after Hevy ends — within tolerance
        garmin_start = datetime(2024, 6, 10, 10, 30)

        async with test_session() as session:
            hevy = _make_hevy_activity(user.id, "Push Day", hevy_start, hevy_end)
            garmin = _make_garmin_activity(user.id, "g123", garmin_start)
            session.add_all([hevy, garmin])
            await session.commit()

        async with test_session() as session:
            result = await merge_garmin_hevy(session, user.id)
            await session.commit()

        assert result.merged == 1

    @pytest.mark.asyncio
    async def test_merge_picks_best_overlap(self, user: User) -> None:
        """When multiple Garmin activities exist, merge with the best overlap."""
        from tests.conftest import test_session

        start = datetime(2024, 6, 10, 9, 0)

        async with test_session() as session:
            hevy = _make_hevy_activity(user.id, "Push Day", start)
            session.add(hevy)

            # Close overlap
            garmin_close = _make_garmin_activity(user.id, "g_close", start + timedelta(minutes=2))
            session.add(garmin_close)

            # Further overlap
            garmin_far = _make_garmin_activity(user.id, "g_far", start + timedelta(minutes=25))
            session.add(garmin_far)
            await session.commit()

        async with test_session() as session:
            result = await merge_garmin_hevy(session, user.id)
            await session.commit()

        assert result.merged == 1

        async with test_session() as session:
            merged = (
                await session.execute(select(Activity).where(Activity.data_source == "merged"))
            ).scalar_one()
            assert merged.garmin_activity_id == "g_close"

            # The far Garmin activity should still exist (unmerged)
            remaining = (
                (await session.execute(select(Activity).where(Activity.data_source == "garmin")))
                .scalars()
                .all()
            )
            assert len(remaining) == 1
            assert remaining[0].garmin_activity_id == "g_far"

    @pytest.mark.asyncio
    async def test_no_hevy_activities(self, user: User) -> None:
        """Merge with no Hevy activities returns zero merges."""
        from tests.conftest import test_session

        async with test_session() as session:
            result = await merge_garmin_hevy(session, user.id)
            await session.commit()

        assert result.merged == 0

    @pytest.mark.asyncio
    async def test_duration_filled_from_garmin(self, user: User) -> None:
        """If Hevy activity has no duration, fill from Garmin."""
        from tests.conftest import test_session

        start = datetime(2024, 6, 10, 9, 0)

        async with test_session() as session:
            hevy = Activity(
                user_id=user.id,
                sport="gym",
                title="Push Day",
                start_time=start,
                end_time=None,
                duration_minutes=None,
                data_source="hevy",
            )
            garmin = _make_garmin_activity(user.id, "g123", start)
            session.add_all([hevy, garmin])
            await session.commit()

        async with test_session() as session:
            result = await merge_garmin_hevy(session, user.id)
            await session.commit()

        assert result.merged == 1

        async with test_session() as session:
            merged = (
                await session.execute(select(Activity).where(Activity.data_source == "merged"))
            ).scalar_one()
            assert merged.duration_minutes == 60


class TestMergeEndpoint:
    @pytest.mark.asyncio
    async def test_merge_endpoint(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        from tests.conftest import test_session

        start = datetime(2024, 6, 10, 9, 0)

        async with test_session() as session:
            hevy = _make_hevy_activity(user.id, "Push Day", start)
            garmin = _make_garmin_activity(user.id, "g123", start)
            session.add_all([hevy, garmin])
            await session.commit()

        response = await client.post("/api/sources/merge")
        assert response.status_code == 200
        data = response.json()
        assert data["activities_merged"] == 1
        assert data["errors"] == []

    @pytest.mark.asyncio
    async def test_merge_endpoint_nothing_to_merge(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        response = await client.post("/api/sources/merge")
        assert response.status_code == 200
        data = response.json()
        assert data["activities_merged"] == 0
