"""Tests for post-workout prompt builder functions."""

from pathlib import Path

import pytest

from mycoach.coaching.prompt_builder import (
    _format_activity_detail,
    _format_gym_details,
    _format_planned_session,
    build_post_workout_prompt,
    set_prompt_dir,
)


@pytest.fixture(autouse=True)
def _use_real_prompts() -> None:
    """Reset prompt dir to use real templates."""
    set_prompt_dir(Path(__file__).parent.parent.parent / "src" / "mycoach" / "prompts")


class TestFormatActivityDetail:
    def test_full_activity(self) -> None:
        activity = {
            "title": "Upper Body",
            "sport": "gym",
            "start_time": "2024-06-10 09:00:00",
            "duration_minutes": 60,
            "avg_hr": 130,
            "max_hr": 165,
            "calories": 450,
            "training_effect_aerobic": 3.2,
        }
        result = _format_activity_detail(activity)
        assert "Upper Body" in result
        assert "gym" in result
        assert "130" in result

    def test_empty_activity(self) -> None:
        result = _format_activity_detail({})
        assert result == "No activity data."


class TestFormatGymDetails:
    def test_with_details(self) -> None:
        details = [
            {
                "exercise_title": "Bench Press",
                "set_index": 1,
                "set_type": "normal",
                "weight_kg": 80.0,
                "reps": 8,
                "rpe": 7.5,
            },
            {
                "exercise_title": "Bench Press",
                "set_index": 2,
                "set_type": "normal",
                "weight_kg": 80.0,
                "reps": 7,
                "rpe": 8.0,
            },
        ]
        result = _format_gym_details(details)
        assert "**Bench Press**" in result
        assert "80.0kg" in result
        assert "x8" in result
        assert "RPE 7.5" in result

    def test_no_details(self) -> None:
        result = _format_gym_details([])
        assert "No gym workout details" in result

    def test_warmup_set_type(self) -> None:
        details = [
            {
                "exercise_title": "Squat",
                "set_index": 1,
                "set_type": "warmup",
                "weight_kg": 40.0,
                "reps": 10,
            },
        ]
        result = _format_gym_details(details)
        assert "(warmup)" in result


class TestFormatPlannedSession:
    def test_with_planned(self) -> None:
        planned = {
            "title": "Upper Body Strength",
            "sport": "gym",
            "duration_minutes": 60,
            "details": '{"exercises": ["bench press", "rows"]}',
            "notes": "Focus on form",
        }
        result = _format_planned_session(planned)
        assert "Upper Body Strength" in result
        assert "Focus on form" in result

    def test_no_planned(self) -> None:
        result = _format_planned_session(None)
        assert "No planned session" in result


class TestBuildPostWorkoutPrompt:
    def test_builds_full_prompt(self) -> None:
        result = build_post_workout_prompt(
            activity={"title": "Upper Body", "sport": "gym", "avg_hr": 130},
            gym_details=[
                {
                    "exercise_title": "Bench Press",
                    "set_index": 1,
                    "set_type": "normal",
                    "weight_kg": 80.0,
                    "reps": 8,
                }
            ],
            planned_session={"title": "Upper Body", "sport": "gym", "duration_minutes": 60},
            similar_activities=[],
            health_context={"resting_hr": 55, "sleep_score": 82},
        )
        assert "Upper Body" in result
        assert "Bench Press" in result
        assert "performance_summary" in result
