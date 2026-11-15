from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from mycoach.health import GarminPushStatus


class BodyMeasurementCreate(BaseModel):
    """One Body page entry. ``measured_at`` is local wall time (the form's datetime-local),
    or aware; omitted means now."""

    measured_at: datetime | None = None
    weight_kg: float | None = Field(default=None, gt=20, lt=400)
    waist_cm: float | None = Field(default=None, gt=30, lt=250)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _weight_or_waist(self) -> "BodyMeasurementCreate":
        if self.weight_kg is None and self.waist_cm is None:
            raise ValueError("Enter a weight or a waist (or both)")
        return self


class BodyMeasurementRead(BaseModel):
    id: int
    measured_at: datetime
    """UTC."""
    weight_kg: float | None
    waist_cm: float | None
    note: str | None
    source: str
    garmin_push_status: GarminPushStatus | None = None
    """None for a waist-only reading (never pushed)."""


class HeightUpdate(BaseModel):
    height_cm: float = Field(gt=100, lt=250)


class HeightRead(BaseModel):
    height_cm: float | None
