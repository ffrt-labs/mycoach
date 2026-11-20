"""The weekly-plan job hands the email each session's intent and the week's Life context (#234)."""

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from mycoach.coaching.life_context import EpisodeKind
from mycoach.models.life_context import LifeContextEpisode
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from mycoach.scheduler.jobs import USER_ID, _weekly_plan
from tests.conftest import test_session as db_session

SUNDAY = date(2026, 11, 15)
MONDAY = date(2026, 11, 16)


async def _seed_plan() -> int:
    async with db_session() as session:
        session.add(User(id=USER_ID, email="a@example.com", name="A"))
        plan = WeeklyPlan(user_id=USER_ID, week_start=MONDAY, summary="Easy week")
        session.add(plan)
        await session.flush()
        session.add_all(
            [
                PlannedSession(
                    plan_id=plan.id,
                    day_of_week=3,
                    sport="swimming",
                    title="Threshold swim",
                    duration_minutes=45,
                    intent="threshold",
                ),
                PlannedSession(
                    plan_id=plan.id,
                    day_of_week=0,
                    sport="gym",
                    title="Squat Day",
                    duration_minutes=75,
                    intent="strength",
                ),
            ]
        )
        session.add(
            LifeContextEpisode(
                user_id=USER_ID,
                kind=EpisodeKind.TRAVEL,
                started_on=date(2026, 11, 18),
                ended_on=date(2026, 11, 20),
                equipment="hotel_gym",
            )
        )
        await session.commit()
        return plan.id


async def _run(
    plan_id: int, coach_notes: str | None = None, cardio_prompt_version: str | None = None
) -> MagicMock:
    engine = MagicMock()
    engine.generate_weekly_plan = AsyncMock(
        return_value=MagicMock(
            id=plan_id,
            summary="Easy week",
            availability_source="declared",
            coach_notes=coach_notes,
            cardio_prompt_version=cardio_prompt_version,
        )
    )
    with (
        patch("mycoach.scheduler.jobs.CoachingEngine", return_value=engine),
        patch("mycoach.scheduler.jobs.async_session", db_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=True)),
        patch("mycoach.scheduler.jobs.send_weekly_plan", return_value=True) as send,
    ):
        await _weekly_plan(today=SUNDAY)
    return send


async def test_the_plan_email_gets_each_sessions_intent_and_weekday() -> None:
    send = await _run(await _seed_plan())
    sessions = send.call_args.kwargs["sessions"]
    assert [(s["day_of_week"], s["day_name"], s["intent"]) for s in sessions] == [
        (0, "Monday", "strength"),
        (3, "Thursday", "threshold"),
    ]


async def test_the_plan_email_gets_the_planned_weeks_life_context() -> None:
    send = await _run(await _seed_plan())
    assert send.call_args.kwargs["week_start"] == str(MONDAY)
    life = send.call_args.kwargs["life"]
    assert [(e.kind, e.started_on, e.ended_on) for e in life.episodes] == [
        (EpisodeKind.TRAVEL, date(2026, 11, 18), date(2026, 11, 20))
    ]


async def test_a_life_context_lookup_failure_does_not_cost_the_plan_email() -> None:
    plan_id = await _seed_plan()
    with patch(
        "mycoach.scheduler.jobs.life_context_for", AsyncMock(side_effect=RuntimeError("db"))
    ):
        send = await _run(plan_id)
    assert send.call_args.kwargs["life"] is None
    assert len(send.call_args.kwargs["sessions"]) == 2


NOTES = {
    "focus": "One quality day.",
    "guardrails": [{"when": "Legs heavy", "then": "Skip the strides"}],
    "goal_assessment": "On track.",
}


async def test_the_plan_email_gets_the_plans_coach_notes() -> None:
    """#243: the focus and Guardrails stored on the plan reach the email."""
    send = await _run(await _seed_plan(), json.dumps(NOTES), "bundled:v5")
    assert send.call_args.kwargs["notes"] == NOTES
    assert send.call_args.kwargs["notes_unavailable"] is False


async def test_a_cardio_plan_without_coach_notes_says_they_are_unavailable() -> None:
    send = await _run(await _seed_plan(), None, "bundled:v5")
    assert send.call_args.kwargs["notes"] is None
    assert send.call_args.kwargs["notes_unavailable"] is True


async def test_a_gym_only_week_expects_no_coach_notes() -> None:
    send = await _run(await _seed_plan(), None, None)
    assert send.call_args.kwargs["notes_unavailable"] is False
