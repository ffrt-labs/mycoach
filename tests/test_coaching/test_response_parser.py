"""Tests for coaching response parser."""

import json

import pytest

from mycoach.coaching.response_parser import (
    DailyBriefingKeyMetrics,
    GymAdjustmentResponse,
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


VALID_ADJUSTMENT = """{
  "exercises": [
    {
      "exercise_name": "Bench Press",
      "target_weight_kg": 80.0,
      "target_rpe": 8,
      "adjustment_rationale": "Last week moved well."
    }
  ],
  "session_notes": "Push the top set."
}"""


class TestParseResponse:
    """``parse_response`` stays for gym adjustment, still on v4 (#244)."""

    def test_valid_json(self) -> None:
        result = parse_response(VALID_ADJUSTMENT, GymAdjustmentResponse)
        assert result.exercises[0].exercise_name == "Bench Press"
        assert result.exercises[0].target_weight_kg == 80.0
        assert result.session_notes == "Push the top set."

    def test_json_in_code_block(self) -> None:
        text = f"```json\n{VALID_ADJUSTMENT}\n```"
        result = parse_response(text, GymAdjustmentResponse)
        assert result.session_notes == "Push the top set."

    def test_invalid_json(self) -> None:
        with pytest.raises(ValueError, match="Failed to parse JSON"):
            parse_response("not json at all", GymAdjustmentResponse)

    def test_out_of_range_value(self) -> None:
        bad = VALID_ADJUSTMENT.replace('"target_rpe": 8', '"target_rpe": 11')
        with pytest.raises(ValueError, match="failed validation"):
            parse_response(bad, GymAdjustmentResponse)

    def test_missing_required_field(self) -> None:
        data = json.loads(VALID_ADJUSTMENT)
        del data["session_notes"]
        with pytest.raises(ValueError, match="failed validation"):
            parse_response(json.dumps(data), GymAdjustmentResponse)

    def test_empty_response(self) -> None:
        with pytest.raises(ValueError, match="empty response"):
            parse_response("  ", GymAdjustmentResponse)


class TestBriefingHrvFields:
    """The LLM confuses Garmin's two HRV fields: a number (lastNightAvg) and a
    word (status). Neither confusion may cost us the whole briefing.
    """

    def _parse_metrics(self, **key_metrics: object) -> DailyBriefingKeyMetrics:
        return DailyBriefingKeyMetrics.model_validate(key_metrics)

    def test_status_word_in_numeric_field_routes_to_text(self) -> None:
        result = self._parse_metrics(hrv_last_night_avg="BALANCED")
        assert result.hrv_last_night_avg is None
        assert result.hrv_status_text == "BALANCED"

    def test_status_word_under_legacy_name_routes_to_text(self) -> None:
        result = self._parse_metrics(hrv_status="BALANCED")
        assert result.hrv_last_night_avg is None
        assert result.hrv_status_text == "BALANCED"

    def test_legacy_numeric_field_name_still_accepted(self) -> None:
        result = self._parse_metrics(hrv_status=82)
        assert result.hrv_last_night_avg == 82.0

    def test_numeric_string_still_parses_as_a_number(self) -> None:
        result = self._parse_metrics(hrv_last_night_avg="82.0")
        assert result.hrv_last_night_avg == 82.0
        assert result.hrv_status_text is None

    def test_routed_word_does_not_clobber_an_explicit_status_text(self) -> None:
        result = self._parse_metrics(hrv_last_night_avg="BALANCED", hrv_status_text="UNBALANCED")
        assert result.hrv_status_text == "UNBALANCED"

    def test_number_with_a_unit_is_still_a_number(self) -> None:
        result = self._parse_metrics(hrv_last_night_avg="82 ms")
        assert result.hrv_last_night_avg == 82.0
        assert result.hrv_status_text is None

    def test_blank_numeric_field_leaves_both_fields_empty(self) -> None:
        result = self._parse_metrics(hrv_last_night_avg="  ")
        assert result.hrv_last_night_avg is None
        assert result.hrv_status_text is None
