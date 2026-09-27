"""Tests for POST /api/logger/sessions/{session_id}/unlink (#130).

Releases a wrongly claimed prescription: clears the prescription side only
(`activity_id`, `completed`) and leaves the activity untouched, so the
prescription is to-do again in the logger's "This week" list.
"""

from datetime import date, datetime

import pytest

from mycoach.models.activity import Activity
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from tests.conftest import test_session


@pytest.fixture
async def user(setup_db: None) -> User:
    async with test_session() as session:
        u = User(id=1, name="Test User", email="test@example.com")
        session.add(u)
        await session.commit()
        return u


async def _claimed_session(week_start: date) -> tuple[int, int]:
    """A planned session already claimed by an activity. Returns (session_id, activity_id)."""
    async with test_session() as session:
        plan = WeeklyPlan(user_id=1, week_start=week_start, status="active")
        session.add(plan)
        await session.flush()
        activity = Activity(
            user_id=1,
            sport="gym",
            title="Push",
            start_time=datetime(2024, 6, 10, 9, 0),
            data_source="logger",
        )
        session.add(activity)
        await session.flush()
        planned = PlannedSession(
            plan_id=plan.id,
            day_of_week=0,
            sport="gym",
            title="Push Day",
            duration_minutes=55,
            track="gym",
            completed=True,
            activity_id=activity.id,
        )
        session.add(planned)
        await session.commit()
        await session.refresh(planned)
        return planned.id, activity.id


class TestUnlinkSession:
    async def test_clears_prescription_side(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        session_id, activity_id = await _claimed_session(date(2024, 6, 10))

        resp = await client.post(f"/api/logger/sessions/{session_id}/unlink")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == session_id
        assert body["done"] is False

        async with test_session() as session:
            planned = await session.get(PlannedSession, session_id)
            assert planned is not None
            assert planned.completed is False
            assert planned.activity_id is None

            activity = await session.get(Activity, activity_id)
            assert activity is not None  # untouched, not deleted

    async def test_unknown_session_is_404(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        resp = await client.post("/api/logger/sessions/999999/unlink")
        assert resp.status_code == 404
