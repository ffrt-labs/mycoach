"""Tests for plans API endpoints."""

import json
from datetime import date, time

from httpx import AsyncClient

from mycoach.models.availability import WeeklyAvailability
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from tests.conftest import test_session


async def _seed_user_availability_plan(session: object) -> tuple[int, int]:
    """Create user + availability + plan with sessions. Returns (user_id, plan_id)."""
    user = User(email="test@example.com", name="Test User", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]
    await session.refresh(user)  # type: ignore[union-attr]

    week_start = date(2024, 6, 10)

    # Availability
    session.add(  # type: ignore[union-attr]
        WeeklyAvailability(
            user_id=user.id,
            week_start=week_start,
            day_of_week=0,
            start_time=time(7, 0),
            duration_minutes=60,
            preferred_sport="gym",
        )
    )

    # Plan
    plan = WeeklyPlan(
        user_id=user.id,
        week_start=week_start,
        prompt_version="v1",
        status="active",
        summary="Test plan",
    )
    session.add(plan)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]

    ps = PlannedSession(
        plan_id=plan.id,
        day_of_week=0,
        sport="gym",
        title="Upper Body",
        duration_minutes=60,
        details=json.dumps({"exercises": [{"name": "Bench Press", "sets": 4}]}),
        notes="Go heavy.",
    )
    session.add(ps)  # type: ignore[union-attr]
    await session.commit()  # type: ignore[union-attr]
    return user.id, plan.id


class TestGetPlan:
    async def test_get_plan_by_id(self, client: AsyncClient) -> None:
        async with test_session() as session:
            _, plan_id = await _seed_user_availability_plan(session)

        resp = await client.get(f"/api/plans/{plan_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "Test plan"
        assert data["status"] == "active"
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["sport"] == "gym"

    async def test_get_plan_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/plans/999")
        assert resp.status_code == 404

    async def test_get_plan_sessions(self, client: AsyncClient) -> None:
        async with test_session() as session:
            _, plan_id = await _seed_user_availability_plan(session)

        resp = await client.get(f"/api/plans/{plan_id}/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Upper Body"
        assert data[0]["plan_id"] == plan_id

    async def test_get_sessions_plan_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/plans/999/sessions")
        assert resp.status_code == 404


class TestGetCurrentPlan:
    async def test_no_current_plan(self, client: AsyncClient) -> None:
        resp = await client.get("/api/plans/current")
        assert resp.status_code == 404


class TestGeneratePlan:
    async def test_generate_no_availability_409(self, client: AsyncClient) -> None:
        """Generate without availability slots returns 409."""
        async with test_session() as session:
            user = User(email="t@t.com", name="T", fitness_level="beginner")
            session.add(user)
            await session.commit()

        resp = await client.post("/api/plans/generate?week_start=2024-06-10")
        assert resp.status_code == 409
        assert "No availability" in resp.json()["detail"] or "Monday" in resp.json()["detail"]

    async def test_generate_non_monday_409(self, client: AsyncClient) -> None:
        resp = await client.post("/api/plans/generate?week_start=2024-06-12")
        assert resp.status_code == 409
