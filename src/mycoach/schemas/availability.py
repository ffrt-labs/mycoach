from datetime import date, time

from pydantic import BaseModel, Field


class AvailabilitySlot(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    duration_minutes: int = Field(gt=0)
    preferred_sport: str = Field(max_length=50)


class WeeklyAvailabilityCreate(BaseModel):
    week_start: date
    slots: list[AvailabilitySlot]


class WeeklyAvailabilityRead(AvailabilitySlot):
    id: int
    user_id: int
    week_start: date

    model_config = {"from_attributes": True}
