"""Tests for coaching prompt builder."""

import json

from mycoach.coaching.prompt_builder import (
    _format_activities,
    _format_activity_detail,
    _format_health,
    _format_health_trends,
    _format_hr_zones,
    _format_load_focus,
    _format_swimming_activity_detail,
    _format_training_status,
    build_daily_briefing_prompt,
    build_post_workout_prompt,
    get_system_prompt,
)


class TestFormatHealth:
    def test_empty(self) -> None:
        assert _format_health({}) == "No health data available for today."

    def test_with_data(self) -> None:
        data = {"resting_hr": 55, "sleep_score": 82}
        result = _format_health(data)
        assert "Resting Heart Rate: 55" in result
        assert "Sleep score: 82" in result

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

    def test_includes_max_hr(self) -> None:
        data = {"max_hr": 185}
        result = _format_health(data)
        assert "Max HR (all day): 185" in result

    def test_includes_new_health_fields(self) -> None:
        data = {
            "hrv_status_text": "BALANCED",
            "body_battery_morning": 75,
            "load_focus": '{"aerobic_low": 30, "aerobic_high": 50, "anaerobic": 20}',
        }
        result = _format_health(data)
        assert "HRV Status: BALANCED" in result
        assert "Body Battery (morning): 75" in result
        assert "Load Focus: Low aerobic 30.0, High aerobic 50.0, Anaerobic 20.0" in result

    def test_training_status_formatted(self) -> None:
        data = {"training_status": "5"}
        result = _format_health(data)
        assert "Training status: Peaking" in result

        data2 = {"training_status": "PRODUCTIVE"}
        result2 = _format_health(data2)
        assert "Training status: Productive" in result2

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

    def test_with_enriched_swimming_activity(self) -> None:
        activities = [
            {
                "title": "Morning Swim",
                "sport": "swimming",
                "start_time": "2024-06-10 07:00:00",
                "duration_minutes": 60,
                "distance_meters": 2400,
                "avg_swolf": 42.0,
                "avg_strokes_per_length": 18,
                "epoc": 85.5,
            }
        ]
        result = _format_activities(activities)
        assert "Morning Swim" in result
        assert "[swimming]" in result
        assert "60 min" in result
        assert "2400m" in result
        assert "pace 2:30/100m" in result
        assert "SWOLF 42.0" in result
        assert "18 str/len" in result
        assert "load 85.5" in result
        # Should NOT contain generic fields for swimming
        assert "avg HR" not in result
        assert "cal" not in result

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


class TestFormatHrZones:
    def test_minutes_format(self) -> None:
        zones = json.dumps([
            {"zone": 1, "minutes": 10},
            {"zone": 2, "minutes": 25},
            {"zone": 3, "minutes": 15},
            {"zone": 4, "minutes": 5},
            {"zone": 5, "minutes": 2},
        ])
        result = _format_hr_zones(zones)
        assert "Zone 1: 10 min" in result
        assert "Zone 2: 25 min" in result
        assert "Zone 5: 2 min" in result

    def test_garmin_secs_format(self) -> None:
        zones = json.dumps([
            {"zoneNumber": 1, "secsInZone": 600},
            {"zoneNumber": 2, "secsInZone": 1500},
        ])
        result = _format_hr_zones(zones)
        assert "Zone 1: 10.0 min" in result
        assert "Zone 2: 25.0 min" in result

    def test_invalid_json_returns_raw(self) -> None:
        assert _format_hr_zones("not json") == "not json"

    def test_empty_list_returns_raw(self) -> None:
        assert _format_hr_zones("[]") == "[]"


class TestFormatActivityDetail:
    def test_hr_zones_formatted(self) -> None:
        activity = {
            "title": "Morning Run",
            "sport": "running",
            "hr_zones": json.dumps([{"zone": 1, "minutes": 10}, {"zone": 2, "minutes": 20}]),
        }
        result = _format_activity_detail(activity)
        assert "HR zones: Zone 1: 10 min, Zone 2: 20 min" in result
        # Should NOT contain raw JSON
        assert '"zone"' not in result

    def test_no_hr_zones(self) -> None:
        activity = {"title": "Walk", "sport": "other"}
        result = _format_activity_detail(activity)
        assert "HR zones" not in result

    def test_includes_new_activity_fields_non_swimming(self) -> None:
        activity = {
            "title": "Morning Run",
            "sport": "running",
            "epoc": 85.5,
            "recovery_time_minutes": 18,
            "avg_cadence": 180,
        }
        result = _format_activity_detail(activity)
        assert "EPOC: 85.5" in result
        assert "Recovery time (min): 18" in result
        assert "Avg cadence (spm): 180" in result

    def test_swimming_dispatches_to_swimming_formatter(self) -> None:
        activity = {
            "title": "Morning Swim",
            "sport": "swimming",
            "distance_meters": 2400,
            "epoc": 85.5,
            "avg_swolf": 42.0,
        }
        result = _format_activity_detail(activity)
        assert "Activity training load: 85.5" in result
        assert "Avg SWOLF: 42.0" in result
        # Should NOT contain generic EPOC label
        assert "- EPOC: 85.5" not in result


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


