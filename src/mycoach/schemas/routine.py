from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from mycoach.exercise_catalogue import is_known_exercise_id


class RoutineExerciseBase(BaseModel):
    exercise_id: str | None = Field(default=None, max_length=200)
    exercise_name: str = Field(max_length=200)
    # More than ten working sets of one exercise is a typo, not a prescription (#187).
    sets: int = Field(gt=0, le=10)
    rep_range: str = Field(max_length=20)  # e.g. "8-10"
    order_index: int = Field(ge=0, default=0)
    notes: str | None = None
    superset_group: int | None = None

    @field_validator("exercise_id")
    @classmethod
    def validate_exercise_id(cls, value: str | None) -> str | None:
        if value is not None and not is_known_exercise_id(value):
            raise ValueError("unknown exercise_id")
        return value


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
    # Unbounded on read: rows saved before the bound must still load so they can be fixed.
    sets: int

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
