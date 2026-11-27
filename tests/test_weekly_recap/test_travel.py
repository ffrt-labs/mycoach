"""Travel in week completion (#221): adapted sessions count, travel days stay owed.

A session filled in adapted form by an activity on a travel day is **Adapted (travel)** and
counts as done. A session not done while travelling stays in the denominator as **Not done
(travelling)**. Only days under "no training possible" excuse a session, like sick days.
"""

import json
from datetime import date, datetime

from mycoach.coaching.context import get_open_this_week, get_plan_adherence_for_week
from mycoach.models.activity import Activity
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


def _squats(day: int, title: str = "Lower A (squat)") -> PlannedSession:
    return PlannedSession(
        day_of_week=day,
        sport="gym",
        title=title,
        track="gym",
        intent="strength",
        duration_minutes=60,
    )


def _easy_run(day: int, title: str = "Easy run") -> PlannedSession:
    return PlannedSession(
        day_of_week=day, sport="running", title=title, intent="aerobic_base", duration_minutes=40
    )


def _circuit(user_id: int, day: int, minutes: int = 25, source: str = "logger") -> Activity:
    """Hotel-room resistance work: a gym activity with no performed sets."""
    return Activity(
        user_id=user_id,
        title="Hotel leg circuit",
        sport="gym",
        start_time=datetime(2026, 11, day, 7, 0),
        duration_minutes=minutes,
        data_source=source,
    )


def _hotel_treadmill(user_id: int, day: int) -> Activity:
    zones = [{"zoneNumber": i + 1, "secsInZone": m * 60} for i, m in enumerate((5, 30, 3, 0, 0))]
    return Activity(
        user_id=user_id,
        title="Treadmill",
        sport="running",
        start_time=datetime(2026, 11, day, 7, 0),
        duration_minutes=38,
        hr_zones=json.dumps(zones),
        training_effect_aerobic=2.4,
        training_effect_anaerobic=0.3,
        data_source="garmin",
    )


def _travel(
    user_id: int,
    start: int,
    end: int | None,
    *,
    equipment: str = "none",
    no_training: bool = False,
) -> LifeContextEpisode:
    return LifeContextEpisode(
        user_id=user_id,
        kind="travel",
        started_on=date(2026, 11, start),
        ended_on=None if end is None else date(2026, 11, end),
        equipment=equipment,
        no_training=no_training,
    )


def _ill(user_id: int, start: int, end: int) -> LifeContextEpisode:
    return LifeContextEpisode(
        user_id=user_id,
        kind="illness",
        severity="sick",
        started_on=date(2026, 11, start),
        ended_on=date(2026, 11, end),
    )


def _statuses(result: object) -> dict[str, str]:
    return {s["title"]: s["status"] for s in result["sessions"]}  # type: ignore[index]


async def test_a_hotel_bodyweight_leg_circuit_fills_a_planned_squat_session_as_adapted() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _squats(1))
        session.add(_travel(user_id, 3, 6))
        session.add(_circuit(user_id, 3))
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert _statuses(result) == {"Lower A (squat)": "adapted_travel"}
    assert (result["completed_sessions"], result["total_sessions"]) == (1, 1)
    assert result["adapted_sessions"] == 1
    assert result["substituted_sessions"] == 0
    adapted = result["sessions"][0]
    assert adapted["substitute"] is not None
    assert adapted["substitute"]["title"] == "Hotel leg circuit"
    assert adapted["actual_day"] == "Tuesday"


async def test_travel_tuesday_to_friday_sick_wednesday_thursday() -> None:
    """Wed/Thu are rest with up to two excused; Tue/Fri are adapted."""
    async with test_session() as session:
        user_id = await _plan(
            session,
            _squats(1, "Lower A"),
            _easy_run(2, "Tempo run"),
            _easy_run(3, "Easy run"),
            _squats(4, "Lower B"),
        )
        session.add(_travel(user_id, 3, 6, equipment="hotel_gym"))
        session.add(_ill(user_id, 4, 5))
        session.add(_circuit(user_id, 3))
        session.add(_circuit(user_id, 6, source="garmin"))
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert _statuses(result) == {
        "Lower A": "adapted_travel",
        "Tempo run": "excused_ill",
        "Easy run": "excused_ill",
        "Lower B": "adapted_travel",
    }
    assert (result["completed_sessions"], result["total_sessions"]) == (2, 2)
    assert result["excused_days"] == {"excused_ill": 2}


