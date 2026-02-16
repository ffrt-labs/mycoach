"""Tests for user profile CRUD API endpoints."""

import pytest
from httpx import AsyncClient

from mycoach.models.user import User
from tests.conftest import test_session


@pytest.fixture
async def user() -> User:
    """Create a test user with ID=1."""
    async with test_session() as session:
        u = User(
            id=1,
            name="Test User",
            email="test@example.com",
            fitness_level="intermediate",
            goals="Get stronger",
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u


async def test_get_profile(client: AsyncClient, user: User) -> None:
    """GET returns the user profile."""
    resp = await client.get("/api/profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Test User"
    assert data["email"] == "test@example.com"
    assert data["fitness_level"] == "intermediate"
    assert data["goals"] == "Get stronger"
    assert data["id"] == 1


async def test_get_profile_not_found(client: AsyncClient) -> None:
    """GET returns 404 when no profile exists."""
    resp = await client.get("/api/profile")
    assert resp.status_code == 404


async def test_create_profile(client: AsyncClient) -> None:
    """POST creates a new user profile."""
    resp = await client.post(
        "/api/profile",
        json={
            "name": "New User",
            "email": "new@example.com",
            "fitness_level": "beginner",
            "goals": "Lose weight",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "New User"
    assert data["email"] == "new@example.com"
    assert data["fitness_level"] == "beginner"
    assert data["goals"] == "Lose weight"
    assert data["id"] == 1
    assert "created_at" in data


async def test_create_profile_duplicate(client: AsyncClient, user: User) -> None:
    """POST returns 409 when profile already exists."""
    resp = await client.post(
        "/api/profile",
        json={
            "name": "Another User",
            "email": "another@example.com",
        },
    )
    assert resp.status_code == 409


async def test_create_profile_default_fitness_level(client: AsyncClient) -> None:
    """POST uses default fitness level when not specified."""
    resp = await client.post(
        "/api/profile",
        json={
            "name": "Default User",
            "email": "default@example.com",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["fitness_level"] == "intermediate"


async def test_create_profile_invalid_fitness_level(client: AsyncClient) -> None:
    """POST rejects invalid fitness level."""
    resp = await client.post(
        "/api/profile",
        json={
            "name": "Bad User",
            "email": "bad@example.com",
            "fitness_level": "elite",
        },
    )
    assert resp.status_code == 422


async def test_update_profile(client: AsyncClient, user: User) -> None:
    """PUT updates the user profile."""
    resp = await client.put(
        "/api/profile",
        json={"name": "Updated Name", "goals": "Build muscle"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Updated Name"
    assert data["goals"] == "Build muscle"
    # Unchanged fields remain
    assert data["email"] == "test@example.com"
    assert data["fitness_level"] == "intermediate"


async def test_update_profile_not_found(client: AsyncClient) -> None:
    """PUT returns 404 when no profile exists."""
    resp = await client.put(
        "/api/profile",
        json={"name": "Nobody"},
    )
    assert resp.status_code == 404


async def test_update_profile_empty_body(client: AsyncClient, user: User) -> None:
    """PUT with empty body changes nothing."""
    resp = await client.put("/api/profile", json={})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test User"


async def test_create_profile_includes_email_prefs(client: AsyncClient) -> None:
    """Created profile includes default email preferences."""
    resp = await client.post(
        "/api/profile",
        json={"name": "Prefs User", "email": "prefs@example.com"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email_daily_briefing"] is True
    assert data["email_weekly_plan"] is True
    assert data["email_post_workout"] is True
    assert data["email_sleep_coaching"] is True
    assert data["email_weekly_recap"] is True
