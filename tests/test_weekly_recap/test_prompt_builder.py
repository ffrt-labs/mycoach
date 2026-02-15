"""Tests for weekly recap prompt builder functions."""

from datetime import date

from mycoach.coaching.prompt_builder import _format_plan_adherence, build_weekly_recap_prompt


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
