"""Tests for coaching response parser."""

import json
import logging

import pytest

from mycoach.coaching.response_parser import (
    CardioPlanResponse,
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
    "hrv_last_night_avg": 45.0,
    "hrv_status_text": "BALANCED",
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
        assert result.key_metrics.hrv_last_night_avg == 45.0
        assert result.key_metrics.hrv_status_text == "BALANCED"

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
        data["key_metrics"] = {"body_battery": None, "hrv_last_night_avg": None}
        result = parse_response(json.dumps(data), DailyBriefingResponse)
        assert result.key_metrics.body_battery is None
        assert result.key_metrics.sleep_score is None


class TestBriefingHrvFields:
    """The LLM confuses Garmin's two HRV fields: a number (lastNightAvg) and a
    word (status). Neither confusion may cost us the whole briefing.
    """

    def _parse_metrics(self, **key_metrics: object) -> DailyBriefingResponse:
        data = json.loads(VALID_BRIEFING)
        data["key_metrics"] = key_metrics
        return parse_response(json.dumps(data), DailyBriefingResponse)

    def test_status_word_in_numeric_field_routes_to_text(self) -> None:
        result = self._parse_metrics(hrv_last_night_avg="BALANCED")
        assert result.key_metrics.hrv_last_night_avg is None
        assert result.key_metrics.hrv_status_text == "BALANCED"

    def test_status_word_under_legacy_name_routes_to_text(self) -> None:
        result = self._parse_metrics(hrv_status="BALANCED")
        assert result.key_metrics.hrv_last_night_avg is None
        assert result.key_metrics.hrv_status_text == "BALANCED"

    def test_legacy_numeric_field_name_still_accepted(self) -> None:
        result = self._parse_metrics(hrv_status=82)
        assert result.key_metrics.hrv_last_night_avg == 82.0

    def test_numeric_string_still_parses_as_a_number(self) -> None:
        result = self._parse_metrics(hrv_last_night_avg="82.0")
        assert result.key_metrics.hrv_last_night_avg == 82.0
        assert result.key_metrics.hrv_status_text is None

    def test_routed_word_does_not_clobber_an_explicit_status_text(self) -> None:
        result = self._parse_metrics(hrv_last_night_avg="BALANCED", hrv_status_text="UNBALANCED")
        assert result.key_metrics.hrv_status_text == "UNBALANCED"

    def test_number_with_a_unit_is_still_a_number(self) -> None:
        result = self._parse_metrics(hrv_last_night_avg="82 ms")
        assert result.key_metrics.hrv_last_night_avg == 82.0
        assert result.key_metrics.hrv_status_text is None

    def test_blank_numeric_field_leaves_both_fields_empty(self) -> None:
        result = self._parse_metrics(hrv_last_night_avg="  ")
        assert result.key_metrics.hrv_last_night_avg is None
        assert result.key_metrics.hrv_status_text is None


def _cardio_plan(**session_fields: object) -> str:
    session = {"day_of_week": 1, "sport": "running", "title": "Tempo Run", **session_fields}
    return json.dumps(
        {"sessions": [session], "goal_assessment": "On track.", "weekly_summary": "One run."}
    )


class TestCardioPlanSessionIntent:
    def test_valid_intent_is_kept(self) -> None:
        result = parse_response(_cardio_plan(intent="threshold"), CardioPlanResponse)
        assert result.sessions[0].intent == "threshold"

    def test_missing_intent_is_null(self) -> None:
        result = parse_response(_cardio_plan(), CardioPlanResponse)
        assert result.sessions[0].intent is None

    def test_bogus_intent_is_null_and_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="mycoach.coaching.response_parser"):
            result = parse_response(_cardio_plan(intent="tempo"), CardioPlanResponse)
        assert result.sessions[0].intent is None
        assert "tempo" in caplog.text
