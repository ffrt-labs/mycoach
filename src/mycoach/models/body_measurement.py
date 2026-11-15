from datetime import datetime

from sqlalchemy import CheckConstraint, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import HEALTH_SCHEMA, Base


class BodyMeasurement(Base):
    """A weight and/or waist reading the athlete entered (ticket #198).

    MyCoach owns body data: the Body page is the only input, so a wearable change never
    loses it (docs/research/health-metrics.md §4). Garmin gets a one-way copy of the
    weight (#199); its weigh-ins are never imported back. ``measured_at`` is UTC.
    """

    __tablename__ = "body_measurements"
    __table_args__ = (
        CheckConstraint(
            "weight_kg IS NOT NULL OR waist_cm IS NOT NULL",
            name="ck_body_measurements_weight_or_waist",
        ),
        Index("ix_body_measurements_athlete_measured_at", "athlete_id", "measured_at"),
        {"schema": HEALTH_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int]
    measured_at: Mapped[datetime]
    weight_kg: Mapped[float | None] = mapped_column(Float, default=None)
    waist_cm: Mapped[float | None] = mapped_column(Float, default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
