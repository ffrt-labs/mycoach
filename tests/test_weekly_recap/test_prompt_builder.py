"""Tests for weekly recap prompt builder functions."""

from datetime import date

from mycoach.coaching.prompt_builder import (
    _format_gym_history,
    _format_plan_adherence,
    _format_weekly_gym_details,
    build_weekly_recap_prompt,
)


class TestFormatPlanAdherence:
    def test_no_plan(self) -> None:
        result = _format_plan_adherence(None)
        assert "No training plan" in result

    def test_with_adherence_data(self) -> None:
        adherence = {
            "plan_summary": "Test plan",
            "total_sessions": 3,
            "completed_sessions": 2,
            "adherence_pct": 66.7,
            "sessions": [
                {"day": "Monday", "sport": "gym", "title": "Push", "completed": True},
                {"day": "Wednesday", "sport": "swimming", "title": "Swim", "completed": True},
                {"day": "Friday", "sport": "padel", "title": "Padel", "completed": False},
            ],
        }
        result = _format_plan_adherence(adherence)
        assert "2/3" in result
        assert "66.7%" in result
        assert "DONE" in result
        assert "MISSED" in result

    def test_empty_sessions(self) -> None:
        adherence = {
            "plan_summary": "Empty plan",
            "total_sessions": 0,
            "completed_sessions": 0,
            "adherence_pct": 0.0,
            "sessions": [],
        }
        result = _format_plan_adherence(adherence)
        assert "0/0" in result


class TestFormatWeeklyGymDetails:
    def test_empty(self) -> None:
        result = _format_weekly_gym_details([])
        assert "No gym sessions" in result

    def test_formats_sets(self) -> None:
        details = [
            {
                "session_date": "2024-06-10",
                "session_title": "Upper Body",
                "exercise_title": "Bench Press",
                "set_index": 1,
                "set_type": "normal",
                "weight_kg": 80.0,
                "reps": 8,
                "rpe": 7,
            },
            {
                "session_date": "2024-06-10",
                "session_title": "Upper Body",
                "exercise_title": "Bench Press",
                "set_index": 2,
                "set_type": "normal",
                "weight_kg": 80.0,
                "reps": 7,
                "rpe": 9,
            },
        ]
        result = _format_weekly_gym_details(details)
        assert "Bench Press" in result
        assert "80kg×8" in result
        assert "RPE7" in result

    def test_groups_by_session(self) -> None:
        details = [
            {
                "session_date": "2024-06-10",
                "session_title": "A",
                "exercise_title": "Squat",
                "set_index": 1,
                "set_type": "normal",
                "weight_kg": 100.0,
                "reps": 5,
                "rpe": None,
            },
            {
                "session_date": "2024-06-12",
                "session_title": "B",
                "exercise_title": "Deadlift",
                "set_index": 1,
                "set_type": "normal",
                "weight_kg": 140.0,
                "reps": 3,
                "rpe": 8,
            },
        ]
        result = _format_weekly_gym_details(details)
        assert "Squat" in result
        assert "Deadlift" in result
        assert "2024-06-10" in result
        assert "2024-06-12" in result


class TestFormatGymHistory:
    def test_empty(self) -> None:
        result = _format_gym_history([])
        assert "No gym history" in result

    def test_formats_week_over_week(self) -> None:
        history = [
            {
                "week_start": "2024-06-03",
                "exercise_title": "Bench Press",
                "best_weight_kg": 80.0,
                "best_reps": 8,
                "total_sets": 3,
                "avg_rpe": 7.5,
            },
            {
                "week_start": "2024-06-10",
                "exercise_title": "Bench Press",
                "best_weight_kg": 80.0,
                "best_reps": 7,
                "total_sets": 3,
                "avg_rpe": 9.0,
            },
        ]
        result = _format_gym_history(history)
        assert "Bench Press" in result
        assert "80kg × 8" in result
        assert "80kg × 7" in result
        assert "avg RPE 7.5" in result

    def test_groups_by_exercise(self) -> None:
        history = [
            {
                "week_start": "2024-06-03",
                "exercise_title": "Squat",
                "best_weight_kg": 120.0,
                "best_reps": 5,
                "total_sets": 4,
                "avg_rpe": 8.0,
            },
            {
                "week_start": "2024-06-03",
                "exercise_title": "Bench Press",
                "best_weight_kg": 80.0,
                "best_reps": 8,
                "total_sets": 3,
                "avg_rpe": 7.0,
            },
        ]
        result = _format_gym_history(history)
        assert "Squat" in result
        assert "Bench Press" in result


class TestBuildWeeklyRecapPrompt:
    def test_builds_full_prompt(self) -> None:
        result = build_weekly_recap_prompt(
            week_start=date(2024, 6, 10),
            plan_adherence={
                "plan_summary": "Test",
                "total_sessions": 2,
                "completed_sessions": 1,
                "adherence_pct": 50.0,
                "sessions": [
                    {"day": "Monday", "sport": "gym", "title": "Push", "completed": True},
                    {"day": "Wednesday", "sport": "gym", "title": "Pull", "completed": False},
                ],
            },
            weekly_activities=[
                {
                    "sport": "gym",
                    "title": "Push",
                    "start_time": "2024-06-10",
                    "duration_minutes": 60,
                }
            ],
            health_trends=[],
            mesocycle_context="Week 2 of 4",
            weekly_gym_details=[],
            gym_history=[],
            sport_profiles=[],
        )
        assert "2024-06-10" in result
        assert "2024-06-16" in result
        assert "1/2" in result
        assert "Week 2 of 4" in result

    def test_no_plan_no_mesocycle(self) -> None:
        result = build_weekly_recap_prompt(
            week_start=date(2024, 6, 10),
            plan_adherence=None,
            weekly_activities=[],
            health_trends=[],
        )
        assert "No training plan" in result
        assert "No mesocycle" in result

    def test_gym_details_included(self) -> None:
        gym_details = [
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
        ]
        gym_history = [
            {
                "week_start": "2024-06-03",
                "exercise_title": "Bench Press",
                "best_weight_kg": 77.5,
                "best_reps": 8,
                "total_sets": 3,
                "avg_rpe": 7.0,
            }
        ]
        result = build_weekly_recap_prompt(
            week_start=date(2024, 6, 10),
            plan_adherence=None,
            weekly_activities=[],
            health_trends=[],
            weekly_gym_details=gym_details,
            gym_history=gym_history,
            version="v2",
        )
        assert "Bench Press" in result
        assert "80kg×8" in result
        assert "77.5kg × 8" in result
