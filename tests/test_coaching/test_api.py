"""Tests for coaching API endpoints."""

import json
from datetime import date, timedelta

from httpx import AsyncClient

from mycoach.models.coaching import CoachingInsight
from mycoach.models.user import User
from tests.conftest import test_session

VALID_BRIEFING_CONTENT = json.dumps(
    {
        "sleep_assessment": "Good sleep.",
        "recovery_status": "Well recovered.",
        "readiness_verdict": "go_hard",
        "readiness_explanation": "All metrics look good.",
        "workout_adjustments": "No adjustments needed.",
        "sleep_recommendation": "Aim for 10:30 PM.",
        "key_metrics": {
            "body_battery": 80,
            "hrv_status": 45.0,
            "sleep_score": 82,
            "training_readiness": 75,
            "resting_hr": 55,
        },
    }
)

VALID_SLEEP_CONTENT = json.dumps(
    {
        "sleep_quality_summary": "Good overall.",
        "consistency_analysis": "Consistent bedtime.",
        "sleep_architecture": "Normal distribution.",
        "performance_correlation": "Positive.",
        "recommended_bedtime": "10:30 PM",
        "recommended_wake_time": "6:30 AM",
        "sleep_debt_assessment": "Minimal debt.",
        "hygiene_tips": ["Avoid screens", "Cool room"],
        "key_concern": "None.",
    }
)

VALID_RECAP_CONTENT = json.dumps(
    {
        "week_summary": "Great week.",
        "adherence_analysis": "100% completion.",
        "performance_highlights": ["New PR on squat", "Improved swim pace"],
        "areas_of_concern": ["Right shoulder tightness"],
        "recovery_assessment": "Well recovered.",
        "training_load_analysis": "Moderate load.",
        "next_week_recommendations": "Increase volume slightly.",
        "mesocycle_progress": "Week 2 of 4, on track.",
    }
)


async def _seed_user() -> int:
    async with test_session() as session:
        user = User(email="test@example.com", name="Test", fitness_level="intermediate")
        session.add(user)
        await session.commit()
        return user.id


async def _seed_user_and_briefing() -> None:
    """Seed a user and today's briefing."""
    async with test_session() as session:
        user = User(email="test@example.com", name="Test", fitness_level="intermediate")
        session.add(user)
        await session.commit()

        insight = CoachingInsight(
            user_id=user.id,
            insight_date=date.today(),
            insight_type="daily_briefing",
            content=VALID_BRIEFING_CONTENT,
            prompt_version="v1",
        )
        session.add(insight)
        await session.commit()


async def _seed_user_and_sleep_coaching() -> None:
    """Seed a user and today's sleep coaching."""
    async with test_session() as session:
        user = User(email="test@example.com", name="Test", fitness_level="intermediate")
        session.add(user)
        await session.commit()

        insight = CoachingInsight(
            user_id=user.id,
            insight_date=date.today(),
            insight_type="sleep",
            content=VALID_SLEEP_CONTENT,
            prompt_version="v1",
        )
        session.add(insight)
        await session.commit()


async def _seed_user_and_weekly_recap(week_start: date) -> None:
    """Seed a user and a weekly recap for the given week."""
    async with test_session() as session:
        user = User(email="test@example.com", name="Test", fitness_level="intermediate")
        session.add(user)
        await session.commit()

        insight = CoachingInsight(
            user_id=user.id,
            insight_date=week_start,
            insight_type="weekly_recap",
            content=VALID_RECAP_CONTENT,
            prompt_version="v1",
        )
        session.add(insight)
        await session.commit()


class TestGetTodayBriefing:
    async def test_returns_existing(self, client: AsyncClient) -> None:
        await _seed_user_and_briefing()
        resp = await client.get("/api/coaching/today")
        assert resp.status_code == 200
        data = resp.json()
        assert data["insight_type"] == "daily_briefing"
        assert "go_hard" in data["content"]

    async def test_404_when_none(self, client: AsyncClient) -> None:
        resp = await client.get("/api/coaching/today")
        assert resp.status_code == 404


class TestGenerateBriefing:
    async def test_409_when_already_exists(self, client: AsyncClient) -> None:
        await _seed_user_and_briefing()
        resp = await client.post("/api/coaching/today/generate")
        assert resp.status_code == 409


class TestGetSleepCoaching:
    async def test_returns_existing(self, client: AsyncClient) -> None:
        await _seed_user_and_sleep_coaching()
        resp = await client.get("/api/coaching/sleep")
        assert resp.status_code == 200
        data = resp.json()
        assert data["insight_type"] == "sleep"
        assert "Good overall" in data["content"]

    async def test_404_when_none(self, client: AsyncClient) -> None:
        resp = await client.get("/api/coaching/sleep")
        assert resp.status_code == 404


class TestGenerateSleepCoaching:
    async def test_409_when_already_exists(self, client: AsyncClient) -> None:
        await _seed_user_and_sleep_coaching()
        resp = await client.post("/api/coaching/sleep/generate")
        assert resp.status_code == 409


class TestGetWeeklyRecap:
    async def test_returns_existing(self, client: AsyncClient) -> None:
        # Use last Monday as week_start
        today = date.today()
        last_monday = today - timedelta(days=today.weekday() + 7)
        await _seed_user_and_weekly_recap(last_monday)
        resp = await client.get(f"/api/coaching/weekly-recap?week_start={last_monday}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["insight_type"] == "weekly_recap"
        assert "Great week" in data["content"]

    async def test_defaults_to_last_week(self, client: AsyncClient) -> None:
        """When no week_start is provided, defaults to last Monday."""
        today = date.today()
        last_monday = today - timedelta(days=today.weekday() + 7)
        await _seed_user_and_weekly_recap(last_monday)
        resp = await client.get("/api/coaching/weekly-recap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["insight_type"] == "weekly_recap"

    async def test_404_when_none(self, client: AsyncClient) -> None:
        resp = await client.get("/api/coaching/weekly-recap")
        assert resp.status_code == 404

    async def test_422_non_monday(self, client: AsyncClient) -> None:
        resp = await client.get("/api/coaching/weekly-recap?week_start=2024-06-12")
        assert resp.status_code == 422

    async def test_returns_specific_week(self, client: AsyncClient) -> None:
        week = date(2024, 6, 10)  # A Monday
        await _seed_user_and_weekly_recap(week)
        resp = await client.get(f"/api/coaching/weekly-recap?week_start={week}")
        assert resp.status_code == 200


class TestGenerateWeeklyRecap:
    async def test_409_when_already_exists(self, client: AsyncClient) -> None:
        week = date(2024, 6, 10)
        await _seed_user_and_weekly_recap(week)
        resp = await client.post(f"/api/coaching/weekly-recap/generate?week_start={week}")
        assert resp.status_code == 409

    async def test_422_non_monday(self, client: AsyncClient) -> None:
        resp = await client.post("/api/coaching/weekly-recap/generate?week_start=2024-06-12")
        assert resp.status_code == 409
