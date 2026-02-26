"""Tests for weekly plan prompt builder."""

from mycoach.coaching.prompt_builder import (
    _format_availability,
    _format_cardio_activities,
    _format_routine_exercises,
    build_weekly_plan_prompt,
)


class TestFormatAvailability:
    def test_formats_slots_with_sport(self) -> None:
        slots = [
            {
                "day_of_week": 0,
                "day_name": "Monday",
                "sport": "gym",
            },
            {
                "day_of_week": 3,
                "day_name": "Thursday",
                "sport": "swimming",
            },
        ]
        result = _format_availability(slots)
        assert "Monday — Gym" in result
        assert "Thursday — Swimming" in result

    def test_formats_slots_without_sport(self) -> None:
        slots = [
            {
                "day_of_week": 0,
                "day_name": "Monday",
                "sport": None,
            },
        ]
        result = _format_availability(slots)
        assert "Monday" in result
        assert "—" not in result

    def test_empty_slots(self) -> None:
        assert _format_availability([]) == "No availability slots set."


class TestBuildWeeklyPlanPrompt:
    def test_includes_all_sections(self, tmp_path: object) -> None:
        from pathlib import Path

        from mycoach.coaching.prompt_builder import set_prompt_dir

        # Create temp prompt directory
        v1_dir = Path(str(tmp_path)) / "v1"
        v1_dir.mkdir()
        (v1_dir / "weekly_plan.txt").write_text(
            "Avail: {availability}\n"
            "Health: {health_trends}\n"
            "Activities: {recent_activities}\n"
            "Meso: {mesocycle_context}"
        )
        set_prompt_dir(Path(str(tmp_path)))

        result = build_weekly_plan_prompt(
            availability=[
                {
                    "day_of_week": 0,
                    "day_name": "Monday",
                    "sport": "gym",
                }
            ],
            health_trends=[],
            recent_activities=[],
        )

        assert "Monday — Gym" in result
        assert "No recent health data" in result
        assert "No recent activities" in result
        assert "No mesocycle configured" in result

        # Reset prompt dir
        set_prompt_dir(None)  # type: ignore[arg-type]


class TestFormatCardioActivities:
    def test_swimming_with_distance_and_pace(self) -> None:
        activities = [
            {
                "sport": "swimming",
                "title": "Evening Swim",
                "start_time": "2026-02-16 18:30:00",
                "duration_minutes": 45,
                "distance_meters": 2000.0,
                "avg_hr": 140,
                "max_hr": 165,
                "training_effect_aerobic": 3.2,
            }
        ]
        result = _format_cardio_activities(activities)
        assert "Evening Swim [swimming]" in result
        assert "2.0km" in result
        assert "pace 2:15/100m" in result
        assert "avg HR 140" in result

    def test_running_with_distance_and_pace(self) -> None:
        activities = [
            {
                "sport": "running",
                "title": "Easy Run",
                "start_time": "2026-02-18 07:00:00",
                "duration_minutes": 30,
                "distance_meters": 5200.0,
                "avg_hr": 145,
            }
        ]
        result = _format_cardio_activities(activities)
        assert "Easy Run [running]" in result
        assert "5.2km" in result
        assert "/km" in result
        assert "avg HR 145" in result

    def test_activity_without_distance(self) -> None:
        activities = [
            {
                "sport": "cardio",
                "title": "Indoor Cycling",
                "start_time": "2026-02-19 08:00:00",
                "duration_minutes": 40,
                "avg_hr": 130,
            }
        ]
        result = _format_cardio_activities(activities)
        assert "Indoor Cycling [cardio]" in result
        assert "40 min" in result
        assert "pace" not in result

    def test_empty_activities(self) -> None:
        assert _format_cardio_activities([]) == "No recent cardio activities."

    def test_short_distance_meters(self) -> None:
        activities = [
            {
                "sport": "swimming",
                "title": "Short Swim",
                "start_time": "2026-02-20 12:00:00",
                "duration_minutes": 15,
                "distance_meters": 500.0,
            }
        ]
        result = _format_cardio_activities(activities)
        assert "500m" in result


class TestFormatRoutineExercises:
    def test_standalone_exercises(self) -> None:
        exercises = [
            {"exercise_name": "Bench Press", "sets": 4, "rep_range": "6-8", "notes": None},
            {"exercise_name": "Shoulder Press", "sets": 3, "rep_range": "8-10", "notes": None},
        ]
        result = _format_routine_exercises(exercises)
        assert "1. Bench Press" in result
        assert "2. Shoulder Press" in result
        assert "Superset" not in result

    def test_superset_grouping(self) -> None:
        exercises = [
            {
                "exercise_name": "Bench Press",
                "sets": 3,
                "rep_range": "8-10",
                "notes": None,
                "superset_group": 0,
            },
            {
                "exercise_name": "Bent Over Row",
                "sets": 3,
                "rep_range": "8-10",
                "notes": None,
                "superset_group": 0,
            },
            {
                "exercise_name": "Lateral Raise",
                "sets": 3,
                "rep_range": "12-15",
                "notes": None,
            },
        ]
        result = _format_routine_exercises(exercises)
        assert "1a. Bench Press" in result
        assert "[Superset A]" in result
        assert "1b. Bent Over Row" in result
        assert "2. Lateral Raise" in result

    def test_multiple_superset_groups(self) -> None:
        exercises = [
            {
                "exercise_name": "Bench Press",
                "sets": 3,
                "rep_range": "8-10",
                "notes": None,
                "superset_group": 0,
            },
            {
                "exercise_name": "Bent Over Row",
                "sets": 3,
                "rep_range": "8-10",
                "notes": None,
                "superset_group": 0,
            },
            {
                "exercise_name": "Bicep Curl",
                "sets": 3,
                "rep_range": "10-12",
                "notes": None,
                "superset_group": 1,
            },
            {
                "exercise_name": "Tricep Extension",
                "sets": 3,
                "rep_range": "10-12",
                "notes": None,
                "superset_group": 1,
            },
        ]
        result = _format_routine_exercises(exercises)
        assert "[Superset A]" in result
        assert "[Superset B]" in result
        assert "1a. Bench Press" in result
        assert "1b. Bent Over Row" in result
        assert "2a. Bicep Curl" in result
        assert "2b. Tricep Extension" in result

    def test_empty_exercises(self) -> None:
        assert _format_routine_exercises([]) == "No exercises defined."

    def test_exercise_with_notes(self) -> None:
        exercises = [
            {
                "exercise_name": "Bench Press",
                "sets": 3,
                "rep_range": "8-10",
                "notes": "Pause at bottom",
                "superset_group": 0,
            },
            {
                "exercise_name": "Row",
                "sets": 3,
                "rep_range": "8-10",
                "notes": None,
                "superset_group": 0,
            },
        ]
        result = _format_routine_exercises(exercises)
        assert "(Pause at bottom)" in result
