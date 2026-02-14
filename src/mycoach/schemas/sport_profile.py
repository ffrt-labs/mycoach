from datetime import datetime

from pydantic import BaseModel, Field


class SportProfileBase(BaseModel):
    sport: str = Field(max_length=50)
    skill_level: str = Field(
        default="intermediate", pattern=r"^(beginner|intermediate|advanced)$"
    )
    goals: str | None = None
    preferences: str | None = None
    benchmarks: str | None = None


class SportProfileCreate(SportProfileBase):
    pass


class SportProfileUpdate(BaseModel):
    skill_level: str | None = Field(
        default=None, pattern=r"^(beginner|intermediate|advanced)$"
    )
    goals: str | None = None
    preferences: str | None = None
    benchmarks: str | None = None


class SportProfileRead(SportProfileBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
