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
        assert "Resting Heart Rate: 55" in result
        assert "Sleep score: 82" in result
        assert "Body Battery high: 80" in result

    def test_includes_sleep_stages(self) -> None:
        data = {
            "sleep_deep_minutes": 90,
            "sleep_light_minutes": 200,
            "sleep_rem_minutes": 110,
            "sleep_awake_minutes": 30,
        }
        result = _format_health(data)
        assert "Deep sleep (min): 90" in result
        assert "Light sleep (min): 200" in result
        assert "REM sleep (min): 110" in result
        assert "Awake time (min): 30" in result

    def test_includes_body_battery_low_and_max_hr(self) -> None:
        data = {"body_battery_low": 20, "max_hr": 185}
        result = _format_health(data)
        assert "Body Battery low: 20" in result
        assert "Max HR: 185" in result

    def test_ignores_none_values(self) -> None:
        data = {"resting_hr": 55, "avg_hr": None}
        result = _format_health(data)
        assert "Resting Heart Rate: 55" in result
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

    def test_with_enriched_activity_data(self) -> None:
        activities = [
            {
                "title": "Morning Swim",
                "sport": "swimming",
                "start_time": "2024-06-10 07:00:00",
                "duration_minutes": 60,
                "distance_meters": 2400,
                "avg_hr": 142,
                "calories": 450,
                "training_effect_aerobic": 3.2,
            }
        ]
        result = _format_activities(activities)
        assert "Morning Swim" in result
        assert "[swimming]" in result
        assert "60 min" in result
        assert "2.4km" in result
        assert "avg HR 142" in result
        assert "450 cal" in result
        assert "TE 3.2" in result

    def test_activity_short_distance_in_meters(self) -> None:
        activities = [
            {
                "title": "Sprint",
                "sport": "running",
                "start_time": "2024-06-10",
                "duration_minutes": 10,
                "distance_meters": 800,
            }
        ]
        result = _format_activities(activities)
        assert "800m" in result


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
        assert "Resting Heart Rate: 56" in result


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
        assert "Resting Heart Rate: 55" in prompt
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
