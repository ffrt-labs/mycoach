"""Tests for weekly plan prompt builder."""

from mycoach.coaching.prompt_builder import _format_availability, build_weekly_plan_prompt


class TestFormatAvailability:
    def test_formats_slots(self) -> None:
        slots = [
            {
                "day_of_week": 0,
                "day_name": "Monday",
                "start_time": "07:00:00",
                "duration_minutes": 60,
                "preferred_sport": "gym",
            },
            {
                "day_of_week": 3,
                "day_name": "Thursday",
                "start_time": "18:00:00",
                "duration_minutes": 45,
                "preferred_sport": "swimming",
            },
        ]
        result = _format_availability(slots)
        assert "Monday at 07:00:00 (60 min) — gym" in result
        assert "Thursday at 18:00:00 (45 min) — swimming" in result

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
                    "start_time": "07:00",
                    "duration_minutes": 60,
                    "preferred_sport": "gym",
                }
            ],
            health_trends=[],
            recent_activities=[],
        )

        assert "Monday at 07:00 (60 min) — gym" in result
        assert "No recent health data" in result
        assert "No recent activities" in result
        assert "No mesocycle configured" in result

        # Reset prompt dir
        set_prompt_dir(None)  # type: ignore[arg-type]
