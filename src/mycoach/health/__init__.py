"""The health side: the module coaching and the Logger read observed facts through (#186).

Reads return plain dicts, commands are idempotent and return events as plain data. Nothing
in here imports coaching; callers import this package, never the models behind it.

The names below are loaded on first use rather than at import. ``mycoach.sources`` imports
``mycoach.health.events``, and importing any submodule runs this file first; loading
``health.commands`` from here, which imports ``mycoach.sources`` back, made the import order
decide whether the app started (``import mycoach.sources.garmin.client`` or
``import mycoach.sources.intake`` first failed with a circular import).
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mycoach.health.commands import (
        BodyMeasurementRecord,
        LinkOutcome,
        RecordedWorkout,
        RecordedWorkouts,
        SyncOutcome,
        delete_body_measurement,
        delete_source_credentials,
        has_source_credentials,
        issue_athlete,
        link_garmin,
        record_body_measurement,
        record_garmin_activities,
        record_workout,
        record_workouts,
        set_athlete_height,
        settle_due_activities,
        store_source_credentials,
        sync_exercise_catalogue,
        sync_garmin,
    )
    from mycoach.health.events import (
        CanonicalActivityRecorded,
        CanonicalActivityRevised,
        CanonicalActivitySettled,
        HealthEvent,
    )
    from mycoach.health.garmin_weight import (
        UNPUSHED,
        delete_weight_from_garmin,
        push_weight_to_garmin,
        retry_garmin_weight_pushes,
    )
    from mycoach.health.queries import (
        BODY_HISTORY_LIMIT,
        get_activities_for_week,
        get_activity,
        get_activity_facts,
        get_activity_with_details,
        get_athlete_height,
        get_daily_steps,
        get_gym_details_for_week,
        get_gym_performance_history,
        get_health_trends,
        get_health_trends_averaged,
        get_last_performed,
        get_last_sync,
        get_last_week_all_activities,
        get_last_week_cardio_performance,
        get_last_week_gym_performance,
        get_recent_activities,
        get_similar_activities,
        get_snapshot,
        get_today_health,
        has_performed_sets,
        list_activities,
        list_body_measurements,
        list_exercise_catalogue,
        list_settled_activities_since,
        list_snapshots,
        list_sports,
    )
    from mycoach.models.body_measurement import GarminPushStatus

_EXPORTS = {
    "BodyMeasurementRecord": "mycoach.health.commands",
    "LinkOutcome": "mycoach.health.commands",
    "RecordedWorkout": "mycoach.health.commands",
    "RecordedWorkouts": "mycoach.health.commands",
    "SyncOutcome": "mycoach.health.commands",
    "delete_body_measurement": "mycoach.health.commands",
    "delete_source_credentials": "mycoach.health.commands",
    "has_source_credentials": "mycoach.health.commands",
    "issue_athlete": "mycoach.health.commands",
    "link_garmin": "mycoach.health.commands",
    "record_body_measurement": "mycoach.health.commands",
    "record_garmin_activities": "mycoach.health.commands",
    "record_workout": "mycoach.health.commands",
    "record_workouts": "mycoach.health.commands",
    "set_athlete_height": "mycoach.health.commands",
    "settle_due_activities": "mycoach.health.commands",
    "store_source_credentials": "mycoach.health.commands",
    "sync_exercise_catalogue": "mycoach.health.commands",
    "sync_garmin": "mycoach.health.commands",
    "CanonicalActivityRecorded": "mycoach.health.events",
    "CanonicalActivityRevised": "mycoach.health.events",
    "CanonicalActivitySettled": "mycoach.health.events",
    "HealthEvent": "mycoach.health.events",
    "UNPUSHED": "mycoach.health.garmin_weight",
    "delete_weight_from_garmin": "mycoach.health.garmin_weight",
    "push_weight_to_garmin": "mycoach.health.garmin_weight",
    "retry_garmin_weight_pushes": "mycoach.health.garmin_weight",
    "BODY_HISTORY_LIMIT": "mycoach.health.queries",
    "get_activities_for_week": "mycoach.health.queries",
    "get_activity": "mycoach.health.queries",
    "get_activity_facts": "mycoach.health.queries",
    "get_activity_with_details": "mycoach.health.queries",
    "get_athlete_height": "mycoach.health.queries",
    "get_daily_steps": "mycoach.health.queries",
    "get_gym_details_for_week": "mycoach.health.queries",
    "get_gym_performance_history": "mycoach.health.queries",
    "get_health_trends": "mycoach.health.queries",
    "get_health_trends_averaged": "mycoach.health.queries",
    "get_last_performed": "mycoach.health.queries",
    "get_last_sync": "mycoach.health.queries",
    "get_last_week_all_activities": "mycoach.health.queries",
    "get_last_week_cardio_performance": "mycoach.health.queries",
    "get_last_week_gym_performance": "mycoach.health.queries",
    "get_recent_activities": "mycoach.health.queries",
    "get_similar_activities": "mycoach.health.queries",
    "get_snapshot": "mycoach.health.queries",
    "get_today_health": "mycoach.health.queries",
    "has_performed_sets": "mycoach.health.queries",
    "list_activities": "mycoach.health.queries",
    "list_body_measurements": "mycoach.health.queries",
    "list_exercise_catalogue": "mycoach.health.queries",
    "list_settled_activities_since": "mycoach.health.queries",
    "list_snapshots": "mycoach.health.queries",
    "list_sports": "mycoach.health.queries",
    "GarminPushStatus": "mycoach.models.body_measurement",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "BODY_HISTORY_LIMIT",
    "UNPUSHED",
    "BodyMeasurementRecord",
    "CanonicalActivityRecorded",
    "CanonicalActivityRevised",
    "CanonicalActivitySettled",
    "GarminPushStatus",
    "HealthEvent",
    "LinkOutcome",
    "RecordedWorkout",
    "RecordedWorkouts",
    "SyncOutcome",
    "delete_body_measurement",
    "delete_source_credentials",
    "delete_weight_from_garmin",
    "get_activities_for_week",
    "get_activity",
    "get_activity_facts",
    "get_activity_with_details",
    "get_athlete_height",
    "get_daily_steps",
    "get_gym_details_for_week",
    "get_gym_performance_history",
    "get_health_trends",
    "get_health_trends_averaged",
    "get_last_performed",
    "get_last_sync",
    "get_last_week_all_activities",
    "get_last_week_cardio_performance",
    "get_last_week_gym_performance",
    "get_recent_activities",
    "get_similar_activities",
    "get_snapshot",
    "get_today_health",
    "has_performed_sets",
    "has_source_credentials",
    "issue_athlete",
    "link_garmin",
    "list_activities",
    "list_body_measurements",
    "list_exercise_catalogue",
    "list_settled_activities_since",
    "list_snapshots",
    "list_sports",
    "push_weight_to_garmin",
    "record_body_measurement",
    "record_garmin_activities",
    "record_workout",
    "record_workouts",
    "retry_garmin_weight_pushes",
    "set_athlete_height",
    "settle_due_activities",
    "store_source_credentials",
    "sync_exercise_catalogue",
    "sync_garmin",
]
