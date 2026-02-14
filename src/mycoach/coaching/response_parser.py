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
        return match.group(1).strip()
    # Try to find a raw JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    return text.strip()


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
    json_str = _extract_json(text)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON from LLM response: {e}") from e

    try:
        return model_class.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"LLM response failed validation: {e}") from e
