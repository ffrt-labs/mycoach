"""Tests for the weekly plan page route."""

import json
import re
from datetime import date, datetime, timedelta

import pytest
from httpx import AsyncClient

from mycoach.config import get_settings
from mycoach.localtime import local_today
from mycoach.models.activity import Activity
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
    today = local_today(get_settings().timezone)
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
            details=json.dumps(
                {
                    "exercises": [
                        {
                            "name": "Bench Press",
                            "sets": 3,
                            "reps": "8-12",
                            "rpe": 7,
                            "rest_seconds": 90,
                            "notes": "Eccentric",
                        },
                        {
                            "name": "Rows",
                            "sets": 3,
                            "reps": "10-12",
                            "rpe": 8,
                            "rest_seconds": 75,
                            "notes": "",
                        },
                    ]
                }
            ),
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
    today = local_today(get_settings().timezone)
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
    today = local_today(get_settings().timezone)
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
            sport="running",
            title="Tempo Run",
            duration_minutes=45,
            completed=False,
        )
        session.add(ps)
        await session.commit()

    resp = await client.get("/plan")
    assert resp.status_code == 200
    assert "Tempo Run" in resp.text
    assert "Today" in resp.text


async def test_plan_page_completed_badge(client: AsyncClient) -> None:
    """Plan page shows Done badge for completed sessions."""
    await _seed_user()
    today = local_today(get_settings().timezone)
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


async def test_plan_page_tags_each_session_with_its_intent(client: AsyncClient) -> None:
    """A session with an intent shows it as a tag; one planned before intents shows none."""
    await _seed_user()
    today = local_today(get_settings().timezone)
    monday = today - timedelta(days=today.weekday())

    async with test_session() as session:
        plan = WeeklyPlan(user_id=1, week_start=monday, status="active", prompt_version="v3")
        session.add(plan)
        await session.flush()
        session.add_all(
            [
                PlannedSession(
                    plan_id=plan.id,
                    day_of_week=1,
                    sport="running",
                    title="Easy Long Run",
                    intent="aerobic_base",
                ),
                PlannedSession(
                    plan_id=plan.id, day_of_week=3, sport="swimming", title="Old Swim"
                ),
            ]
        )
        await session.commit()

    resp = await client.get("/plan")
    assert resp.status_code == 200
    assert "Aerobic base" in resp.text
    assert resp.text.count('data-intent="') == 1


async def _plan_for(monday: date, sessions: list[PlannedSession]) -> None:
    async with test_session() as session:
        plan = WeeklyPlan(user_id=1, week_start=monday, status="active", prompt_version="v3")
        session.add(plan)
        await session.flush()
        for ps in sessions:
            ps.plan_id = plan.id
            session.add(ps)
        await session.commit()


async def test_plan_page_lists_the_sessions_open_this_week(client: AsyncClient) -> None:
    """#218: the current week shows what is still owed and the days left for it."""
    await _seed_user()
    today = local_today(get_settings().timezone)
    await _plan_for(
        today - timedelta(days=today.weekday()),
        [
            PlannedSession(day_of_week=0, sport="gym", title="Squat Day", track="gym"),
            PlannedSession(day_of_week=3, sport="running", title="Tempo Run"),
            PlannedSession(day_of_week=5, sport="swimming", title="Swim", completed=True),
        ],
    )

    resp = await client.get("/plan")

    days_left = 7 - today.weekday()
    remaining = f"{days_left} day{'s' if days_left != 1 else ''} remaining"
    assert f"Open this week — 2 left · {remaining}" in resp.text


async def test_plan_page_counts_week_completion(client: AsyncClient) -> None:
    """A run done on another day of the week completes the planned run (ADR 0005),
    as Open this week counts it, though nobody ticked it."""
    await _seed_user()
    today = local_today(get_settings().timezone)
    last_monday = today - timedelta(days=today.weekday() + 7)
    await _plan_for(
        last_monday,
        [
            PlannedSession(day_of_week=0, sport="running", title="Easy Run"),
            PlannedSession(day_of_week=2, sport="swimming", title="Swim"),
        ],
    )
    async with test_session() as session:
        wednesday = last_monday + timedelta(days=2)
        session.add(
            Activity(
                user_id=1,
                title="Run",
                sport="running",
                start_time=datetime(wednesday.year, wednesday.month, wednesday.day, 7, 0),
                duration_minutes=40,
                data_source="garmin",
            )
        )
        await session.commit()

    resp = await client.get(f"/plan?week={last_monday.isoformat()}")

    assert "1/2 done" in resp.text
    assert resp.text.count(">Done</span>") == 1


def _shifted_timezone() -> str:
    """UTC+14 and UTC-12 are 26 hours apart: one is always on another date than the server."""
    return next(z for z in ("Pacific/Kiritimati", "Etc/GMT+12") if local_today(z) != date.today())


async def test_plan_page_marks_today_in_the_athletes_timezone(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tz = _shifted_timezone()
    monkeypatch.setenv("MYCOACH_TIMEZONE", tz)
    local = local_today(tz)
    await _seed_user()
    monday = local - timedelta(days=local.weekday())
    await _plan_for(monday, [PlannedSession(day_of_week=0, sport="gym", title="Squat Day")])

    resp = await client.get(f"/plan?week={monday.isoformat()}")

    today_cell = re.search(r"font-mono text-accent\">\s*(\d+)\s*<", resp.text)
    assert today_cell is not None
    assert int(today_cell.group(1)) == local.day


async def test_plan_page_for_another_week_has_no_open_list(client: AsyncClient) -> None:
    await _seed_user()
    today = local_today(get_settings().timezone)
    last_monday = today - timedelta(days=today.weekday() + 7)
    await _plan_for(last_monday, [PlannedSession(day_of_week=0, sport="gym", title="Squat Day")])

    resp = await client.get(f"/plan?week={last_monday.isoformat()}")

    assert resp.status_code == 200
    assert "Open this week" not in resp.text
