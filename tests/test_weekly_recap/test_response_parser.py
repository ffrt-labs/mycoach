"""Tests for weekly recap response parser."""

import pytest

from mycoach.coaching.response_parser import WeeklyRecapResponse, parse_response

VALID_RECAP_JSON = """{
  "week_summary": "Strong training week with 4/5 sessions completed.",
  "adherence_analysis": "Missed Friday padel due to weather. All gym sessions completed.",
  "performance_highlights": ["New bench press PR at 85kg", "Improved 100m swim time"],
  "areas_of_concern": ["Elevated Resting Heart Rate mid-week suggests accumulated fatigue"],
  "recovery_assessment": "Sleep quality dipped mid-week. HRV trending down slightly.",
  "training_load_analysis": "Good distribution across gym and swimming. Padel volume low.",
  "next_week_recommendations": "Reduce gym volume by 10% and prioritize recovery.",
  "mesocycle_progress": "Week 3 of 4 in build phase. On track for deload next week."
}"""


class TestWeeklyRecapParser:
    def test_valid_json(self) -> None:
        result = parse_response(VALID_RECAP_JSON, WeeklyRecapResponse)
        assert result.week_summary == "Strong training week with 4/5 sessions completed."
        assert len(result.performance_highlights) == 2
        assert len(result.areas_of_concern) == 1

    def test_code_block(self) -> None:
        wrapped = f"```json\n{VALID_RECAP_JSON}\n```"
        result = parse_response(wrapped, WeeklyRecapResponse)
        assert result.week_summary == "Strong training week with 4/5 sessions completed."

    def test_missing_required_field(self) -> None:
        bad_json = '{"week_summary": "test"}'
        with pytest.raises(ValueError, match="failed validation"):
            parse_response(bad_json, WeeklyRecapResponse)

    def test_too_few_highlights(self) -> None:
        import json

        data = json.loads(VALID_RECAP_JSON)
        data["performance_highlights"] = ["Only one"]
        with pytest.raises(ValueError, match="failed validation"):
            parse_response(json.dumps(data), WeeklyRecapResponse)

    def test_too_many_concerns(self) -> None:
        import json

        data = json.loads(VALID_RECAP_JSON)
        data["areas_of_concern"] = ["One", "Two", "Three", "Four", "Five", "Six"]
        with pytest.raises(ValueError, match="failed validation"):
            parse_response(json.dumps(data), WeeklyRecapResponse)

    def test_structured_gym_coaching(self) -> None:
        import json

        data = json.loads(VALID_RECAP_JSON)
        data["gym_coaching"] = [
            {
                "day_label": "Day 1 (Push)",
                "exercises": [
                    "Bench Press: Prog. 80kg×8 vs 77.5kg×8",
                    "Lateral Raise: Plateau. 20kg×10 for 3 weeks",
                ],
            },
            {
                "day_label": "Day 2 (Pull)",
                "exercises": ["Deadlift: Regressed. 120kg×4 vs 120kg×5"],
            },
        ]
        result = parse_response(json.dumps(data), WeeklyRecapResponse)
        assert len(result.gym_coaching) == 2
        assert result.gym_coaching[0].day_label == "Day 1 (Push)"
        assert len(result.gym_coaching[0].exercises) == 2
        assert len(result.gym_coaching[1].exercises) == 1

    def test_structured_cardio_coaching(self) -> None:
        import json

        data = json.loads(VALID_RECAP_JSON)
        data["cardio_coaching"] = [
            {
                "sport": "Swimming",
                "analysis": "1250m at 2:30/100m, SWOLF 53.",
                "recommendation": "Add catch-up drill sets for stroke length.",
            },
            {
                "sport": "Running",
                "analysis": "6.6km at 6:48/km, avg HR 131.",
                "recommendation": "Add 2-3 tempo segments within easy runs.",
            },
        ]
        result = parse_response(json.dumps(data), WeeklyRecapResponse)
        assert len(result.cardio_coaching) == 2
        assert result.cardio_coaching[0].sport == "Swimming"
        assert result.cardio_coaching[1].sport == "Running"

    def test_empty_gym_and_cardio_defaults(self) -> None:
        result = parse_response(VALID_RECAP_JSON, WeeklyRecapResponse)
        assert result.gym_coaching == []
        assert result.cardio_coaching == []
