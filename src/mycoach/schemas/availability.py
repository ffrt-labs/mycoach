from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

SportType = Literal["gym", "swimming", "running", "padel"]


class AvailabilitySlot(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    sport: SportType


class WeeklyAvailabilityCreate(BaseModel):
    week_start: date
    slots: list[AvailabilitySlot]


class WeeklyAvailabilityRead(BaseModel):
    id: int
    user_id: int
    week_start: date
    day_of_week: int
    sport: str | None = None  # nullable for backward compat with existing rows

    model_config = {"from_attributes": True}
