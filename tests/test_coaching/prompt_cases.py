"""Coaching contexts for the compiled-prompt golden tests.

Each case is (operation, builder inputs). The golden files under
``golden_prompts/`` hold the system and user messages the pre-#171 string
builders produced for these inputs, so the compile step can be checked
byte-for-byte against them.
"""

from datetime import date
from typing import Any

_HEALTH_TODAY: dict[str, Any] = {
    "resting_hr": 55,
    "sleep_score": 82,
    "hrv_status": 82.0,
    "hrv_status_text": "BALANCED",
    "training_status": "4",
}
_HEALTH_TRENDS: list[dict[str, Any]] = [
    {"snapshot_date": "2024-06-09", "resting_hr": 56, "sleep_score": 75},
    {"snapshot_date": "2024-06-08", "resting_hr": 54},
]
_RECENT_ACTIVITIES: list[dict[str, Any]] = [
    {
        "title": "Push Day",
        "sport": "gym",
        "start_time": "2024-06-10",
        "duration_minutes": 60,
        "avg_hr": 120,
    },
    {
        "sport": "swimming",
        "title": "Evening Swim",
        "start_time": "2024-06-08 18:30:00",
        "duration_minutes": 45,
        "distance_meters": 2000.0,
        "avg_hr": 140,
        "max_hr": 165,
        "training_effect_aerobic": 3.2,
    },
]
_SPORT_PROFILES: list[dict[str, Any]] = [
    {
        "sport": "running",
        "skill_level": "intermediate",
        "goals": "Sub-50 10k",
        "preferences": "Mornings",
        "benchmarks": "5k in 24:00",
    },
]
_ROUTINE_EXERCISES: list[dict[str, Any]] = [
    {
        "exercise_name": "Bench Press",
        "sets": 3,
        "rep_range": "8-10",
        "notes": "Pause at bottom",
        "superset_group": 0,
    },
    {
        "exercise_name": "Bent Over Row",
        "sets": 3,
        "rep_range": "8-10",
        "notes": None,
        "superset_group": 0,
    },
    {"exercise_name": "Lateral Raise", "sets": 3, "rep_range": "12-15", "notes": None},
]
_GYM_DETAILS: list[dict[str, Any]] = [
    {
        "exercise_title": "Bench Press",
        "set_index": 1,
        "set_type": "normal",
        "weight_kg": 80.0,
        "reps": 8,
    }
]

CASES: dict[str, tuple[str, dict[str, Any]]] = {
    "daily_briefing_full": (
        "daily_briefing",
        {
            "health_today": _HEALTH_TODAY,
            "health_trends": _HEALTH_TRENDS,
            "recent_activities": _RECENT_ACTIVITIES,
            "planned_workout": [
                {"sport": "gym", "title": "Pull Day", "duration_minutes": 60},
            ],
        },
    ),
    "daily_briefing_empty": (
        "daily_briefing",
        {"health_today": {}, "health_trends": [], "recent_activities": []},
    ),
    "gym_adjustment_full": (
        "gym_adjustment",
        {
            "routine_day_name": "Push A",
            "routine_exercises": _ROUTINE_EXERCISES,
            "health_trends": {
                "resting_hr": 55.5,
                "sleep_score": 80.0,
                "days_with_data": 7,
                "load_focus": "low_aerobic",
            },
            "last_week_activities": _RECENT_ACTIVITIES,
            "mesocycle_context": "Week 2 of 4 (accumulation)",
            "sport_profiles": _SPORT_PROFILES,
        },
    ),
    "gym_adjustment_empty": (
        "gym_adjustment",
        {
            "routine_day_name": "Legs",
            "routine_exercises": [],
            "health_trends": {},
        },
    ),
    "cardio_plan_full": (
        "cardio_plan",
        {
            "cardio_slots": [
                {"day_name": "Tuesday", "day_of_week": 1, "sport": "running"},
                {"day_name": "Thursday", "day_of_week": 3, "sport": "swimming"},
            ],
            "last_week_cardio": _RECENT_ACTIVITIES[1:],
            "health_trends": _HEALTH_TRENDS,
            "mesocycle_context": "Week 2 of 4",
            "sport_profiles": _SPORT_PROFILES,
        },
    ),
    "cardio_plan_empty": (
        "cardio_plan",
        {"cardio_slots": [], "last_week_cardio": [], "health_trends": []},
    ),
    "post_workout_gym_planned": (
        "post_workout",
        {
            "activity": {"title": "Upper Body", "sport": "gym", "avg_hr": 130},
            "gym_details": _GYM_DETAILS,
            "planned_session": {
                "title": "Upper Body",
                "sport": "gym",
                "duration_minutes": 60,
            },
            "similar_activities": _RECENT_ACTIVITIES[:1],
            "health_context": _HEALTH_TODAY,
        },
    ),
    "post_workout_swim_unplanned": (
        "post_workout",
        {
            "activity": {
                "title": "Morning Swim",
                "sport": "swimming",
                "distance_meters": 2400,
                "moving_duration_seconds": 2700.0,
                "duration_minutes": 60,
                "training_effect_aerobic": 3.5,
                "epoc": 92.0,
                "avg_swolf": 38.0,
                "avg_strokes_per_length": 16,
                "fastest_split_100_seconds": 105.0,
            },
            "gym_details": [],
            "planned_session": None,
            "similar_activities": [],
            "health_context": {},
        },
    ),
    "post_workout_run_planned_no_gym": (
        "post_workout",
        {
            "activity": {"title": "Run", "sport": "running", "duration_minutes": 30},
            "gym_details": [],
            "planned_session": {"title": "Easy Run", "sport": "running"},
            "similar_activities": [],
            "health_context": {"resting_hr": 50},
        },
    ),
    "weekly_recap_full": (
        "weekly_recap",
        {
            "week_start": date(2024, 6, 10),
            "plan_adherence": {
                "plan_summary": "Test",
                "total_sessions": 2,
                "completed_sessions": 1,
                "adherence_pct": 50.0,
                "sessions": [
                    {"day": "Monday", "sport": "gym", "title": "Push", "completed": True},
                    {"day": "Wednesday", "sport": "gym", "title": "Pull", "completed": False},
                ],
            },
            "weekly_activities": _RECENT_ACTIVITIES,
            "health_trends": _HEALTH_TRENDS,
            "mesocycle_context": "Week 2 of 4",
            "routine": {
                "name": "PPL",
                "days": [{"name": "Push A", "day_of_week": 0, "exercises": _ROUTINE_EXERCISES}],
            },
            "availability": [{"day_name": "Monday", "day_of_week": 0, "sport": "gym"}],
            "weekly_gym_details": [
                {
                    "session_date": "2024-06-10",
                    "session_title": "Push",
                    "exercise_title": "Bench Press",
                    "set_index": 1,
                    "set_type": "normal",
                    "weight_kg": 80.0,
                    "reps": 8,
                    "rpe": 7,
                }
            ],
            "gym_history": [
                {
                    "week_start": "2024-06-03",
                    "exercise_title": "Bench Press",
                    "best_weight_kg": 77.5,
                    "best_reps": 8,
                    "total_sets": 3,
                    "avg_rpe": 7.0,
                }
            ],
            "sport_profiles": _SPORT_PROFILES,
        },
    ),
    "weekly_recap_empty": (
        "weekly_recap",
        {
            "week_start": date(2024, 6, 10),
            "plan_adherence": None,
            "weekly_activities": [],
            "health_trends": [],
        },
    ),
}
