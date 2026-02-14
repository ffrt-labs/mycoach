from datetime import datetime

from pydantic import BaseModel, Field


class GymWorkoutDetailBase(BaseModel):
    exercise_title: str = Field(max_length=200)
    superset_id: int | None = None
    exercise_notes: str | None = None
    set_index: int
    set_type: str = Field(default="normal", pattern=r"^(normal|warmup|dropset|failure)$")
    weight_kg: float | None = None
    reps: int | None = None
    distance_meters: float | None = None
    duration_seconds: int | None = None
    rpe: float | None = Field(default=None, ge=1, le=10)


class GymWorkoutDetailCreate(GymWorkoutDetailBase):
    pass


class GymWorkoutDetailRead(GymWorkoutDetailBase):
    id: int
    activity_id: int

    model_config = {"from_attributes": True}


class ActivityBase(BaseModel):
    sport: str = Field(max_length=50)
    title: str = Field(max_length=200)
    start_time: datetime
    end_time: datetime | None = None
    duration_minutes: int | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    calories: int | None = None
    hr_zones: str | None = None
    training_effect_aerobic: float | None = None
    training_effect_anaerobic: float | None = None
    data_source: str = Field(max_length=20)
    garmin_activity_id: str | None = None
    notes: str | None = None


class ActivityCreate(ActivityBase):
    gym_details: list[GymWorkoutDetailCreate] | None = None


class ActivityRead(ActivityBase):
    id: int
    user_id: int
    created_at: datetime
    gym_details: list[GymWorkoutDetailRead] | None = None

    model_config = {"from_attributes": True}
