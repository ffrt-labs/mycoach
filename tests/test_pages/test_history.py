"""Tests for the history page route."""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.coaching import CoachingInsight
from mycoach.models.user import User
from tests.conftest import test_session

pytestmark = pytest.mark.anyio


async def _seed_user() -> User:
    async with test_session() as session:
        user = User(id=1, email="test@example.com", name="Test User")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _seed_activity(
    sport: str = "gym",
    title: str = "Morning Workout",
    start_time: datetime | None = None,
    data_source: str = "hevy",
    avg_hr: int | None = None,
    calories: int | None = None,
    duration_minutes: int | None = 60,
) -> Activity:
    if start_time is None:
        start_time = datetime(2025, 6, 10, 8, 0)
    async with test_session() as session:
        activity = Activity(
            user_id=1,
            sport=sport,
            title=title,
            start_time=start_time,
            duration_minutes=duration_minutes,
            data_source=data_source,
            avg_hr=avg_hr,
            calories=calories,
        )
        session.add(activity)
        await session.commit()
        await session.refresh(activity)
        return activity


async def test_history_page_empty(client: AsyncClient) -> None:
    """History page renders with no activities."""
    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "History" in resp.text
    assert "No activities yet" in resp.text
    assert "0 activities" in resp.text


async def test_history_page_with_activities(client: AsyncClient) -> None:
    """History page shows activities grouped by date."""
    await _seed_user()
    await _seed_activity(title="Push Day", start_time=datetime(2025, 6, 10, 8, 0))
    await _seed_activity(
        title="Swimming Laps",
        sport="swimming",
        start_time=datetime(2025, 6, 10, 18, 0),
        data_source="garmin",
    )

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "Push Day" in resp.text
    assert "Swimming Laps" in resp.text
    assert "Jun 10, 2025" in resp.text
    assert "2 activities" in resp.text


async def test_history_page_sport_filter(client: AsyncClient) -> None:
    """History page filters by sport."""
    await _seed_user()
    await _seed_activity(title="Push Day", sport="gym")
    await _seed_activity(title="Swim", sport="swimming", start_time=datetime(2025, 6, 11, 8, 0))

    # Filter for gym only
    resp = await client.get("/history?sport=gym")
    assert resp.status_code == 200
    assert "Push Day" in resp.text
    # "Swim" title should not appear as an activity card (it's in the filter chips though)
    assert "1 activity" in resp.text
    # Only gym activity card present
    assert resp.text.count("font-semibold text-gray-900 truncate") == 1


async def test_history_page_gym_details(client: AsyncClient) -> None:
    """History page shows gym exercise details."""
    await _seed_user()
    activity = await _seed_activity(title="Push Day")

    async with test_session() as session:
        detail = GymWorkoutDetail(
            activity_id=activity.id,
            exercise_title="Bench Press",
            set_index=1,
            set_type="normal",
            weight_kg=80.0,
            reps=8,
        )
        session.add(detail)
        await session.commit()

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "Bench Press" in resp.text
    assert "80.0kg" in resp.text
    assert "x 8" in resp.text


async def test_history_page_metrics(client: AsyncClient) -> None:
    """History page shows HR and calorie metrics."""
    await _seed_user()
    await _seed_activity(title="Cardio Run", sport="cardio", avg_hr=145, calories=350)

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "145 bpm" in resp.text
    assert "350" in resp.text


async def test_history_page_analysis_badge(client: AsyncClient) -> None:
    """History page shows Analyzed badge for activities with post-workout analysis."""
    await _seed_user()
    activity = await _seed_activity(title="Analyzed Workout")

    async with test_session() as session:
        insight = CoachingInsight(
            user_id=1,
            insight_type="post_workout",
            insight_date=activity.start_time.date(),
            content="analysis",
            activity_id=activity.id,
        )
        session.add(insight)
        await session.commit()

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "Analyzed" in resp.text


async def test_history_page_sport_filter_chips(client: AsyncClient) -> None:
    """History page shows sport filter chips for available sports."""
    await _seed_user()
    await _seed_activity(title="Gym", sport="gym")
    await _seed_activity(title="Swim", sport="swimming", start_time=datetime(2025, 6, 11, 8, 0))

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert 'href="/history?sport=gym"' in resp.text
    assert 'href="/history?sport=swimming"' in resp.text


async def test_history_page_pagination(client: AsyncClient) -> None:
    """History page paginates when many activities exist."""
    await _seed_user()
    base = datetime(2025, 1, 1, 8, 0)
    async with test_session() as session:
        for i in range(25):
            session.add(
                Activity(
                    user_id=1,
                    sport="gym",
                    title=f"Workout {i}",
                    start_time=base + timedelta(days=i),
                    data_source="hevy",
                )
            )
        await session.commit()

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "Page 1 of 2" in resp.text
    assert "Next" in resp.text

    resp2 = await client.get("/history?page=2")
    assert resp2.status_code == 200
    assert "Page 2 of 2" in resp2.text
    assert "Prev" in resp2.text


async def test_history_page_data_source_badge(client: AsyncClient) -> None:
    """History page shows data source badge."""
    await _seed_user()
    await _seed_activity(title="Merged Workout", data_source="merged")

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "merged" in resp.text.lower()
