"""Coaching contexts for the compiled-prompt golden tests.

Each case is (operation, builder inputs). The golden files under
``golden_prompts/`` hold the system and user messages the pre-#171 string
builders produced for these inputs, so the compile step can be checked
byte-for-byte against them.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from mycoach.coaching.body import summarize_body
from mycoach.coaching.life_context import Episode, LifeContext, RampBack, RecoverySignals
from mycoach.coaching.steps import summarize_steps

# The week of 2026-11-02 whose v2 recap said "2/5 planned sessions done... 40%
# on paper... Mon squat moved to Wed... Fix: fewer, firmer slots" (#214, #216,
# #217): the squat was done Wednesday, the swim came up short (1325 m of 1.7 km),
# Sunday's run was replaced by padel, and Thursday's intervals were not done.
WEEK_2026_11_02_ADHERENCE: dict[str, Any] = {
    "plan_id": 41,
    "plan_summary": "Base block week 3: two lifts, a swim, intervals and a long run.",
    "total_sessions": 5,
    "completed_sessions": 4,
    "substituted_sessions": 1,
    "adherence_pct": 80.0,
    "sessions": [
        {
            "id": 1,
            "day": "Monday",
            "sport": "gym",
            "title": "Squat",
            "intent": "strength",
            "completed": True,
            "status": "done",
            "on_planned_day": False,
            "actual_day": "Wednesday",
            "substitute": None,
        },
        {
            "id": 2,
            "day": "Tuesday",
            "sport": "swimming",
            "title": "Swim 1.7 km",
            "intent": "aerobic_base",
            "completed": True,
            "status": "done",
            "on_planned_day": True,
            "actual_day": "Tuesday",
            "substitute": None,
        },
        {
            "id": 3,
            "day": "Thursday",
            "sport": "running",
            "title": "Intervals 6x3 min",
            "intent": "intervals",
            "completed": False,
            "status": "not_done",
            "on_planned_day": None,
            "actual_day": None,
            "substitute": None,
        },
        {
            "id": 4,
            "day": "Friday",
            "sport": "gym",
            "title": "Deadlift",
            "intent": "strength",
            "completed": True,
            "status": "done",
            "on_planned_day": True,
            "actual_day": "Friday",
            "substitute": None,
        },
        {
            "id": 5,
            "day": "Sunday",
            "sport": "running",
            "title": "Long easy run",
            "intent": "aerobic_base",
            "completed": True,
            "status": "substituted",
            "on_planned_day": True,
            "actual_day": "Sunday",
            "substitute": {
                "external_id": "g-padel-1108",
                "sport": "padel",
                "title": "Padel",
                "intent": "aerobic_base",
                "duration_minutes": 101,
                "avg_hr": 118,
                "training_effect_aerobic": 2.7,
            },
        },
    ],
    "extras": [
        {
            "external_id": "g-walk-1107",
            "sport": "walking",
            "title": "Walk",
            "day": "Saturday",
            "duration_minutes": 45,
            "intent": "recovery",
        }
    ],
}

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
# 14 days of steps ending yesterday (2024-06-09), one day missing.
_STEPS = summarize_steps(
    [
        {"date": str(date(2024, 5, 27) + timedelta(days=i)), "steps": steps}
        for i, steps in enumerate(
            [10200, 9800, 11000, 10400, 9900, 12000, 10100]
            + [9000, 12000, None, 8000, 11000, 4000, 12431]
        )
    ],
    target=10000,
)
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

def _body_reading(day: int, weight: float | None, waist: float | None = None,
                  note: str | None = None, month: int = 6) -> dict[str, Any]:  # fmt: skip
    return {"id": day, "measured_at": f"2024-{month:02d}-{day:02d} 06:30:00", "weight_kg": weight,
            "waist_cm": waist, "note": note, "source": "manual"}  # fmt: skip


# Weigh-ins most mornings of the fortnight before 2024-06-10, a waist check a month apart.
_BODY = summarize_body(
    [
        _body_reading(9, 81.6, 90.0, note="pizza and beer"),
        _body_reading(8, 80.9),
        _body_reading(6, 81.0),
        _body_reading(5, 81.2),
        _body_reading(3, 81.4),
        _body_reading(1, 81.5),
        _body_reading(12, 82.0, 92.5, month=5),
    ],
    height_cm=180.0,
    now=datetime(2024, 6, 10, 9, 30, tzinfo=UTC),
    tz=ZoneInfo("Europe/London"),
)

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _open(
    day_of_week: int, status: str, sport: str, title: str, duration: int | None
) -> dict[str, Any]:
    """One session of an Open this week list (#218)."""
    return {
        "id": day_of_week + 1,
        "day": _DAY_NAMES[day_of_week],
        "day_of_week": day_of_week,
        "status": status,
        "sport": sport,
        "track": sport if sport == "gym" else None,
        "title": title,
        "duration_minutes": duration,
        "details": None,
        "notes": None,
    }


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
            "steps": _STEPS,
            "body": _BODY,
            "open_this_week": {
                "sessions": [
                    _open(0, "overdue", "gym", "Squat Day", 60),
                    _open(2, "today", "gym", "Pull Day", 60),
                ],
                "days_remaining": 5,
            },
        },
    ),
    "daily_briefing_open_overload": (
        "daily_briefing",
        {
            "health_today": {},
            "health_trends": [],
            "recent_activities": [],
            "open_this_week": {
                "sessions": [
                    _open(1, "overdue", "gym", "Squat Day", 60),
                    _open(5, "today", "running", "Long Run", 90),
                    _open(5, "today", "swimming", "Swim", None),
                ],
                "days_remaining": 2,
            },
        },
    ),
    "daily_briefing_hrv_band": (
        "daily_briefing",
        {
            "health_today": {
                "resting_hr": 61,
                "sleep_score": 64,
                "hrv_status": 41.0,
                "hrv_7day_avg": 44.0,
                "hrv_status_text": "UNBALANCED",
                "hrv_baseline_low": 47.0,
                "hrv_baseline_high": 58.0,
                "hrv_baseline_low_upper": 43.0,
            },
            "health_trends": [
                {
                    "snapshot_date": "2024-06-09",
                    "hrv_status": 42.0,
                    "hrv_7day_avg": 46.0,
                    "hrv_status_text": "UNBALANCED",
                    "hrv_baseline_low": 47.0,
                    "hrv_baseline_high": 58.0,
                    "hrv_baseline_low_upper": 43.0,
                },
            ],
            "recent_activities": [],
        },
    ),
    # Evals case (#219): wake times swing by over 3 h, so the briefing should
    # nudge toward an anchored wake time.
    "daily_briefing_irregular_wake": (
        "daily_briefing",
        {
            "health_today": {"sleep_score": 68, "wake_time": "09:40", "resting_hr": 57},
            "health_trends": [
                {"snapshot_date": "2024-06-09", "sleep_score": 74, "wake_time": "06:05"},
                {"snapshot_date": "2024-06-08", "sleep_score": 61, "wake_time": "10:15"},
                {"snapshot_date": "2024-06-07", "sleep_score": 79, "wake_time": "06:20"},
            ],
            "recent_activities": [],
        },
    ),
    # #220: sick since Wednesday, still open on Thursday.
    "daily_briefing_ill": (
        "daily_briefing",
        {
            "health_today": {"resting_hr": 62, "sleep_score": 58},
            "health_trends": [],
            "recent_activities": [],
            "life_context": LifeContext(
                today=date(2024, 6, 13),
                episodes=[
                    Episode(
                        id=1, kind="illness", started_on=date(2024, 6, 12), ended_on=None,
                        severity="sick", note="fever, aches",
                    )  # fmt: skip
                ],
                ramp_back=None,
            ),
        },
    ),
    # #221: day 2 of a bodyweight-only trip six hours west, mildly ill since today.
    "daily_briefing_travel_jet_lag": (
        "daily_briefing",
        {
            "health_today": {"resting_hr": 58, "sleep_score": 61},
            "health_trends": [],
            "recent_activities": [],
            "life_context": LifeContext(
                today=date(2024, 6, 13),
                episodes=[
                    Episode(
                        id=2, kind="travel", started_on=date(2024, 6, 12),
                        ended_on=date(2024, 6, 15), equipment="none", tz_shift_hours=-6.0,
                        note="Conference in Boston",
                    ),
                    Episode(
                        id=3, kind="illness", started_on=date(2024, 6, 13), ended_on=None,
                        severity="mild",
                    ),
                ],  # fmt: skip
                ramp_back=None,
            ),
        },
    ),
    # #220: the last day of a five-day ramp-back, resting HR still up.
    "daily_briefing_ramp_back_end": (
        "daily_briefing",
        {
            "health_today": {"resting_hr": 60, "sleep_score": 80},
            "health_trends": [],
            "recent_activities": [],
            "life_context": LifeContext(
                today=date(2024, 6, 13),
                episodes=[],
                ramp_back=RampBack(
                    episode_id=1, day=5, length=5, load_pct=90, ends_on=date(2024, 6, 13),
                    extend_because=RecoverySignals(
                        hrv_below_baseline=False, resting_hr_elevated=True
                    ),
                ),  # fmt: skip
            ),
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
    # The bad v2 recap's week, under the v3 recap prompt (#217).
    "weekly_recap_2026_11_02": (
        "weekly_recap",
        {
            "week_start": date(2026, 11, 2),
            "plan_adherence": WEEK_2026_11_02_ADHERENCE,
            "weekly_activities": [
                {
                    "title": "Pool swim",
                    "sport": "swimming",
                    "start_time": "2026-11-03 07:00:00",
                    "duration_minutes": 35,
                    "distance_meters": 1325.0,
                },
                {
                    "title": "Walk",
                    "sport": "walking",
                    "start_time": "2026-11-07 11:00:00",
                    "duration_minutes": 45,
                    "avg_hr": 96,
                },
                {
                    "title": "Padel",
                    "sport": "padel",
                    "start_time": "2026-11-08 10:00:00",
                    "duration_minutes": 101,
                    "avg_hr": 118,
                },
            ],
            "health_trends": [
                {"snapshot_date": "2026-11-08", "resting_hr": 52, "sleep_score": 81},
                {"snapshot_date": "2026-11-05", "resting_hr": 55, "sleep_score": 64},
            ],
            "weekly_gym_details": [
                {
                    "session_date": "2026-11-04",
                    "session_title": "Squat",
                    "exercise_title": "Barbell Squat",
                    "set_index": 1,
                    "set_type": "normal",
                    "weight_kg": 100.0,
                    "reps": 5,
                    "rpe": 8,
                },
                {
                    "session_date": "2026-11-06",
                    "session_title": "Deadlift",
                    "exercise_title": "Barbell Deadlift",
                    "set_index": 1,
                    "set_type": "normal",
                    "weight_kg": 140.0,
                    "reps": 5,
                    "rpe": 8,
                },
            ],
        },
    ),
}
