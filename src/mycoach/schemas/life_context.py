from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mycoach.models.life_context import Equipment, IllnessSeverity


class IllnessCreate(BaseModel):
    """The dashboard's "Feeling unwell" check-in."""

    severity: IllnessSeverity
    note: str | None = Field(default=None, max_length=500)


class TravelCreate(BaseModel):
    """The dashboard's "Travelling" form (#221). A trip may be entered in advance.

    ``started_on`` defaults to today; ``ended_on`` is the expected last travel day,
    optional. ``tz_shift_hours`` is the destination's clock minus home's.
    """

    started_on: date | None = None
    ended_on: date | None = None
    equipment: Equipment
    tz_shift_hours: float | None = Field(default=None, ge=-14, le=14)
    no_training: bool = False
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _returns_after_leaving(self) -> "TravelCreate":
        if (
            self.started_on is not None
            and self.ended_on is not None
            and self.ended_on < self.started_on
        ):
            raise ValueError("A trip cannot end before it starts")
        return self


class EpisodeUpdate(BaseModel):
    """Edit an episode while it is open; omitted fields stay as they are.

    ``severity`` is illness only; ``equipment``, ``tz_shift_hours``, ``no_training``
    and ``ended_on`` (the expected return) are travel only.
    """

    severity: IllnessSeverity | None = None
    note: str | None = Field(default=None, max_length=500)
    equipment: Equipment | None = None
    tz_shift_hours: float | None = Field(default=None, ge=-14, le=14)
    no_training: bool | None = None
    ended_on: date | None = None


class EpisodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    started_on: date
    ended_on: date | None
    note: str | None
    severity: str | None
    equipment: str | None = None
    tz_shift_hours: float | None = None
    no_training: bool | None = None
