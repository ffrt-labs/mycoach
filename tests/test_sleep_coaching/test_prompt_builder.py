"""Tests for sleep coaching prompt builder."""

from mycoach.coaching.prompt_builder import _format_sleep_trends, build_sleep_coaching_prompt


class TestFormatSleepTrends:
    def test_formats_entries(self) -> None:
        trends = [
            {
                "snapshot_date": "2024-06-13",
                "sleep_duration_minutes": 450,
                "sleep_score": 85,
                "sleep_deep_minutes": 90,
                "resting_hr": 52,
            },
        ]
        result = _format_sleep_trends(trends)
        assert "2024-06-13" in result
        assert "Sleep duration (min): 450" in result
        assert "Sleep score: 85" in result
        assert "Resting HR: 52" in result

    def test_empty_trends(self) -> None:
        result = _format_sleep_trends([])
        assert result == "No sleep data available."

    def test_multiple_entries(self) -> None:
        trends = [
            {"snapshot_date": "2024-06-13", "sleep_score": 85},
            {"snapshot_date": "2024-06-12", "sleep_score": 78},
        ]
        result = _format_sleep_trends(trends)
        assert "2024-06-13" in result
        assert "2024-06-12" in result


class TestBuildSleepCoachingPrompt:
    def test_builds_full_prompt(self) -> None:
        trends = [{"snapshot_date": "2024-06-13", "sleep_score": 85}]
        activities = [{"sport": "gym", "title": "Upper Body", "start_time": "2024-06-13 09:00"}]
        result = build_sleep_coaching_prompt(
            sleep_trends=trends,
            recent_activities=activities,
        )
        assert "Sleep score: 85" in result
        assert "Upper Body" in result
        assert "No workout planned for tomorrow" in result

    def test_with_planned_workout(self) -> None:
        result = build_sleep_coaching_prompt(
            sleep_trends=[],
            recent_activities=[],
            planned_workout="Leg Day — heavy squats",
        )
        assert "Leg Day — heavy squats" in result
