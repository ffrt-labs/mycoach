"""Tests for sleep coaching response parsing."""

import pytest

from mycoach.coaching.response_parser import SleepCoachingResponse, parse_response

VALID_SLEEP_JSON = """{
  "sleep_quality_summary": "Overall good sleep quality with 7.2h average duration.",
  "consistency_analysis": "Bedtime varies by ~45 min, reasonably consistent.",
  "sleep_architecture": "Deep sleep averaging 1.5h, REM 1.8h — both in healthy range.",
  "performance_correlation": "Better sleep scores correlate with higher training readiness.",
  "recommended_bedtime": "22:30",
  "recommended_wake_time": "06:00",
  "sleep_debt_assessment": "No significant sleep debt. Minor deficit on Tuesday recovered.",
  "hygiene_tips": ["Limit screen time 1h before bed", "Keep bedroom at 18-19C"],
  "key_concern": "None"
}"""


class TestSleepCoachingResponse:
    def test_valid_json(self) -> None:
        result = parse_response(VALID_SLEEP_JSON, SleepCoachingResponse)
        assert "7.2h average" in result.sleep_quality_summary
        assert result.recommended_bedtime == "22:30"
        assert len(result.hygiene_tips) == 2

    def test_code_block(self) -> None:
        wrapped = f"```json\n{VALID_SLEEP_JSON}\n```"
        result = parse_response(wrapped, SleepCoachingResponse)
        assert result.recommended_wake_time == "06:00"

    def test_missing_field_raises(self) -> None:
        bad_json = '{"sleep_quality_summary": "Good"}'
        with pytest.raises(ValueError, match="failed validation"):
            parse_response(bad_json, SleepCoachingResponse)

    def test_too_few_tips_raises(self) -> None:
        import json

        data = json.loads(VALID_SLEEP_JSON)
        data["hygiene_tips"] = ["Only one tip"]
        with pytest.raises(ValueError, match="failed validation"):
            parse_response(json.dumps(data), SleepCoachingResponse)

    def test_four_tips_valid(self) -> None:
        import json

        data = json.loads(VALID_SLEEP_JSON)
        data["hygiene_tips"] = ["Tip 1", "Tip 2", "Tip 3", "Tip 4"]
        result = parse_response(json.dumps(data), SleepCoachingResponse)
        assert len(result.hygiene_tips) == 4
