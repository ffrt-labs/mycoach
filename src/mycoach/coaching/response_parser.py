"""Parse and validate LLM JSON responses into Pydantic models."""

import json
import logging
import re
from typing import Any, TypeVar

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class WeeklyPlanSessionResponse(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    sport: str
    title: str
    duration_minutes: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class WeeklyPlanResponse(BaseModel):
    summary: str
    sessions: list[WeeklyPlanSessionResponse] = Field(min_length=1)


class SlotAssignment(BaseModel):
    slot_index: int = Field(ge=0)
    track: str = Field(pattern=r"^(gym|cardio)$")
    routine_day_index: int | None = None
    rationale: str


class ScheduleDistributionResponse(BaseModel):
    schedule: list[SlotAssignment] = Field(min_length=1)
    distribution_notes: str


class GymAdjustmentExercise(BaseModel):
    exercise_id: str | None = None
    exercise_name: str
    target_weight_kg: float | None = None
    target_rpe: int | None = Field(default=None, ge=1, le=10)
    rest_seconds: int | None = None
    adjustment_rationale: str
    notes: str | None = None


class GymAdjustmentResponse(BaseModel):
    exercises: list[GymAdjustmentExercise] = Field(min_length=1)
    session_notes: str
    estimated_duration_minutes: int | None = None


# The keys the model may put the overnight HRV number under: the current field
# name and the pre-rename one, which older stored briefings still carry.
_HRV_NUMERIC_KEYS = ("hrv_last_night_avg", "hrv_status")


class DailyBriefingKeyMetrics(BaseModel):
    """The briefing's headline numbers.

    HRV arrives from Garmin's ``hrvSummary`` as two different things — a number
    (``lastNightAvg``) and a word (``status``: "BALANCED") — so this model keeps
    them in two fields whose names say which is which. The old name for the
    number, ``hrv_status``, read like it wanted the word, and the model
    periodically obliged and lost the whole briefing to a validation error.
    """

    model_config = ConfigDict(populate_by_name=True)

    body_battery: int | None = None
    hrv_last_night_avg: float | None = Field(
        default=None,
        validation_alias=AliasChoices(*_HRV_NUMERIC_KEYS),
    )
    hrv_status_text: str | None = None
    sleep_score: int | None = None
    training_readiness: int | None = None
    resting_hr: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _route_status_word_out_of_the_number(cls, data: Any) -> Any:
        """Move a non-numeric HRV value into the text field instead of failing.

        A briefing that names the HRV state but not its value is worth far more
        than no briefing at all, and this is the one field the model is known to
        confuse. An explicit ``hrv_status_text`` wins over the routed word.
        """
        if not isinstance(data, dict):
            return data
        for key in _HRV_NUMERIC_KEYS:
            value = data.get(key)
            if not isinstance(value, str):
                continue
            text = value.strip()
            try:
                float(text)
            except ValueError:
                pass
            else:
                continue  # a plain numeric string; pydantic coerces it itself
            data = {**data, key: None}
            if not text:
                continue
            number = re.fullmatch(r"([-+]?\d*\.?\d+)\s*[a-zA-Z/%]*", text)
            if number:
                # "82 ms" and friends: a number wearing a unit, not a status word.
                data[key] = float(number.group(1))
                continue
            logger.warning(
                "LLM returned %r for the numeric %s; routing it to hrv_status_text",
                value,
                key,
            )
            data.setdefault("hrv_status_text", None)
            if data["hrv_status_text"] is None:
                data["hrv_status_text"] = text
        return data


def _extract_json(text: str) -> str:
    """Extract JSON from LLM response, handling markdown code blocks."""
    # Try to find JSON in a code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        result = match.group(1).strip()
        if not result:
            raise ValueError("No JSON object found in LLM response")
        return result
    # Try to find a raw JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    result = text.strip()
    if not result:
        raise ValueError("No JSON object found in LLM response")
    return result


def parse_response(text: str, model_class: type[T]) -> T:
    """Parse LLM text response into a Pydantic model.

    Args:
        text: Raw LLM response text (may contain markdown/code blocks).
        model_class: Pydantic model to validate against.

    Returns:
        Validated Pydantic model instance.

    Raises:
        ValueError: If JSON extraction or Pydantic validation fails.
    """
    if not text or not text.strip():
        raise ValueError("LLM returned empty response")

    json_str = _extract_json(text)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON from LLM response: {e}") from e

    try:
        return model_class.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"LLM response failed validation: {e}") from e
