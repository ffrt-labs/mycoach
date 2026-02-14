"""Tests for activity API endpoints."""

from datetime import datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.user import User
from tests.conftest import test_session


async def _create_user(session: AsyncSession) -> User:
    user = User(id=1, name="Test User", email="test@example.com")
    session.add(user)
    await session.commit()
    return user


async def _create_gym_activity(
    session: AsyncSession, title: str = "Push Day", start_hour: int = 9
) -> Activity:
    activity = Activity(
        user_id=1,
        sport="gym",
        title=title,
        start_time=datetime(2024, 6, 10, start_hour, 0),
        end_time=datetime(2024, 6, 10, start_hour + 1, 15),
        duration_minutes=75,
        data_source="hevy",
    )
    session.add(activity)
    await session.flush()

    details = [
        GymWorkoutDetail(
            activity_id=activity.id,
            exercise_title="Bench Press",
            set_index=1,
            set_type="warmup",
            weight_kg=61.2,
            reps=10,
            rpe=5.0,
        ),
        GymWorkoutDetail(
            activity_id=activity.id,
            exercise_title="Bench Press",
            set_index=2,
            set_type="normal",
            weight_kg=83.9,
            reps=8,
            rpe=7.0,
        ),
    ]
    session.add_all(details)
    await session.commit()
    return activity


async def _create_swim_activity(session: AsyncSession) -> Activity:
    activity = Activity(
        user_id=1,
        sport="swimming",
        title="Pool Session",
        start_time=datetime(2024, 6, 11, 7, 0),
        end_time=datetime(2024, 6, 11, 8, 0),
        duration_minutes=60,
        avg_hr=145,
        max_hr=170,
        calories=450,
        data_source="garmin",
    )
    session.add(activity)
    await session.commit()
    return activity


# ── GET /api/activities ─────────────────────────────────────────────


async def test_list_activities(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)
        await _create_gym_activity(session)
        await _create_swim_activity(session)

    response = await client.get("/api/activities")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["page"] == 1
    assert data["per_page"] == 20
    assert len(data["items"]) == 2


async def test_list_activities_with_gym_details(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)
        await _create_gym_activity(session)

    response = await client.get("/api/activities")
    data = response.json()
    gym_item = data["items"][0]
    assert gym_item["sport"] == "gym"
    assert gym_item["gym_details"] is not None
    assert len(gym_item["gym_details"]) == 2
    assert gym_item["gym_details"][0]["exercise_title"] == "Bench Press"
    assert gym_item["gym_details"][0]["set_type"] == "warmup"
    assert gym_item["gym_details"][1]["set_type"] == "normal"


async def test_list_activities_sport_filter(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)
        await _create_gym_activity(session)
        await _create_swim_activity(session)

    response = await client.get("/api/activities?sport=swimming")
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["sport"] == "swimming"


async def test_list_activities_pagination(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)
        for i in range(5):
            await _create_gym_activity(session, title=f"Workout {i}", start_hour=9 + i)

    response = await client.get("/api/activities?page=1&per_page=2")
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["per_page"] == 2

    response2 = await client.get("/api/activities?page=3&per_page=2")
    data2 = response2.json()
    assert len(data2["items"]) == 1


async def test_list_activities_empty(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    response = await client.get("/api/activities")
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


# ── GET /api/activities/{id} ────────────────────────────────────────


async def test_get_activity_gym(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)
        activity = await _create_gym_activity(session)
        activity_id = activity.id

    response = await client.get(f"/api/activities/{activity_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Push Day"
    assert data["sport"] == "gym"
    assert len(data["gym_details"]) == 2


async def test_get_activity_swim(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)
        activity = await _create_swim_activity(session)
        activity_id = activity.id

    response = await client.get(f"/api/activities/{activity_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Pool Session"
    assert data["avg_hr"] == 145
    assert data["gym_details"] is None


async def test_get_activity_not_found(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    response = await client.get("/api/activities/999")
    assert response.status_code == 404
