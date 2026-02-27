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
        data["areas_of_concern"] = ["One", "Two", "Three", "Four"]
        with pytest.raises(ValueError, match="failed validation"):
            parse_response(json.dumps(data), WeeklyRecapResponse)
