"""Open this week (#218): the week's planned sessions not yet completed.

Completion is week-scoped (ADR 0005), so a session done on another day is not
open, and a session whose planned day has passed undone still is.
"""

from datetime import date, datetime

from mycoach.coaching.context import get_open_this_week
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from tests.conftest import test_session

MONDAY = date(2024, 6, 10)
WEDNESDAY = date(2024, 6, 12)


async def _week(session: object, sessions: list[PlannedSession]) -> int:
    user = User(email="t@t.com", name="T", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    plan = WeeklyPlan(user_id=user.id, week_start=MONDAY, status="active", prompt_version="v3")
    session.add(plan)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    for ps in sessions:
        ps.plan_id = plan.id
        session.add(ps)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    return user.id


async def _gym_activity(session: object, user_id: int, day: date) -> None:
    activity = Activity(
        user_id=user_id,
        title="Gym",
        sport="gym",
        start_time=datetime(day.year, day.month, day.day, 9, 0),
        data_source="hevy",
    )
    session.add(activity)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    session.add(  # type: ignore[union-attr]
        GymWorkoutDetail(activity_id=activity.id, exercise_title="Squat", set_index=1)
    )
    await session.commit()  # type: ignore[union-attr]  # health reads its own session


async def test_wednesday_with_mondays_squat_undone_lists_it() -> None:
    async with test_session() as session:
        user_id = await _week(
            session,
            [
                PlannedSession(day_of_week=0, sport="gym", title="Squat Day", track="gym"),
                PlannedSession(day_of_week=1, sport="swimming", title="Swim", completed=True),
                PlannedSession(day_of_week=2, sport="running", title="Easy Run"),
                PlannedSession(day_of_week=4, sport="gym", title="Pull Day", track="gym"),
            ],
        )

        open_week = await get_open_this_week(session, user_id, WEDNESDAY)

    assert open_week is not None
    assert [(s["day"], s["title"]) for s in open_week["sessions"]] == [
        ("Monday", "Squat Day"),
        ("Wednesday", "Easy Run"),
        ("Friday", "Pull Day"),
    ]
    assert [s["status"] for s in open_week["sessions"]] == ["overdue", "today", "upcoming"]
    assert open_week["days_remaining"] == 5  # Wednesday to Sunday, today included


async def test_a_session_done_on_another_day_is_not_open() -> None:
    async with test_session() as session:
        user_id = await _week(
            session,
            [PlannedSession(day_of_week=0, sport="gym", title="Squat Day", track="gym")],
        )
        await _gym_activity(session, user_id, date(2024, 6, 11))  # lifted Tuesday

        open_week = await get_open_this_week(session, user_id, WEDNESDAY)

    assert open_week is not None
    assert open_week["sessions"] == []


async def test_no_plan_this_week_is_none() -> None:
    async with test_session() as session:
        user = User(email="t@t.com", name="T", fitness_level="intermediate")
        session.add(user)
        await session.flush()

        assert await get_open_this_week(session, user.id, WEDNESDAY) is None


async def test_an_open_session_carries_its_intent() -> None:
    async with test_session() as session:
        user_id = await _week(
            session,
            [
                PlannedSession(day_of_week=3, sport="swimming", title="CSS", intent="threshold"),
                PlannedSession(day_of_week=4, sport="gym", title="Pull Day", track="gym"),
            ],
        )

        open_week = await get_open_this_week(session, user_id, WEDNESDAY)

    assert open_week is not None
    assert [s["intent"] for s in open_week["sessions"]] == ["threshold", None]
