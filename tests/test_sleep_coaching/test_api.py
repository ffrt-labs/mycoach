"""Tests for sleep coaching API endpoints."""

import json
from datetime import date

from httpx import AsyncClient

from mycoach.models.coaching import CoachingInsight
from mycoach.models.user import User
from tests.conftest import test_session

VALID_SLEEP_CONTENT = json.dumps(
    {
        "sleep_quality_summary": "Good overall sleep quality.",
        "consistency_analysis": "Consistent bedtime.",
        "sleep_architecture": "Healthy deep/REM ratio.",
        "performance_correlation": "Good correlation.",
        "recommended_bedtime": "22:30",
        "recommended_wake_time": "06:00",
        "sleep_debt_assessment": "No sleep debt.",
        "hygiene_tips": ["Avoid caffeine", "Dim lights"],
        "key_concern": "None",
    }
)


async def _seed_user_and_sleep_coaching() -> None:
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


class TestGetSleepCoaching:
    async def test_returns_existing(self, client: AsyncClient) -> None:
        await _seed_user_and_sleep_coaching()
        resp = await client.get("/api/coaching/sleep")
        assert resp.status_code == 200
        data = resp.json()
        assert data["insight_type"] == "sleep"
        assert "22:30" in data["content"]

    async def test_404_when_none(self, client: AsyncClient) -> None:
        resp = await client.get("/api/coaching/sleep")
        assert resp.status_code == 404


class TestGenerateSleepCoaching:
    async def test_409_when_already_exists(self, client: AsyncClient) -> None:
        await _seed_user_and_sleep_coaching()
        resp = await client.post("/api/coaching/sleep/generate")
        assert resp.status_code == 409
