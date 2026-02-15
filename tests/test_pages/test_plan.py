"""Tests for the weekly plan page route."""

import json
from datetime import date, timedelta

import pytest
from httpx import AsyncClient

from mycoach.models.plan import PlannedSession, WeeklyPlan
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


async def test_plan_page_no_plan(client: AsyncClient) -> None:
    """Plan page renders empty state when no plan exists."""
    resp = await client.get("/plan")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "No plan for this week" in resp.text
    assert "availability" in resp.text.lower()


async def test_plan_page_with_sessions(client: AsyncClient) -> None:
    """Plan page shows all sessions for the week."""
    await _seed_user()
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    async with test_session() as session:
        plan = WeeklyPlan(
            user_id=1,
            week_start=monday,
            status="active",
            prompt_version="v1",
            summary="Focus on upper/lower split this week.",
        )
        session.add(plan)
        await session.flush()

        s1 = PlannedSession(
            plan_id=plan.id,
            day_of_week=0,
            sport="gym",
            title="Upper Body",
            duration_minutes=60,
            notes="Compound focus",
            details=json.dumps({"exercises": ["Bench Press", "Rows"]}),
            completed=True,
        )
        s2 = PlannedSession(
            plan_id=plan.id,
            day_of_week=2,
            sport="gym",
            title="Lower Body",
            duration_minutes=55,
            completed=False,
        )
        s3 = PlannedSession(
            plan_id=plan.id,
            day_of_week=4,
            sport="swimming",
            title="Endurance Swim",
            duration_minutes=45,
            completed=False,
        )
        session.add_all([s1, s2, s3])
        await session.commit()

    resp = await client.get("/plan")
    assert resp.status_code == 200
    assert "Upper Body" in resp.text
    assert "Lower Body" in resp.text
    assert "Endurance Swim" in resp.text
    assert "Compound focus" in resp.text
    assert "upper/lower split" in resp.text
    assert "Bench Press" in resp.text
    assert "Rest day" in resp.text  # At least some days are rest days


async def test_plan_page_adherence(client: AsyncClient) -> None:
    """Plan page shows adherence percentage."""
    await _seed_user()
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    async with test_session() as session:
        plan = WeeklyPlan(
            user_id=1,
            week_start=monday,
            status="active",
            prompt_version="v1",
        )
        session.add(plan)
        await session.flush()

        s1 = PlannedSession(
            plan_id=plan.id,
            day_of_week=0,
            sport="gym",
            title="Session A",
            completed=True,
        )
        s2 = PlannedSession(
            plan_id=plan.id,
            day_of_week=2,
            sport="gym",
            title="Session B",
            completed=True,
        )
        s3 = PlannedSession(
            plan_id=plan.id,
            day_of_week=4,
            sport="gym",
            title="Session C",
            completed=False,
        )
        session.add_all([s1, s2, s3])
        await session.commit()

    resp = await client.get("/plan")
    assert resp.status_code == 200
    assert "67%" in resp.text  # 2/3 = 66.67 rounded to 67
    assert "2/3 done" in resp.text


async def test_plan_page_today_highlighted(client: AsyncClient) -> None:
    """Plan page highlights today's session."""
    await _seed_user()
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    async with test_session() as session:
        plan = WeeklyPlan(
            user_id=1,
            week_start=monday,
            status="active",
            prompt_version="v1",
        )
        session.add(plan)
        await session.flush()

        ps = PlannedSession(
            plan_id=plan.id,
            day_of_week=today.weekday(),
            sport="padel",
            title="Padel Drills",
            duration_minutes=90,
            completed=False,
        )
        session.add(ps)
        await session.commit()

    resp = await client.get("/plan")
    assert resp.status_code == 200
    assert "Padel Drills" in resp.text
    assert "Today" in resp.text


async def test_plan_page_completed_badge(client: AsyncClient) -> None:
    """Plan page shows Done badge for completed sessions."""
    await _seed_user()
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    async with test_session() as session:
        plan = WeeklyPlan(
            user_id=1,
            week_start=monday,
            status="active",
            prompt_version="v1",
        )
        session.add(plan)
        await session.flush()

        # Use a day that isn't today so we get "Done" not "Today"
        other_day = (today.weekday() + 1) % 7
        ps = PlannedSession(
            plan_id=plan.id,
            day_of_week=other_day,
            sport="gym",
            title="Arms & Abs",
            completed=True,
        )
        session.add(ps)
        await session.commit()

    resp = await client.get("/plan")
    assert resp.status_code == 200
    assert "Arms &amp; Abs" in resp.text or "Arms & Abs" in resp.text
    assert "Done" in resp.text
