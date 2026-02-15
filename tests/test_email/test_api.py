"""Tests for email preferences API endpoints."""

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
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u


async def test_get_email_preferences(client: AsyncClient, user: User) -> None:
    """GET returns all email preferences (all default True)."""
    resp = await client.get("/api/email-preferences")
    assert resp.status_code == 200
    data = resp.json()
    assert data["email_daily_briefing"] is True
    assert data["email_weekly_plan"] is True
    assert data["email_post_workout"] is True
    assert data["email_sleep_coaching"] is True
    assert data["email_weekly_recap"] is True


async def test_get_email_preferences_no_user(client: AsyncClient) -> None:
    """GET returns 404 when no user exists."""
    resp = await client.get("/api/email-preferences")
    assert resp.status_code == 404


async def test_update_email_preferences(client: AsyncClient, user: User) -> None:
    """PATCH updates only the specified fields."""
    resp = await client.patch(
        "/api/email-preferences",
        json={"email_daily_briefing": False, "email_weekly_recap": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email_daily_briefing"] is False
    assert data["email_weekly_plan"] is True
    assert data["email_weekly_recap"] is False


async def test_update_email_preferences_no_user(client: AsyncClient) -> None:
    """PATCH returns 404 when no user exists."""
    resp = await client.patch(
        "/api/email-preferences",
        json={"email_daily_briefing": False},
    )
    assert resp.status_code == 404


async def test_update_email_preferences_partial(client: AsyncClient, user: User) -> None:
    """PATCH with empty body changes nothing."""
    resp = await client.patch("/api/email-preferences", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email_daily_briefing"] is True
    assert data["email_weekly_plan"] is True
