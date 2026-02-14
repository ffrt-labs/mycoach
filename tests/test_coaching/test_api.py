"""Tests for coaching API endpoints."""

import json
from datetime import date

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
