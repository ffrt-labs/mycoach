"""Tests for the dashboard page route."""

import json
from datetime import date, timedelta

import pytest
from httpx import AsyncClient

from mycoach.models.coaching import CoachingInsight
from mycoach.models.health import DailyHealthSnapshot
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


async def test_dashboard_empty(client: AsyncClient) -> None:
    """Dashboard renders with no data."""
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "MyCoach" in resp.text
    assert "No health metrics for today" in resp.text
    assert "No daily briefing generated yet" in resp.text


async def test_dashboard_with_health(client: AsyncClient) -> None:
    """Dashboard shows health metrics when available."""
    await _seed_user()
    async with test_session() as session:
        snapshot = DailyHealthSnapshot(
            user_id=1,
            snapshot_date=date.today(),
            resting_hr=52,
            hrv_status=65.0,
            body_battery_high=85,
            sleep_score=82,
            sleep_duration_minutes=450,
            steps=8500,
            avg_stress=28,
            training_readiness=75,
        )
        session.add(snapshot)
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "52" in resp.text  # resting HR
    assert "85" in resp.text  # body battery
    assert "82" in resp.text  # sleep score
    assert "8,500" in resp.text  # steps formatted with comma


async def test_dashboard_with_briefing(client: AsyncClient) -> None:
    """Dashboard shows readiness verdict from daily briefing."""
    await _seed_user()
    briefing_content = json.dumps(
        {
            "readiness_verdict": "go_hard",
            "recovery_status": "Fully recovered and ready to train.",
            "workout_adjustments": "No adjustments needed.",
            "sleep_recommendation": "Aim for 7.5 hours tonight.",
        }
    )
    async with test_session() as session:
        insight = CoachingInsight(
            user_id=1,
            insight_date=date.today(),
            insight_type="daily_briefing",
            content=briefing_content,
            prompt_version="v1",
        )
        session.add(insight)
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    # Template emits "go hard" and relies on a CSS `capitalize` class for display casing.
    assert "go hard" in resp.text
    assert "Fully recovered" in resp.text
    assert "7.5 hours tonight" in resp.text


async def test_dashboard_with_plan(client: AsyncClient) -> None:
    """Dashboard shows today's planned workout."""
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

        planned = PlannedSession(
            plan_id=plan.id,
            day_of_week=today.weekday(),
            sport="gym",
            title="Upper Body Strength",
            duration_minutes=60,
            notes="Focus on compound movements",
            details=json.dumps({"exercises": ["Bench Press", "Rows", "OHP"]}),
            completed=False,
        )
        session.add(planned)
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Upper Body Strength" in resp.text
    assert "gym" in resp.text
    assert "60 min" in resp.text
    assert "compound movements" in resp.text


async def test_dashboard_no_session_today(client: AsyncClient) -> None:
    """Dashboard shows rest day message when plan exists but no session today."""
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

        # Add session for a different day
        other_day = (today.weekday() + 1) % 7
        planned = PlannedSession(
            plan_id=plan.id,
            day_of_week=other_day,
            sport="swimming",
            title="Endurance Swim",
            duration_minutes=45,
            completed=False,
        )
        session.add(planned)
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "rest day" in resp.text.lower()


async def test_dashboard_static_files(client: AsyncClient) -> None:
    """Static CSS and JS files are served."""
    css_resp = await client.get("/static/css/app.css")
    assert css_resp.status_code == 200

    js_resp = await client.get("/static/js/app.js")
    assert js_resp.status_code == 200

    manifest_resp = await client.get("/static/manifest.json")
    assert manifest_resp.status_code == 200
    manifest = manifest_resp.json()
    assert manifest["name"] == "MyCoach"

    icon_192_resp = await client.get("/static/icon-192.png")
    assert icon_192_resp.status_code == 200
    assert icon_192_resp.headers["content-type"] == "image/png"

    icon_512_resp = await client.get("/static/icon-512.png")
    assert icon_512_resp.status_code == 200
    assert icon_512_resp.headers["content-type"] == "image/png"
