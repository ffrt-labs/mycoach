"""The settlement check rides on the Garmin-sync job, and its events reach coaching (#186, #112).

No cron of its own: the sync settles what is due inside health, then dispatches the returned
events to coaching's handlers after health's commit, each handler isolated from the sync.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mycoach import health
from mycoach.scheduler.jobs import USER_ID, _garmin_sync
from mycoach.sources.base import ImportResult
from mycoach.sources.workout_import import WorkoutImport, WorkoutSetImport
from tests.conftest import test_session

ENGINE = "mycoach.coaching.engine.CoachingEngine"


def _source() -> MagicMock:
    source = MagicMock()
    source.authenticate = AsyncMock(return_value=True)
    source.fetch_and_import = AsyncMock(return_value=ImportResult(source_type="garmin"))
    return source


async def _log(external_id: str, hours_ago: float) -> None:
    start = datetime.utcnow() - timedelta(hours=hours_ago)
    await health.record_workout(
        USER_ID,
        "logger",
        WorkoutImport(
            title="Push",
            start_time=start,
            end_time=start + timedelta(hours=1),
            external_id=external_id,
            sets=[WorkoutSetImport(exercise_title="Bench", set_index=1, weight_kg=60, reps=5)],
        ),
    )


async def _sync() -> None:
    with (
        patch("mycoach.scheduler.jobs.GarminSource", return_value=_source()),
        patch("mycoach.scheduler.jobs.async_session", test_session),
        patch("mycoach.scheduler.jobs._get_user_email_pref", AsyncMock(return_value=False)),
    ):
        await _garmin_sync()


def _engine(**kwargs: object) -> MagicMock:
    engine = MagicMock()
    engine.generate_post_workout_analysis = AsyncMock(
        return_value=MagicMock(content="{}"), **kwargs
    )
    return engine


async def test_an_activity_past_its_deadline_is_settled_by_the_sync_and_coached() -> None:
    await _log("late-1", hours_ago=30)
    engine = _engine()

    with patch(ENGINE, return_value=engine):
        await _sync()

    activity = await health.get_activity(USER_ID, "late-1")
    assert activity is not None and activity["settled"] is True
    engine.generate_post_workout_analysis.assert_awaited_once()
    assert engine.generate_post_workout_analysis.await_args.args[1:3] == (USER_ID, "late-1")


async def test_an_activity_inside_its_window_stays_unsettled_and_uncoached() -> None:
    await _log("fresh-1", hours_ago=2)
    engine = _engine()

    with patch(ENGINE, return_value=engine):
        await _sync()

    activity = await health.get_activity(USER_ID, "fresh-1")
    assert activity is not None and activity["settled"] is False
    engine.generate_post_workout_analysis.assert_not_awaited()


async def test_a_settled_activity_is_not_coached_again_by_the_next_sync() -> None:
    await _log("late-2", hours_ago=30)
    engine = _engine()

    with patch(ENGINE, return_value=engine):
        await _sync()
        await _sync()

    assert engine.generate_post_workout_analysis.await_count == 1


async def test_events_are_dispatched_after_health_has_committed() -> None:
    await _log("late-3", hours_ago=30)
    seen: list[bool] = []

    async def coach(session: object, user_id: int, external_id: str, **_: object) -> MagicMock:
        # the handler reads health through its own session: it must see the settled row
        activity = await health.get_activity(user_id, external_id)
        seen.append(bool(activity and activity["settled"]))
        return MagicMock(content="{}")

    engine = MagicMock()
    engine.generate_post_workout_analysis = coach

    with patch(ENGINE, return_value=engine):
        await _sync()

    assert seen == [True]


async def test_a_failing_handler_does_not_fail_the_sync_or_unsettle_the_activity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _log("late-4", hours_ago=30)
    engine = _engine()
    engine.generate_post_workout_analysis = AsyncMock(side_effect=RuntimeError("LLM down"))

    with patch(ENGINE, return_value=engine):
        await _sync()  # must not raise

    activity = await health.get_activity(USER_ID, "late-4")
    assert activity is not None and activity["settled"] is True
    assert any("handler failed" in r.message for r in caplog.records)


async def test_one_failing_handler_does_not_stop_the_next_activity() -> None:
    await _log("late-5", hours_ago=40)
    await _log("late-6", hours_ago=30)
    engine = _engine()
    engine.generate_post_workout_analysis = AsyncMock(
        side_effect=[RuntimeError("boom"), MagicMock(content="{}")]
    )

    with patch(ENGINE, return_value=engine):
        await _sync()

    assert engine.generate_post_workout_analysis.await_count == 2
