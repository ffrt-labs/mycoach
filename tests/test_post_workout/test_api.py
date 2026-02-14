"""Tests for post-workout analysis API endpoints."""

import json
from datetime import datetime

from httpx import AsyncClient

from mycoach.models.activity import Activity
from mycoach.models.coaching import CoachingInsight
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from tests.conftest import test_session


async def _seed_user_and_activity(sport: str = "gym", title: str = "Upper Body") -> tuple[int, int]:
    """Create a user and an activity, return (user_id, activity_id)."""
    async with test_session() as session:
        user = User(email="test@example.com", name="Test User", fitness_level="intermediate")
        session.add(user)
        await session.flush()

        activity = Activity(
            user_id=user.id,
            sport=sport,
            title=title,
            start_time=datetime(2024, 6, 10, 9, 0),
            duration_minutes=60,
            avg_hr=130,
            data_source="hevy",
        )
        session.add(activity)
        await session.commit()
        return user.id, activity.id


async def _seed_analysis(user_id: int, activity_id: int) -> int:
    """Create a post-workout analysis for an activity, return insight id."""
    async with test_session() as session:
        insight = CoachingInsight(
            user_id=user_id,
            insight_date=datetime(2024, 6, 10).date(),
            insight_type="post_workout",
            content=json.dumps({"performance_summary": "Good session"}),
            prompt_version="v1",
            activity_id=activity_id,
        )
        session.add(insight)
        await session.commit()
        return insight.id


class TestGetActivityAnalysis:
    async def test_returns_existing_analysis(self, client: AsyncClient) -> None:
        user_id, activity_id = await _seed_user_and_activity()
        await _seed_analysis(user_id, activity_id)

        resp = await client.get(f"/api/activities/{activity_id}/analysis")
        assert resp.status_code == 200
        data = resp.json()
        assert data["insight_type"] == "post_workout"
        assert data["activity_id"] == activity_id

    async def test_404_when_no_analysis(self, client: AsyncClient) -> None:
        user_id, activity_id = await _seed_user_and_activity()

        resp = await client.get(f"/api/activities/{activity_id}/analysis")
        assert resp.status_code == 404

    async def test_404_for_nonexistent_activity(self, client: AsyncClient) -> None:
        resp = await client.get("/api/activities/999/analysis")
        assert resp.status_code == 404


class TestMarkSessionCompleted:
    async def test_mark_session_completed(self, client: AsyncClient) -> None:
        async with test_session() as session:
            user = User(
                email="test@example.com",
                name="Test User",
                fitness_level="intermediate",
            )
            session.add(user)
            await session.flush()

            from datetime import date

            plan = WeeklyPlan(
                user_id=user.id,
                week_start=date(2024, 6, 10),
                status="active",
                summary="Test",
            )
            session.add(plan)
            await session.flush()

            planned = PlannedSession(
                plan_id=plan.id,
                day_of_week=0,
                sport="gym",
                title="Test Session",
                duration_minutes=60,
            )
            session.add(planned)
            await session.commit()

            resp = await client.patch(f"/api/plans/{plan.id}/sessions/{planned.id}?activity_id=42")
            assert resp.status_code == 200
            data = resp.json()
            assert data["completed"] is True
            assert data["activity_id"] == 42

    async def test_mark_session_not_found(self, client: AsyncClient) -> None:
        resp = await client.patch("/api/plans/999/sessions/999")
        assert resp.status_code == 404