async def test_sessions_not_done_while_travelling_stay_in_the_count() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _squats(1), _easy_run(2), _easy_run(5, "Long run"))
        session.add(_travel(user_id, 3, 4))
        session.add(_hotel_treadmill(user_id, 4))
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert _statuses(result) == {
        "Lower A (squat)": "not_done_travelling",
        "Easy run": "done",
        "Long run": "not_done",
    }
    assert (result["completed_sessions"], result["total_sessions"]) == (1, 3)
    assert result["excused_sessions"] == 0


async def test_a_short_circuit_does_not_fill_a_strength_slot() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _squats(1))
        session.add(_travel(user_id, 3, 3))
        session.add(_circuit(user_id, 3, minutes=15))
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert _statuses(result) == {"Lower A (squat)": "not_done_travelling"}


async def test_a_circuit_at_home_does_not_fill_a_strength_slot() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _squats(1))
        session.add(_circuit(user_id, 3))
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert _statuses(result) == {"Lower A (squat)": "not_done"}


async def test_no_training_possible_days_excuse_one_session_each() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _squats(1), _easy_run(2), _easy_run(5, "Long run"))
        session.add(_travel(user_id, 3, 4, no_training=True))
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert _statuses(result) == {
        "Lower A (squat)": "excused_travel",
        "Easy run": "excused_travel",
        "Long run": "not_done",
    }
    assert result["excused_days"] == {"excused_travel": 2}
    assert (result["completed_sessions"], result["total_sessions"]) == (0, 1)


async def test_a_day_both_sick_and_without_training_excuses_once() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _squats(1), _easy_run(2), _easy_run(5, "Long run"))
        session.add(_travel(user_id, 3, 3, no_training=True))
        session.add(_ill(user_id, 3, 3))
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert result["excused_sessions"] == 1
    assert result["excused_days"] == {"excused_ill": 1}
    assert result["total_sessions"] == 2


async def test_a_session_left_on_a_trip_is_still_open_this_week() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _squats(1), _easy_run(5, "Long run"))
        session.add(_travel(user_id, 3, None))
        await session.commit()

        result = await get_open_this_week(session, user_id, date(2026, 11, 4))

    assert result is not None
    assert [s["title"] for s in result["sessions"]] == ["Lower A (squat)", "Long run"]


def _home_ride(user_id: int, day: int) -> Activity:
    """An easy ride: delivers aerobic base under the normal substitution rules."""
    zones = [{"zoneNumber": i + 1, "secsInZone": m * 60} for i, m in enumerate((5, 30, 3, 0, 0))]
    return Activity(
        user_id=user_id,
        title="Easy ride",
        sport="cycling",
        start_time=datetime(2026, 11, day, 7, 0),
        duration_minutes=38,
        hr_zones=json.dumps(zones),
        training_effect_aerobic=2.4,
        training_effect_anaerobic=0.3,
        data_source="garmin",
    )


async def test_a_travel_day_slot_filled_at_home_is_substituted_not_adapted() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _easy_run(2))
        session.add(_travel(user_id, 3, 4))  # Tue-Wed away
        session.add(_home_ride(user_id, 6))  # Friday, home
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert _statuses(result) == {"Easy run": "substituted"}
    assert (result["substituted_sessions"], result["adapted_sessions"]) == (1, 0)


async def test_a_circuit_at_home_does_not_fill_a_travel_day_strength_slot() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _squats(1))  # Tuesday, away
        session.add(_travel(user_id, 3, 4))
        session.add(_circuit(user_id, 6))  # Friday, home
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert _statuses(result) == {"Lower A (squat)": "not_done_travelling"}


async def test_a_circuit_on_a_trip_fills_a_home_day_strength_slot_as_adapted() -> None:
    async with test_session() as session:
        user_id = await _plan(session, _squats(0))  # Monday, home
        session.add(_travel(user_id, 3, 4))
        session.add(_circuit(user_id, 3))  # Tuesday, away
        await session.commit()

        result = await get_plan_adherence_for_week(session, user_id, WEEK, today=AFTER)

    assert result is not None
    assert _statuses(result) == {"Lower A (squat)": "adapted_travel"}
