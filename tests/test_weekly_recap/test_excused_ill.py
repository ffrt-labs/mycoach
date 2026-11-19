"""Excused (ill) in week completion (#220): each sick day excuses one not-done session."""

from datetime import date, datetime

from mycoach.coaching.context import get_open_this_week, get_plan_adherence_for_week
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.life_context import LifeContextEpisode
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from tests.conftest import test_session

WEEK = date(2026, 11, 2)  # Monday
AFTER = date(2026, 11, 9)


async def _plan(session: object, *planned: PlannedSession) -> int:
    user = User(email="athlete@example.com", name="Athlete", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    plan = WeeklyPlan(user_id=user.id, week_start=WEEK, status="active", prompt_version="v3")
    session.add(plan)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    for p in planned:
        p.plan_id = plan.id
    session.add_all(planned)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    return user.id


def _run(day: int, title: str) -> PlannedSession:
    return PlannedSession(day_of_week=day, sport="running", title=title, duration_minutes=40)


def _ran(user_id: int, day: int) -> Activity:
    return Activity(
        user_id=user_id,
        title="Run",
        sport="running",
        start_time=datetime(2026, 11, day, 7, 0),
        duration_minutes=40,
        data_source="garmin",
    )


async def _lifted(session: object, user_id: int, day: int) -> None:
    lift = Activity(
        user_id=user_id,
        title="Upper",
        sport="gym",
        start_time=datetime(2026, 11, day, 18, 0),
        duration_minutes=60,
        data_source="logger",
    )
    session.add(lift)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    session.add(GymWorkoutDetail(activity_id=lift.id, exercise_title="Bench", set_index=1))  # type: ignore[union-attr]


def _ill(user_id: int, start: int, end: int | None, severity: str = "sick") -> LifeContextEpisode:
    return LifeContextEpisode(
        user_id=user_id,
        kind="illness",
        severity=severity,
        started_on=date(2026, 11, start),
        ended_on=None if end is None else date(2026, 11, end),
    )


def _week() -> tuple[PlannedSession, ...]:
    return (
        PlannedSession(day_of_week=0, sport="gym", title="Upper A", track="gym"),
        _run(1, "Easy run"),
        _run(2, "Tempo run"),
        PlannedSession(day_of_week=3, sport="gym", title="Upper B", track="gym"),
        _run(5, "Long run"),
    )


async def test_five_planned_sick_three_days_two_done_is_two_of_two() -> None:
    async with test_session() as session:
        user_id = await _plan(session, *_week())
        session.add(_ill(user_id, 2, 4))
        await _lifted(session, user_id, 5)
        session.add(_ran(user_id, 7))
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert result["total_sessions"] == 2
    assert result["completed_sessions"] == 2
    assert result["adherence_pct"] == 100.0
    assert result["excused_sessions"] == 3
    assert result["excused_days"] == {"excused_ill": 3}
    by_title = {s["title"]: s["status"] for s in result["sessions"]}
    assert by_title == {
        "Upper A": "excused_ill",
        "Easy run": "excused_ill",
        "Tempo run": "excused_ill",
        "Upper B": "done",
        "Long run": "done",
    }


async def test_a_sick_day_with_nothing_planned_excuses_an_earlier_not_done_session() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _run(1, "Easy run"), _run(5, "Long run"))
        session.add(_ill(user_id, 4, 4))  # Wednesday, a rest day
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert [s["status"] for s in result["sessions"]] == ["excused_ill", "not_done"]
    assert (result["completed_sessions"], result["total_sessions"]) == (0, 1)


async def test_a_sick_day_never_excuses_a_later_session() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _run(5, "Long run"))
        session.add(_ill(user_id, 2, 2))  # Monday, a rest day
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert [s["status"] for s in result["sessions"]] == ["not_done"]
    assert (result["completed_sessions"], result["total_sessions"]) == (0, 1)
    assert result["excused_sessions"] == 0


async def test_done_sessions_are_never_excused_and_extra_sick_days_excuse_nothing() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _run(1, "Easy run"), _run(5, "Long run"))
        session.add(_ill(user_id, 2, 6))
        session.add(_ran(user_id, 7))
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert [s["status"] for s in result["sessions"]] == ["excused_ill", "done"]
    assert result["excused_sessions"] == 1
    assert result["excused_days"] == {"excused_ill": 5}
    assert (result["completed_sessions"], result["total_sessions"]) == (1, 1)


async def test_mild_days_excuse_nothing() -> None:
    async with test_session() as session:
        user_id = await _plan(session, *_week())
        session.add(_ill(user_id, 2, 4, "mild"))
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert result["excused_sessions"] == 0
    assert result["excused_days"] == {}
    assert (result["completed_sessions"], result["total_sessions"]) == (0, 5)


async def test_overlapping_episodes_excuse_each_day_once() -> None:
    async with test_session() as session:
        user_id = await _plan(session, *_week())
        session.add(_ill(user_id, 2, 3))
        session.add(_ill(user_id, 3, 3))
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert result["excused_sessions"] == 2
    assert result["total_sessions"] == 3


async def test_an_excused_session_is_not_open_this_week() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _run(1, "Easy run"), _run(5, "Long run"))
        session.add(_ill(user_id, 3, None))  # ill since Tuesday, still open
        await session.commit()

        result = await get_open_this_week(session, user_id, date(2026, 11, 3))

    assert result is not None
    assert [s["title"] for s in result["sessions"]] == ["Long run"]
