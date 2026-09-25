"""Tests for GET /api/logger/week — the training-week endpoint (#68).

Contract in the resolution on #48: one flat, server-merged payload for the
current Monday–Sunday, all sports, one shape on the phone. Coach prescribed →
weights filled; no plan → same shape from the routine, weights null.
"""

import json
from datetime import date, timedelta

import pytest

from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User


def _monday(offset_weeks: int = 0) -> date:
    today = date.today()
    return today - timedelta(days=today.weekday()) + timedelta(weeks=offset_weeks)


@pytest.fixture
async def user(setup_db: None) -> User:
    from tests.conftest import test_session

    async with test_session() as session:
        u = User(id=1, name="Test User", email="test@example.com")
        session.add(u)
        await session.commit()
        return u


def _routine_payload() -> dict:
    return {
        "name": "PPL",
        "days": [
            {
                "name": "Push Day",
                "day_of_week": 0,
                "order_index": 0,
                "exercises": [
                    {
                        "exercise_name": "Bench Press",
                        "sets": 3,
                        "rep_range": "8-10",
                        "order_index": 0,
                        "notes": "Pause at chest",
                        "superset_group": 1,
                    },
                    {
                        "exercise_name": "Overhead Press",
                        "sets": 4,
                        "rep_range": "6-8",
                        "order_index": 1,
                        "superset_group": 1,
                    },
                ],
            }
        ],
    }


async def _add_plan(week_start: date, sessions: list[PlannedSession]) -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        plan = WeeklyPlan(user_id=1, week_start=week_start, status="active")
        session.add(plan)
        await session.flush()
        for ps in sessions:
            ps.plan_id = plan.id
            session.add(ps)
        await session.commit()


def _gym_session(**overrides) -> PlannedSession:
    details = {
        "exercises": [
            {
                "exercise_id": "Barbell_Bench_Press_-_Medium_Grip",
                "name": "Bench Press",
                "target_weight_kg": 80.0,
                "sets": 3,
                "reps": "8-10",
                "rpe": 8.0,
                "rest_seconds": 120,
                "adjustment_rationale": "progressing",
                "notes": "Pause at chest",
                "superset_group": 1,
            }
        ]
    }
    defaults = dict(
        day_of_week=0,
        sport="gym",
        title="Push Day",
        duration_minutes=55,
        details=json.dumps(details),
        notes="Session notes",
        completed=False,
        track="gym",
    )
    defaults.update(overrides)
    return PlannedSession(**defaults)


class TestTrainingWeekFallback:
    @pytest.mark.asyncio
    async def test_empty_when_no_plan_and_no_routine(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        resp = await client.get("/api/logger/week")
        assert resp.status_code == 200
        body = resp.json()
        assert body["week_start"] == _monday().isoformat()
        assert body["sessions"] == []

    @pytest.mark.asyncio
    async def test_falls_back_to_routine_with_null_weights(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        assert (await client.post("/api/routines", json=_routine_payload())).status_code == 201

        resp = await client.get("/api/logger/week")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 1
        s = sessions[0]
        # A routine-built session has no PlannedSession behind it.
        assert s["id"] is None
        assert s["track"] == "gym"
        assert s["loggable"] is True
        assert s["done"] is False
        assert s["title"] == "Push Day"
        names = [e["name"] for e in s["exercises"]]
        assert names == ["Bench Press", "Overhead Press"]
        first = s["exercises"][0]
        assert first["sets"] == 3
        assert first["rep_range"] == "8-10"
        assert first["target_weight_kg"] is None  # no coach → weights null
        assert first["superset_group"] == 1


class TestTrainingWeekFromPlan:
    @pytest.mark.asyncio
    async def test_gym_session_carries_prescribed_weights(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        await _add_plan(_monday(), [_gym_session()])

        resp = await client.get("/api/logger/week")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 1
        s = sessions[0]
        assert isinstance(s["id"], int)
        assert s["loggable"] is True
        assert s["done"] is False
        assert s["duration_minutes"] == 55
        ex = s["exercises"][0]
        assert ex["name"] == "Bench Press"
        assert ex["exercise_id"] == "Barbell_Bench_Press_-_Medium_Grip"
        assert ex["target_weight_kg"] == 80.0
        assert ex["target_rpe"] == 8.0
        assert ex["rest_seconds"] == 120
        assert ex["rep_range"] == "8-10"
        assert ex["superset_group"] == 1
        # details blob is gym → no passthrough, and internal keys don't leak
        assert s["details"] is None
        assert "adjustment_rationale" not in ex

    @pytest.mark.asyncio
    async def test_done_reflects_completed(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        await _add_plan(_monday(), [_gym_session(completed=True)])

        resp = await client.get("/api/logger/week")
        assert resp.json()["sessions"][0]["done"] is True

    @pytest.mark.asyncio
    async def test_cardio_session_is_display_only_with_passthrough_details(  # type: ignore[no-untyped-def]
        self, client, user: User
    ) -> None:
        blob = {"warmup": "10 min easy", "intervals": [{"work": "3 min", "rest": "90s"}]}
        cardio = PlannedSession(
            day_of_week=2,
            sport="run",
            title="Interval Run",
            duration_minutes=40,
            details=json.dumps(blob),
            notes=None,
            track="cardio",
        )
        await _add_plan(_monday(), [cardio])

        resp = await client.get("/api/logger/week")
        s = resp.json()["sessions"][0]
        assert s["sport"] == "run"
        assert s["loggable"] is False
        assert s["exercises"] == []
        assert s["details"] == blob  # LLM-shaped, passed through untouched

    @pytest.mark.asyncio
    async def test_cardio_with_null_details_passes_through_none(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        cardio = PlannedSession(
            day_of_week=3,
            sport="swim",
            title="Easy Swim",
            duration_minutes=30,
            details=None,
            track="cardio",
        )
        await _add_plan(_monday(), [cardio])

        resp = await client.get("/api/logger/week")
        s = resp.json()["sessions"][0]
        assert s["loggable"] is False
        assert s["details"] is None

    @pytest.mark.asyncio
    async def test_all_sports_included(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        cardio = PlannedSession(
            day_of_week=1, sport="run", title="Run", track="cardio", details=None
        )
        await _add_plan(_monday(), [_gym_session(), cardio])

        resp = await client.get("/api/logger/week")
        sessions = resp.json()["sessions"]
        assert {s["sport"] for s in sessions} == {"gym", "run"}

    @pytest.mark.asyncio
    async def test_only_current_week_returned(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        # A plan for last week must not leak into this week's payload, and its
        # presence must not suppress the current-week routine fallback.
        await _add_plan(_monday(offset_weeks=-1), [_gym_session(title="Last Week")])
        assert (await client.post("/api/routines", json=_routine_payload())).status_code == 201

        resp = await client.get("/api/logger/week")
        sessions = resp.json()["sessions"]
        titles = [s["title"] for s in sessions]
        assert "Last Week" not in titles
        assert titles == ["Push Day"]  # current week has no plan → routine fallback
