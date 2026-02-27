from datetime import datetime

from pydantic import BaseModel, Field


class RoutineExerciseBase(BaseModel):
    exercise_name: str = Field(max_length=200)
    sets: int = Field(gt=0)
    rep_range: str = Field(max_length=20)  # e.g. "8-10"
    order_index: int = Field(ge=0, default=0)
    notes: str | None = None
    superset_group: int | None = None


class RoutineDayBase(BaseModel):
    name: str = Field(max_length=100)  # e.g. "Push Day"
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    order_index: int = Field(ge=0, default=0)
    exercises: list[RoutineExerciseBase] = Field(min_length=1)


class WorkoutRoutineCreate(BaseModel):
    name: str = Field(max_length=100)
    days: list[RoutineDayBase] = Field(min_length=1)


class RoutineExerciseRead(RoutineExerciseBase):
    id: int

    model_config = {"from_attributes": True}


class RoutineDayRead(BaseModel):
    id: int
    name: str
    day_of_week: int | None
    order_index: int
    exercises: list[RoutineExerciseRead]

    model_config = {"from_attributes": True}


class WorkoutRoutineRead(BaseModel):
    id: int
    user_id: int
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    days: list[RoutineDayRead]

    model_config = {"from_attributes": True}
