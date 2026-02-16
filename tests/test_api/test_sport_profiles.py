"""Tests for sport profile CRUD API endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.user import User
from tests.conftest import test_session


async def _create_user(session: AsyncSession) -> User:
    user = User(id=1, name="Test User", email="test@example.com")
    session.add(user)
    await session.commit()
    return user


@pytest.mark.asyncio
async def test_create_sport_profile(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    resp = await client.post(
        "/api/sport-profiles",
        json={
            "sport": "gym",
            "skill_level": "intermediate",
            "goals": "Build muscle, increase strength",
            "preferences": "Morning sessions preferred",
            "benchmarks": "Bench 80kg, Squat 100kg",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["sport"] == "gym"
    assert data["skill_level"] == "intermediate"
    assert data["goals"] == "Build muscle, increase strength"
    assert data["preferences"] == "Morning sessions preferred"
    assert data["benchmarks"] == "Bench 80kg, Squat 100kg"
    assert data["user_id"] == 1


@pytest.mark.asyncio
async def test_create_sport_profile_duplicate(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    payload = {"sport": "gym"}
    await client.post("/api/sport-profiles", json=payload)
    resp = await client.post("/api/sport-profiles", json=payload)
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_sport_profile_invalid_skill_level(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    resp = await client.post(
        "/api/sport-profiles",
        json={"sport": "gym", "skill_level": "expert"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_sport_profiles(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    await client.post("/api/sport-profiles", json={"sport": "swimming"})
    await client.post("/api/sport-profiles", json={"sport": "gym"})

    resp = await client.get("/api/sport-profiles")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    # Ordered by sport name
    assert data[0]["sport"] == "gym"
    assert data[1]["sport"] == "swimming"


@pytest.mark.asyncio
async def test_list_sport_profiles_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/sport-profiles")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_sport_profile_by_sport(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    await client.post(
        "/api/sport-profiles",
        json={"sport": "swimming", "skill_level": "beginner", "goals": "Improve technique"},
    )
    resp = await client.get("/api/sport-profiles/swimming")
    assert resp.status_code == 200
    assert resp.json()["sport"] == "swimming"
    assert resp.json()["skill_level"] == "beginner"
    assert resp.json()["goals"] == "Improve technique"


@pytest.mark.asyncio
async def test_get_sport_profile_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/sport-profiles/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_sport_profile(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    await client.post("/api/sport-profiles", json={"sport": "gym"})

    resp = await client.put(
        "/api/sport-profiles/gym",
        json={"skill_level": "advanced", "goals": "Compete in powerlifting"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["skill_level"] == "advanced"
    assert data["goals"] == "Compete in powerlifting"


@pytest.mark.asyncio
async def test_update_sport_profile_not_found(client: AsyncClient) -> None:
    resp = await client.put(
        "/api/sport-profiles/nonexistent",
        json={"skill_level": "advanced"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_sport_profile(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    await client.post("/api/sport-profiles", json={"sport": "gym"})
    resp = await client.delete("/api/sport-profiles/gym")
    assert resp.status_code == 204

    # Verify it's gone
    resp2 = await client.get("/api/sport-profiles/gym")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_delete_sport_profile_not_found(client: AsyncClient) -> None:
    resp = await client.delete("/api/sport-profiles/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_default_skill_level(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    resp = await client.post("/api/sport-profiles", json={"sport": "padel"})
    assert resp.status_code == 201
    assert resp.json()["skill_level"] == "intermediate"
