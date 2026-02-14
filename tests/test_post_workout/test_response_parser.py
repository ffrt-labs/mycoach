"""Tests for PostWorkoutResponse parsing."""

import pytest

from mycoach.coaching.response_parser import PostWorkoutResponse, parse_response

VALID_JSON = """{
  "performance_summary": "Solid session.",
  "planned_vs_actual": "Matched planned workout.",
  "performance_trends": "Improving steadily.",
  "hr_analysis": "Average HR 130bpm, zone 2-3.",
  "training_effect_assessment": "Good aerobic stimulus.",
  "key_highlights": ["PR on bench press", "Good form"],
  "areas_for_improvement": ["Rest times"],
  "next_session_recommendations": "Increase weight by 2.5kg.",
  "recovery_notes": "Get 7+ hours sleep."
}"""


class TestPostWorkoutResponseParsing:
    def test_valid_json(self) -> None:
        result = parse_response(VALID_JSON, PostWorkoutResponse)
        assert result.performance_summary == "Solid session."
        assert len(result.key_highlights) == 2
        assert len(result.areas_for_improvement) == 1

    def test_json_in_code_block(self) -> None:
        wrapped = f"```json\n{VALID_JSON}\n```"
        result = parse_response(wrapped, PostWorkoutResponse)
        assert result.performance_summary == "Solid session."

    def test_missing_required_field(self) -> None:
        bad_json = '{"performance_summary": "test"}'
        with pytest.raises(ValueError, match="validation"):
            parse_response(bad_json, PostWorkoutResponse)

    def test_empty_highlights_fails(self) -> None:
        import json

        data = json.loads(VALID_JSON)
        data["key_highlights"] = []
        with pytest.raises(ValueError, match="validation"):
            parse_response(json.dumps(data), PostWorkoutResponse)
