"""Parse and validate LLM JSON responses into Pydantic models."""

import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel, Field, ValidationError

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


class PostWorkoutResponse(BaseModel):
    performance_summary: str
    planned_vs_actual: str | None = None
    performance_trends: str
    hr_analysis: str
    training_effect_assessment: str
    key_highlights: list[str] = Field(min_length=1)
    areas_for_improvement: list[str] = Field(min_length=1)
    next_session_recommendations: str
    recovery_notes: str



class WeeklyRecapResponse(BaseModel):
    week_summary: str
    adherence_analysis: str
    performance_highlights: list[str] = Field(min_length=2, max_length=4)
    areas_of_concern: list[str] = Field(min_length=1, max_length=3)
    recovery_assessment: str
    training_load_analysis: str
    gym_coaching: str = ""
    exercise_substitutions: list[str] = Field(default_factory=list)
    cardio_coaching: str = ""
    coach_recommendations: list[str] = Field(default_factory=list)
    next_week_recommendations: str
    mesocycle_progress: str


class SlotAssignment(BaseModel):
    slot_index: int = Field(ge=0)
    track: str = Field(pattern=r"^(gym|cardio)$")
    routine_day_index: int | None = None
    rationale: str


class ScheduleDistributionResponse(BaseModel):
    schedule: list[SlotAssignment] = Field(min_length=1)
    distribution_notes: str


class GymAdjustmentExercise(BaseModel):
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


class CardioSessionResponse(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    sport: str
    title: str
    duration_minutes: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class CardioPlanResponse(BaseModel):
    sessions: list[CardioSessionResponse] = Field(min_length=1)
    goal_assessment: str
    weekly_summary: str


class DailyBriefingKeyMetrics(BaseModel):
    body_battery: int | None = None
    hrv_status: float | None = None
    sleep_score: int | None = None
    training_readiness: int | None = None
    resting_hr: int | None = None


class DailyBriefingResponse(BaseModel):
    sleep_assessment: str
    recovery_status: str
    readiness_verdict: str = Field(pattern=r"^(go_hard|moderate|active_recovery|rest)$")
    readiness_explanation: str
    workout_adjustments: str
    sleep_recommendation: str
    key_metrics: DailyBriefingKeyMetrics


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
