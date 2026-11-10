"""The health module's public interface (map #85, ticket #186, designs #111 and #112).

Everything here goes through ``mycoach.health`` only: plain-dict reads, one-call idempotent
``record_workout``, events returned as data. No ORM object crosses the boundary.
"""

import json
from datetime import date, datetime, timedelta

import pytest

from mycoach import health
from mycoach.health.events import (
    CanonicalActivityRecorded,
    CanonicalActivityRevised,
    CanonicalActivitySettled,
)
from mycoach.sources.workout_import import WorkoutImport, WorkoutSetImport

START = datetime(2026, 11, 2, 9, 0)
ATHLETE = 1


def _workout(external_id: str = "uuid-1", start: datetime = START, **kw) -> WorkoutImport:
    return WorkoutImport(
        title="Push Day",
        start_time=start,
        end_time=start + timedelta(hours=1),
        external_id=external_id,
        sets=[
            WorkoutSetImport(
                exercise_title="Barbell Bench Press - Medium Grip",
                exercise_id="Barbell_Bench_Press_-_Medium_Grip",
                set_index=1,
                weight_kg=80,
                reps=8,
                rpe=8,
                # A prescription is coaching's; health ignores it.
                prescribed_weight_kg=82.5,
                prescribed_reps=8,
            ),
            WorkoutSetImport(
                exercise_title="Barbell Bench Press - Medium Grip",
                set_index=2,
                weight_kg=80,
                reps=7,
            ),
        ],
        **kw,
    )


def _garmin_raw(garmin_id: int, start: datetime, **kw) -> dict:
    return {
        "activityId": garmin_id,
        "activityName": "Strength",
        "activityType": {"typeKey": "strength_training"},
        "startTimeGMT": start.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": 3600,
        "averageHR": 120,
        **kw,
    }


class TestRecordWorkout:
    async def test_records_in_one_call_and_returns_the_external_id(self):
        out = await health.record_workout(ATHLETE, "logger", _workout())
        assert out.created is True
        assert out.activity_external_id == "uuid-1"
        details = await health.get_activity_with_details(ATHLETE, "uuid-1")
        activity, sets = details
        assert activity["title"] == "Push Day"
        assert [s["set_index"] for s in sets] == [1, 2]

    async def test_is_idempotent_on_source_and_external_id(self):
        await health.record_workout(ATHLETE, "logger", _workout())
        again = await health.record_workout(ATHLETE, "logger", _workout())
        assert again.created is False
        assert again.activity_external_id == "uuid-1"
        assert again.events == []
        _, sets = await health.get_activity_with_details(ATHLETE, "uuid-1")
        assert len(sets) == 2

    async def test_a_gym_source_raises_recorded_but_is_not_settled(self):
        out = await health.record_workout(ATHLETE, "logger", _workout())
        assert [type(e) for e in out.events] == [CanonicalActivityRecorded]
        event = out.events[0]
        assert event.activity_external_id == "uuid-1"
        assert event.source == "logger"
        assert event.athlete_id == ATHLETE

    async def test_a_source_without_a_native_id_still_gets_an_external_id(self):
        out = await health.record_workout(ATHLETE, "hevy", _workout(external_id=None))
        assert out.activity_external_id.startswith("derived:")

    async def test_prescriptions_are_not_stored_on_the_performed_set(self):
        await health.record_workout(ATHLETE, "logger", _workout())
        _, sets = await health.get_activity_with_details(ATHLETE, "uuid-1")
        assert not any("prescribed" in key for s in sets for key in s)


