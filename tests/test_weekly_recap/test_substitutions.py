"""Substitution in week completion (#216): a leftover session filled by intent."""

import json
from datetime import date, datetime, timedelta

from mycoach.coaching.context import get_open_this_week, get_plan_adherence_for_week
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.plan import PlannedSession, SessionIntent, WeeklyPlan
from mycoach.models.user import User
from tests.conftest import test_session

WEEK = date(2026, 11, 2)  # Monday


def _zones(*minutes: float) -> str:
    return json.dumps([{"zoneNumber": i + 1, "secsInZone": m * 60} for i, m in enumerate(minutes)])


async def _plan(session: object, *planned: PlannedSession) -> int:
    user = User(email="athlete@example.com", name="Athlete", fitness_level="intermediate")
    session.add(user)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    plan = WeeklyPlan(
        user_id=user.id, week_start=WEEK, status="active", summary="p", prompt_version="v3"
    )
    session.add(plan)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    for p in planned:
        p.plan_id = plan.id
    session.add_all(planned)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    return user.id


def _sunday_run(**overrides: object) -> PlannedSession:
    fields: dict[str, object] = {
        "day_of_week": 6,
        "sport": "running",
        "title": "Long easy run",
        "duration_minutes": 90,
        "intent": SessionIntent.AEROBIC_BASE.value,
    }
    return PlannedSession(**(fields | overrides))


def _padel(user_id: int, **overrides: object) -> Activity:
    """The athlete's Sunday 2026-11-08: 101 min of padel, avg HR 118, aerobic TE 2.7."""
    fields: dict[str, object] = {
        "user_id": user_id,
        "title": "Padel",
        "sport": "padel",
        "start_time": datetime(2026, 11, 8, 10, 0),
        "duration_minutes": 101,
        "avg_hr": 118,
        "max_hr": 152,
        "hr_zones": _zones(38, 46, 13, 4, 0),
        "training_effect_aerobic": 2.7,
        "training_effect_anaerobic": 0.6,
        "data_source": "garmin",
    }
    return Activity(**(fields | overrides))


async def _gym(session: object, user_id: int, day: int) -> None:
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


