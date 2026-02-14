"""Tests for coaching response parser."""

import json

import pytest

from mycoach.coaching.response_parser import (
    DailyBriefingResponse,
    _extract_json,
    parse_response,
)


class TestExtractJson:
    def test_plain_json(self) -> None:
        text = '{"key": "value"}'
        assert _extract_json(text) == '{"key": "value"}'

    def test_json_in_code_block(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        assert _extract_json(text) == '{"key": "value"}'

    def test_json_in_plain_code_block(self) -> None:
        text = '```\n{"key": "value"}\n```'
        assert _extract_json(text) == '{"key": "value"}'

    def test_json_with_surrounding_text(self) -> None:
        text = 'Here is the result:\n{"key": "value"}\nDone.'
        assert _extract_json(text) == '{"key": "value"}'


VALID_BRIEFING = """{
  "sleep_assessment": "Good sleep with 7.5 hours duration.",
  "recovery_status": "Well recovered, Body Battery at 80.",
  "readiness_verdict": "go_hard",
  "readiness_explanation": "HRV is above baseline, Body Battery high.",
  "workout_adjustments": "No adjustments needed.",
  "sleep_recommendation": "Aim for 10:30 PM bedtime.",
  "key_metrics": {
    "body_battery": 80,
    "hrv_status": 45.0,
    "sleep_score": 82,
    "training_readiness": 75,
    "resting_hr": 55
  }
}"""


class TestParseResponse:
    def test_valid_json(self) -> None:
        result = parse_response(VALID_BRIEFING, DailyBriefingResponse)
        assert result.readiness_verdict == "go_hard"
        assert result.key_metrics.body_battery == 80
        assert result.key_metrics.hrv_status == 45.0

    def test_json_in_code_block(self) -> None:
        text = f"```json\n{VALID_BRIEFING}\n```"
        result = parse_response(text, DailyBriefingResponse)
        assert result.readiness_verdict == "go_hard"

    def test_invalid_json(self) -> None:
        with pytest.raises(ValueError, match="Failed to parse JSON"):
            parse_response("not json at all", DailyBriefingResponse)

    def test_invalid_verdict(self) -> None:
        bad = VALID_BRIEFING.replace("go_hard", "invalid_verdict")
        with pytest.raises(ValueError, match="failed validation"):
            parse_response(bad, DailyBriefingResponse)

    def test_missing_required_field(self) -> None:
        data = json.loads(VALID_BRIEFING)
        del data["sleep_assessment"]
        with pytest.raises(ValueError, match="failed validation"):
            parse_response(json.dumps(data), DailyBriefingResponse)

    def test_null_key_metrics(self) -> None:
        data = json.loads(VALID_BRIEFING)
        data["key_metrics"] = {"body_battery": None, "hrv_status": None}
        result = parse_response(json.dumps(data), DailyBriefingResponse)
        assert result.key_metrics.body_battery is None
        assert result.key_metrics.sleep_score is None