class TestReadShapes:
    async def test_reads_are_plain_json_serialisable_dicts(self):
        await health.record_workout(ATHLETE, "logger", _workout())
        activity, sets = await health.get_activity_with_details(ATHLETE, "uuid-1")
        json.dumps([activity, sets])  # nothing ORM, nothing un-serialisable
        assert activity["external_id"] == "uuid-1"
        assert "id" not in activity  # the internal row id never crosses the boundary

    async def test_data_source_is_not_exposed_as_reconciliation_state(self):
        await health.record_workout(ATHLETE, "logger", _workout())
        activity, _ = await health.get_activity_with_details(ATHLETE, "uuid-1")
        assert "data_source" not in activity
        assert activity["sources"] == ["logger"]

    async def test_unknown_activity_is_a_value_error(self):
        with pytest.raises(ValueError):
            await health.get_activity_with_details(ATHLETE, "nope")

    async def test_other_athletes_activities_are_invisible(self):
        await health.record_workout(ATHLETE, "logger", _workout())
        with pytest.raises(ValueError):
            await health.get_activity_with_details(2, "uuid-1")

    async def test_gym_performance_history_is_aggregated_in_health(self):
        await health.record_workout(ATHLETE, "logger", _workout(start=datetime(2026, 10, 27, 9)))
        rows = await health.get_gym_performance_history(ATHLETE, date(2026, 11, 2), weeks=1)
        assert rows == [
            {
                "week_start": "2026-10-26",
                "exercise_id": "Barbell_Bench_Press_-_Medium_Grip",
                "exercise_title": "Barbell Bench Press - Medium Grip",
                "best_weight_kg": 80,
                "best_reps": 8,
                "total_sets": 2,
                "avg_rpe": 8.0,
            }
        ]

    async def test_last_performed_is_the_most_recent_sets_per_exercise(self):
        await health.record_workout(ATHLETE, "logger", _workout("a", START - timedelta(days=7)))
        await health.record_workout(ATHLETE, "logger", _workout("b", START))
        rows = await health.get_last_performed(ATHLETE)
        bench = "Barbell Bench Press - Medium Grip"
        assert [r["date"] for r in rows if r["title"] == bench] == ["2026-11-02"]

    async def test_catalogue_listing(self):
        items = await health.list_exercise_catalogue()
        assert items and set(items[0]) == {"id", "name"}


class TestSettlement:
    async def test_a_garmin_only_activity_settles_immediately(self):
        out = await health.record_garmin_activities(ATHLETE, [_garmin_raw(11, START)])
        kinds = [type(e) for e in out.events]
        assert kinds == [CanonicalActivityRecorded, CanonicalActivitySettled]
        settled = out.events[1]
        assert settled.settlement_reason == "immediate"
        assert settled.activity_external_id == "11"

    async def test_a_gym_activity_settles_when_garmin_is_linked(self):
        await health.record_workout(ATHLETE, "logger", _workout())
        out = await health.record_garmin_activities(ATHLETE, [_garmin_raw(11, START)])
        link = await health.link_garmin(ATHLETE)
        assert link.linked == 1
        settled = [e for e in link.events if isinstance(e, CanonicalActivitySettled)]
        assert [e.activity_external_id for e in settled] == ["uuid-1"]
        assert settled[0].settlement_reason == "reconciled"
        # the Garmin-only activity's own events were raised when it arrived
        assert any(isinstance(e, CanonicalActivitySettled) for e in out.events)

    async def test_a_gym_activity_settles_at_the_deadline_without_garmin(self):
        await health.record_workout(ATHLETE, "logger", _workout())
        early = await health.settle_due_activities(ATHLETE, now=START + timedelta(hours=23))
        assert early == []
        due = await health.settle_due_activities(ATHLETE, now=START + timedelta(hours=25))
        assert [type(e) for e in due] == [CanonicalActivitySettled]
        assert due[0].settlement_reason == "deadline"
        # settling is recorded, so a second check raises nothing
        assert await health.settle_due_activities(ATHLETE, now=START + timedelta(hours=30)) == []

    async def test_the_deadline_applies_only_to_gym_sourced_activities(self):
        from mycoach.health.runtime import health_session
        from mycoach.models.activity import Activity

        async with health_session() as session:
            session.add(
                Activity(
                    user_id=ATHLETE, sport="running", title="Run", start_time=START,
                    data_source="garmin", external_id="g-legacy",
                )
            )
            await session.commit()
        await health.record_workout(ATHLETE, "logger", _workout())
        due = await health.settle_due_activities(ATHLETE, now=START + timedelta(hours=25))
        assert [e.activity_external_id for e in due] == ["uuid-1"]

    async def test_late_garmin_data_on_a_settled_activity_is_a_revision(self):
        await health.record_workout(ATHLETE, "logger", _workout())
        await health.settle_due_activities(ATHLETE, now=START + timedelta(hours=25))
        await health.record_garmin_activities(ATHLETE, [_garmin_raw(11, START)])
        link = await health.link_garmin(ATHLETE)
        revised = [e for e in link.events if isinstance(e, CanonicalActivityRevised)]
        assert len(revised) == 1
        assert revised[0].activity_external_id == "uuid-1"
        assert "avg_hr" in revised[0].changed_fields
        assert not any(isinstance(e, CanonicalActivitySettled) for e in link.events)

    async def test_deadline_is_a_setting(self):
        from mycoach.config import get_settings

        assert get_settings().activity_settlement_deadline_hours == 24