class TestSubstitution:
    async def test_padel_fills_the_sunday_aerobic_base_run_as_substituted(self) -> None:
        async with test_session() as session:
            user_id = await _plan(
                session,
                PlannedSession(
                    day_of_week=0,
                    sport="gym",
                    title="Upper",
                    track="gym",
                    intent=SessionIntent.STRENGTH.value,
                ),
                _sunday_run(),
            )
            await _gym(session, user_id, day=2)
            session.add(_padel(user_id))
            await session.commit()

            result = await get_plan_adherence_for_week(
                session, user_id, WEEK, today=WEEK + timedelta(days=7)
            )

        assert result is not None
        assert result["total_sessions"] == 2
        assert result["completed_sessions"] == 2
        assert result["substituted_sessions"] == 1
        assert result["extras"] == []
        by_title = {s["title"]: s for s in result["sessions"]}
        assert by_title["Upper"]["status"] == "done"
        assert by_title["Upper"]["substitute"] is None
        run = by_title["Long easy run"]
        assert run["status"] == "substituted"
        assert run["completed"] is True
        assert run["intent"] == "aerobic_base"
        assert run["actual_day"] == "Sunday"
        assert run["on_planned_day"] is True
        assert run["substitute"] is not None
        assert run["substitute"]["sport"] == "padel"
        assert run["substitute"]["title"] == "Padel"
        assert run["substitute"]["intent"] == "aerobic_base"
        # Key metrics, so the recap can judge the stimulus delivered (#217).
        assert run["substitute"]["duration_minutes"] == 101
        assert run["substitute"]["avg_hr"] == 118
        assert run["substitute"]["training_effect_aerobic"] == 2.7

    async def test_a_short_activity_stays_an_extra(self) -> None:
        async with test_session() as session:
            user_id = await _plan(session, _sunday_run())
            session.add(_padel(user_id, duration_minutes=60))  # < 70% of 90
            await session.commit()

            result = await get_plan_adherence_for_week(
                session, user_id, WEEK, today=WEEK + timedelta(days=7)
            )

        assert result is not None
        assert result["completed_sessions"] == 0
        assert result["substituted_sessions"] == 0
        assert result["sessions"][0]["status"] == "not_done"
        assert result["sessions"][0]["substitute"] is None
        assert [(e["title"], e["day"], e["intent"]) for e in result["extras"]] == [
            ("Padel", "Sunday", "aerobic_base")
        ]

    async def test_a_wrong_intent_activity_stays_an_extra(self) -> None:
        async with test_session() as session:
            user_id = await _plan(session, _sunday_run(intent=SessionIntent.THRESHOLD.value))
            session.add(_padel(user_id))
            await session.commit()

            result = await get_plan_adherence_for_week(
                session, user_id, WEEK, today=WEEK + timedelta(days=7)
            )

        assert result is not None
        assert result["sessions"][0]["status"] == "not_done"
        assert [e["title"] for e in result["extras"]] == ["Padel"]

    async def test_a_session_with_no_intent_is_never_substituted(self) -> None:
        async with test_session() as session:
            user_id = await _plan(session, _sunday_run(intent=None))
            session.add(_padel(user_id))
            await session.commit()

            result = await get_plan_adherence_for_week(
                session, user_id, WEEK, today=WEEK + timedelta(days=7)
            )

        assert result is not None
        assert result["sessions"][0]["status"] == "not_done"
        assert result["sessions"][0]["intent"] is None

    async def test_an_unclassifiable_activity_cannot_substitute(self) -> None:
        async with test_session() as session:
            user_id = await _plan(session, _sunday_run())
            session.add(_padel(user_id, hr_zones=None))
            await session.commit()

            result = await get_plan_adherence_for_week(
                session, user_id, WEEK, today=WEEK + timedelta(days=7)
            )

        assert result is not None
        assert result["sessions"][0]["status"] == "not_done"
        assert [(e["title"], e["intent"]) for e in result["extras"]] == [("Padel", None)]

    async def test_a_same_sport_match_is_taken_before_a_substitution(self) -> None:
        """The run the athlete did fills the run; Saturday's padel is left as an extra."""
        async with test_session() as session:
            user_id = await _plan(session, _sunday_run())
            session.add(_padel(user_id, start_time=datetime(2026, 11, 7, 10, 0)))
            session.add(
                Activity(
                    user_id=user_id,
                    title="Run",
                    sport="running",
                    start_time=datetime(2026, 11, 8, 8, 0),
                    duration_minutes=40,
                    data_source="garmin",
                )
            )
            await session.commit()

            result = await get_plan_adherence_for_week(
                session, user_id, WEEK, today=WEEK + timedelta(days=7)
            )

        assert result is not None
        assert result["sessions"][0]["status"] == "done"
        assert result["substituted_sessions"] == 0
        assert [e["title"] for e in result["extras"]] == ["Padel"]

    async def test_one_activity_substitutes_for_at_most_one_session(self) -> None:
        async with test_session() as session:
            user_id = await _plan(
                session,
                _sunday_run(day_of_week=5, title="Saturday run"),
                _sunday_run(),
            )
            session.add(_padel(user_id))
            await session.commit()

            result = await get_plan_adherence_for_week(
                session, user_id, WEEK, today=WEEK + timedelta(days=7)
            )

        assert result is not None
        assert result["completed_sessions"] == 1
        assert result["substituted_sessions"] == 1
        by_title = {s["title"]: s["status"] for s in result["sessions"]}
        # Day is a soft preference: Sunday's padel fills Sunday's run.
        assert by_title == {"Saturday run": "not_done", "Long easy run": "substituted"}
        assert result["extras"] == []

    async def test_a_substituted_session_is_not_open_this_week(self) -> None:
        """Open this week (#218) is built on completion, so a substitution closes the slot."""
        async with test_session() as session:
            user_id = await _plan(
                session, _sunday_run(day_of_week=5, title="Saturday run"), _sunday_run()
            )
            session.add(_padel(user_id))
            await session.commit()

            result = await get_open_this_week(session, user_id, date(2026, 11, 8))

        assert result is not None
        assert [s["title"] for s in result["sessions"]] == ["Saturday run"]
