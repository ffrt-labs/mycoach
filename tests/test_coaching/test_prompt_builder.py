"""Tests for coaching prompt builder."""

from mycoach.coaching.prompt_builder import (
    _format_activities,
    _format_health,
    _format_health_trends,
    build_daily_briefing_prompt,
    get_system_prompt,
)


class TestFormatHealth:
    def test_empty(self) -> None:
        assert _format_health({}) == "No health data available for today."

    def test_with_data(self) -> None:
        data = {"resting_hr": 55, "sleep_score": 82, "body_battery_high": 80}
        result = _format_health(data)
        assert "Resting HR: 55" in result
        assert "Sleep score: 82" in result
        assert "Body Battery high: 80" in result

    def test_ignores_none_values(self) -> None:
        data = {"resting_hr": 55, "avg_hr": None}
        result = _format_health(data)
        assert "Resting HR: 55" in result
        assert "Avg HR" not in result


class TestFormatActivities:
    def test_empty(self) -> None:
        assert _format_activities([]) == "No recent activities."

    def test_with_activities(self) -> None:
        activities = [
            {
                "title": "Push Day",
                "sport": "gym",
                "start_time": "2024-06-10 09:00:00",
                "duration_minutes": 75,
            }
        ]
        result = _format_activities(activities)
        assert "Push Day" in result
        assert "[gym]" in result
        assert "75 min" in result


class TestFormatHealthTrends:
    def test_empty(self) -> None:
        assert _format_health_trends([]) == "No recent health data."

    def test_with_snapshots(self) -> None:
        snapshots = [
            {"snapshot_date": "2024-06-09", "resting_hr": 56},
            {"snapshot_date": "2024-06-08", "resting_hr": 58},
        ]
        result = _format_health_trends(snapshots)
        assert "2024-06-09" in result
        assert "2024-06-08" in result
        assert "Resting HR: 56" in result


class TestGetSystemPrompt:
    def test_loads_system_prompt(self) -> None:
        prompt = get_system_prompt("v1")
        assert "MyCoach" in prompt
        assert "JSON" in prompt


class TestBuildDailyBriefingPrompt:
    def test_builds_full_prompt(self) -> None:
        prompt = build_daily_briefing_prompt(
            health_today={"resting_hr": 55, "sleep_score": 82},
            health_trends=[{"snapshot_date": "2024-06-09", "resting_hr": 56}],
            recent_activities=[
                {
                    "title": "Push Day",
                    "sport": "gym",
                    "start_time": "2024-06-10",
                    "duration_minutes": 60,
                }
            ],
        )
        assert "Resting HR: 55" in prompt
        assert "Sleep score: 82" in prompt
        assert "Push Day" in prompt
        assert "readiness_verdict" in prompt

    def test_no_data(self) -> None:
        prompt = build_daily_briefing_prompt(
            health_today={},
            health_trends=[],
            recent_activities=[],
        )
        assert "No health data available" in prompt
        assert "No recent activities" in prompt
        assert "No planned workout" in prompt