class TestFormatTrainingStatus:
    def test_numeric_values(self) -> None:
        assert _format_training_status("0") == "No Status"
        assert _format_training_status("4") == "Productive"
        assert _format_training_status("5") == "Peaking"
        assert _format_training_status("8") == "Strained"

    def test_string_uppercase(self) -> None:
        assert _format_training_status("PRODUCTIVE") == "Productive"
        assert _format_training_status("OVERREACHING") == "Overreaching"

    def test_fallback(self) -> None:
        assert _format_training_status("something_else") == "something_else"
        assert _format_training_status(42) == "42"


class TestFormatLoadFocus:
    def test_full_json(self) -> None:
        val = json.dumps({
            "aerobic_low": 148.26,
            "aerobic_high": 0.0,
            "anaerobic": 10.58,
            "feedback": "AEROBIC_LOW_SHORTAGE",
        })
        result = _format_load_focus(val)
        assert "Low aerobic 148.3" in result
        assert "High aerobic 0.0" in result
        assert "Anaerobic 10.6" in result
        assert "Lacking low-aerobic training load" in result

    def test_balanced_feedback(self) -> None:
        val = json.dumps({
            "aerobic_low": 50.0,
            "aerobic_high": 50.0,
            "anaerobic": 50.0,
            "feedback": "BALANCED",
        })
        result = _format_load_focus(val)
        assert "Balanced training load" in result

    def test_unknown_feedback_title_cased(self) -> None:
        val = json.dumps({
            "aerobic_low": 10.0,
            "feedback": "SOME_NEW_STATUS",
        })
        result = _format_load_focus(val)
        assert "Some New Status" in result

    def test_invalid_json_returns_raw(self) -> None:
        assert _format_load_focus("not json") == "not json"

    def test_none_returns_str(self) -> None:
        assert _format_load_focus(None) == "None"


class TestActivityDetailPace:
    def test_swimming_pace(self) -> None:
        activity = {
            "title": "Swim",
            "sport": "swimming",
            "duration_minutes": 60,
            "distance_meters": 2400,
        }
        result = _format_activity_detail(activity)
        assert "Avg pace: 2:30/100m" in result

    def test_running_pace(self) -> None:
        activity = {
            "title": "Run",
            "sport": "running",
            "duration_minutes": 30,
            "distance_meters": 5000,
        }
        result = _format_activity_detail(activity)
        assert "Avg pace (min/km): 6:00/km" in result

    def test_gym_no_pace(self) -> None:
        activity = {
            "title": "Push",
            "sport": "gym",
            "duration_minutes": 60,
            "distance_meters": 100,
        }
        result = _format_activity_detail(activity)
        assert "pace" not in result.lower()

    def test_no_speed_field(self) -> None:
        """avg_speed_mps should no longer appear in output."""
        activity = {
            "title": "Run",
            "sport": "running",
            "avg_speed_mps": 3.5,
        }
        result = _format_activity_detail(activity)
        assert "speed" not in result.lower()

    def test_running_cadence_label(self) -> None:
        activity = {"title": "Run", "sport": "running", "avg_cadence": 180}
        result = _format_activity_detail(activity)
        assert "Avg cadence (spm): 180" in result

    def test_swimming_strokes_per_length(self) -> None:
        activity = {"title": "Swim", "sport": "swimming", "avg_strokes_per_length": 18}
        result = _format_activity_detail(activity)
        assert "Strokes per length: 18" in result

    def test_gym_no_cadence(self) -> None:
        activity = {"title": "Push", "sport": "gym", "avg_cadence": 10}
        result = _format_activity_detail(activity)
        assert "cadence" not in result.lower()

    def test_max_hr_label(self) -> None:
        activity = {"title": "Run", "sport": "running", "max_hr": 185}
        result = _format_activity_detail(activity)
        assert "Max HR (activity): 185" in result


