from datetime import date, datetime

from pydantic import BaseModel, Field


class CoachingInsightBase(BaseModel):
    insight_date: date
    insight_type: str = Field(pattern=r"^(daily_briefing|post_workout|sleep|weekly_recap)$")
    content: str
    prompt_version: str = "v1"
    activity_id: int | None = None


class CoachingInsightCreate(CoachingInsightBase):
    pass


class CoachingInsightRead(CoachingInsightBase):
    id: int
    user_id: int
    created_at: datetime

    model_config = {"from_attributes": True}


class MesocycleConfigBase(BaseModel):
    sport: str = Field(max_length=50)
    block_length_weeks: int = Field(default=4, ge=1)
    current_week: int = Field(default=1, ge=1)
    phase: str = Field(default="build", pattern=r"^(build|peak|deload)$")
    start_date: date
    progression_rules: str | None = None


class MesocycleConfigCreate(MesocycleConfigBase):
    pass


class MesocycleConfigUpdate(BaseModel):
    current_week: int | None = Field(default=None, ge=1)
    phase: str | None = Field(default=None, pattern=r"^(build|peak|deload)$")
    progression_rules: str | None = None


class MesocycleConfigRead(MesocycleConfigBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
