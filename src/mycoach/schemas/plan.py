from datetime import date, datetime

from pydantic import BaseModel, Field


class PlannedSessionBase(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    sport: str = Field(max_length=50)
    title: str = Field(max_length=200)
    duration_minutes: int | None = None
    details: str | None = None
    notes: str | None = None


class PlannedSessionCreate(PlannedSessionBase):
    pass


class PlannedSessionRead(PlannedSessionBase):
    id: int
    plan_id: int
    completed: bool
    activity_id: int | None = None

    model_config = {"from_attributes": True}


class WeeklyPlanBase(BaseModel):
    week_start: date
    mesocycle_week: int | None = None
    mesocycle_phase: str | None = Field(
        default=None, pattern=r"^(build|peak|deload)$"
    )


class WeeklyPlanCreate(WeeklyPlanBase):
    sessions: list[PlannedSessionCreate] | None = None


class WeeklyPlanRead(WeeklyPlanBase):
    id: int
    user_id: int
    prompt_version: str
    status: str
    summary: str | None = None
    created_at: datetime
    sessions: list[PlannedSessionRead] | None = None

    model_config = {"from_attributes": True}