class TestPostWorkoutConditionalSections:
    def test_no_gym_section_when_empty(self) -> None:
        result = build_post_workout_prompt(
            activity={"title": "Swim", "sport": "swimming"},
            gym_details=[],
            planned_session=None,
            similar_activities=[],
            health_context={},
        )
        assert "## Gym Workout Details" not in result

    def test_gym_section_when_present(self) -> None:
        result = build_post_workout_prompt(
            activity={"title": "Push", "sport": "gym"},
            gym_details=[{
                "exercise_title": "Bench", "set_index": 1,
                "set_type": "normal", "weight_kg": 80, "reps": 8,
            }],
            planned_session=None,
            similar_activities=[],
            health_context={},
        )
        assert "## Gym Workout Details" in result
        assert "Bench" in result

    def test_no_planned_section_when_none(self) -> None:
        result = build_post_workout_prompt(
            activity={"title": "Swim", "sport": "swimming"},
            gym_details=[],
            planned_session=None,
            similar_activities=[],
            health_context={},
        )
        assert "## Planned Session" not in result
        assert "planned_vs_actual" not in result

    def test_planned_section_when_present(self) -> None:
        result = build_post_workout_prompt(
            activity={"title": "Swim", "sport": "swimming"},
            gym_details=[],
            planned_session={"title": "Swim session", "sport": "swimming", "duration_minutes": 60},
            similar_activities=[],
            health_context={},
        )
        assert "## Planned Session" in result
        assert "planned_vs_actual" in result

    def test_glossary_present(self) -> None:
        result = build_post_workout_prompt(
            activity={"title": "Swim", "sport": "swimming"},
            gym_details=[],
            planned_session=None,
            similar_activities=[],
            health_context={"training_status": "4"},
        )
        assert "## Glossary" in result
        assert "SWOLF" in result
        assert "EPOC" in result
        assert "Training Status: Productive" in result

    def test_sport_aware_recommendations_swimming(self) -> None:
        result = build_post_workout_prompt(
            activity={"title": "Swim", "sport": "swimming"},
            gym_details=[],
            planned_session=None,
            similar_activities=[],
            health_context={},
        )
        assert "stroke technique focus" in result

    def test_sport_aware_recommendations_gym(self) -> None:
        result = build_post_workout_prompt(
            activity={"title": "Push", "sport": "gym"},
            gym_details=[],
            planned_session=None,
            similar_activities=[],
            health_context={},
        )
        assert "weights, volume, intensity adjustments" in result

    def test_sport_aware_recommendations_running(self) -> None:
        result = build_post_workout_prompt(
            activity={"title": "Run", "sport": "running"},
            gym_details=[],
            planned_session=None,
            similar_activities=[],
            health_context={},
        )
        assert "pace targets" in result


class TestSwimmingActivityDetail:
    def test_all_10_metrics(self) -> None:
        activity = {
            "sport": "swimming",
            "distance_meters": 2400,
            "moving_duration_seconds": 2700.0,
            "duration_minutes": 60,
            "hr_zones": json.dumps([
                {"zoneNumber": 1, "secsInZone": 300},
                {"zoneNumber": 2, "secsInZone": 1200},
                {"zoneNumber": 3, "secsInZone": 900},
            ]),
            "training_effect_aerobic": 3.5,
            "epoc": 92.0,
            "fastest_split_100_seconds": 105.0,
            "avg_swolf": 38.0,
            "avg_strokes_per_length": 16,
        }
        result = _format_swimming_activity_detail(activity)
        assert "Total distance: 2400m" in result
        assert "Moving duration: 45:00" in result
        assert "Avg pace: 1:52/100m" in result
        assert "HR time in zones:" in result
        assert "Zone 2:" in result
        assert "Zone 3:" in result
        assert "Aerobic training effect: 3.5" in result
        assert "Activity training load: 92.0" in result
        assert "Rest/active ratio:" in result
        assert "Fastest 100m: 1:45/100m" in result
        assert "Avg SWOLF: 38.0" in result
        assert "Strokes per length: 16" in result

    def test_pace_falls_back_to_total_duration(self) -> None:
        activity = {
            "sport": "swimming",
            "distance_meters": 2400,
            "duration_minutes": 60,
        }
        result = _format_swimming_activity_detail(activity)
        assert "Avg pace: 2:30/100m" in result

    def test_rest_active_ratio_not_shown_without_moving_duration(self) -> None:
        activity = {
            "sport": "swimming",
            "distance_meters": 2400,
            "duration_minutes": 60,
        }
        result = _format_swimming_activity_detail(activity)
        assert "Rest/active ratio" not in result

    def test_empty_swimming_activity(self) -> None:
        result = _format_swimming_activity_detail({"sport": "swimming"})
        assert result == "No activity data."

    def test_no_generic_fields_leaked(self) -> None:
        activity = {
            "sport": "swimming",
            "distance_meters": 2400,
            "calories": 500,
            "avg_hr": 140,
            "max_hr": 170,
            "recovery_time_minutes": 20,
        }
        result = _format_swimming_activity_detail(activity)
        assert "Calories" not in result
        assert "Avg HR" not in result
        assert "Max HR" not in result
        assert "Recovery time" not in result
