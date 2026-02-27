"""Tests for weekly recap API endpoints."""

import json
from datetime import date

from httpx import AsyncClient

from mycoach.models.coaching import CoachingInsight
from mycoach.models.user import User
from tests.conftest import test_session

VALID_RECAP_CONTENT = json.dumps(
    {
        "week_summary": "Strong week.",
        "adherence_analysis": "4/5 completed.",
        "performance_highlights": ["PR on bench", "Improved swim time"],
        "areas_of_concern": ["Elevated Resting Heart Rate"],
        "recovery_assessment": "Good recovery.",
        "training_load_analysis": "Balanced.",
        "next_week_recommendations": "Maintain volume.",
        "mesocycle_progress": "Week 3 of 4.",
    }
)


async def _seed_user_and_recap(week_start: date = date(2024, 6, 10)) -> None:
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


class TestGetWeeklyRecap:
    async def test_returns_existing(self, client: AsyncClient) -> None:
        await _seed_user_and_recap()
        resp = await client.get("/api/coaching/weekly-recap?week_start=2024-06-10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["insight_type"] == "weekly_recap"
        assert "Strong week" in data["content"]

    async def test_404_when_none(self, client: AsyncClient) -> None:
        resp = await client.get("/api/coaching/weekly-recap?week_start=2024-06-10")
        assert resp.status_code == 404

    async def test_422_non_monday(self, client: AsyncClient) -> None:
        resp = await client.get("/api/coaching/weekly-recap?week_start=2024-06-12")
        assert resp.status_code == 422


class TestGenerateWeeklyRecap:
    async def test_409_when_already_exists(self, client: AsyncClient) -> None:
        await _seed_user_and_recap()
        resp = await client.post("/api/coaching/weekly-recap/generate?week_start=2024-06-10")
        assert resp.status_code == 409

    async def test_409_non_monday(self, client: AsyncClient) -> None:
        resp = await client.post("/api/coaching/weekly-recap/generate?week_start=2024-06-12")
        assert resp.status_code == 409
